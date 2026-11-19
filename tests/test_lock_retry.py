"""Losing a lock race is not a reason to lose a night's work.

At every boot systemd starts the body backfill and the daemon in the same
second. The backfill reads article_snapshot and then joins journal; the
daemon's ensure_schema takes journal and then article_snapshot. Postgres sees
the cycle, kills one of them, and it is always the backfill -- which has
Restart=no, so the stint is simply skipped. It had never once run from a cold
boot before this.
"""
import time

import pytest

from mediatracker import db

psycopg = pytest.importorskip("psycopg")


def _deadlock():
    return psycopg.errors.DeadlockDetected("deadlock detected")


# --------------------------------------------------------------------------- #
# the retry itself
# --------------------------------------------------------------------------- #

def test_a_read_that_works_is_not_retried_or_delayed():
    calls = []
    t0 = time.time()
    out = db.retrying_on_lock(lambda: calls.append(1) or "rows")
    assert out == "rows" and len(calls) == 1
    assert time.time() - t0 < 0.1          # no sleep on the happy path


def test_the_victim_asks_again_and_gets_its_answer():
    """Postgres kills one side; the survivor finishes and the tables free up."""
    tries = []

    def flaky():
        tries.append(1)
        if len(tries) < 3:
            raise _deadlock()
        return "rows"

    out = db.retrying_on_lock(flaky, what="the backfill queue", first_wait=0.001)
    assert out == "rows"
    assert len(tries) == 3


def test_a_lock_it_could_not_take_is_retried_too():
    """lock_timeout raises LockNotAvailable, not DeadlockDetected."""
    tries = []

    def flaky():
        tries.append(1)
        if len(tries) < 2:
            raise psycopg.errors.LockNotAvailable("canceling statement")
        return "rows"

    assert db.retrying_on_lock(flaky, first_wait=0.001) == "rows"


def test_a_standoff_that_never_clears_still_ends_the_run():
    """A hang would be worse than a failure: the unit would never exit."""
    tries = []

    def stuck():
        tries.append(1)
        raise _deadlock()

    with pytest.raises(psycopg.errors.DeadlockDetected):
        db.retrying_on_lock(stuck, attempts=3, first_wait=0.001)
    assert len(tries) == 3


def test_any_other_database_error_is_raised_at_once():
    """A missing column is a fault in the query; asking again cannot fix it."""
    tries = []

    def broken():
        tries.append(1)
        raise psycopg.errors.UndefinedColumn("column does not exist")

    with pytest.raises(psycopg.errors.UndefinedColumn):
        db.retrying_on_lock(broken, first_wait=0.001)
    assert len(tries) == 1


def test_the_waits_back_off_rather_than_hammering_the_lock():
    waits = []

    def flaky():
        raise _deadlock()

    import mediatracker.db as dbmod
    real_sleep = dbmod.time.sleep
    dbmod.time.sleep = waits.append
    try:
        with pytest.raises(psycopg.errors.DeadlockDetected):
            db.retrying_on_lock(flaky, attempts=4, first_wait=0.5)
    finally:
        dbmod.time.sleep = real_sleep
    assert waits == [0.5, 1.0, 2.0]        # three waits for four attempts


def test_the_default_budget_outlasts_ensure_schema():
    """The race it exists for lasts about 2.6s; the waits must cover it."""
    total = sum(db.LOCK_FIRST_WAIT * 2 ** i for i in range(db.LOCK_ATTEMPTS - 1))
    assert total > 10


# --------------------------------------------------------------------------- #
# the backfill actually using it
# --------------------------------------------------------------------------- #

def test_the_backfill_survives_a_deadlock_on_its_opening_reads(monkeypatch):
    """Both reads, not just the one that happened to lose on 19 November."""
    from mediatracker import body_backfill as bf

    hits = {"candidates": 0, "control": 0}

    def candidates(conn, **kw):
        hits["candidates"] += 1
        if hits["candidates"] == 1:
            raise _deadlock()
        return []

    def control(conn, *, journal=None):
        hits["control"] += 1
        if hits["control"] == 1:
            raise _deadlock()
        return None

    monkeypatch.setattr(bf, "candidates", candidates)
    monkeypatch.setattr(bf, "control_capture", control)
    # `time` is one module object, so this is db's sleep as well as the
    # backfill's: the retry waits are what would otherwise make this slow.
    monkeypatch.setattr(bf.time, "sleep", lambda s: None)

    out = bf.run(None, client=object())
    assert hits == {"candidates": 2, "control": 2}
    assert out["seen"] == 0


def test_the_constants_are_the_setting(monkeypatch):
    """Patching the module constant must change behaviour, not be ignored."""
    tries = []

    def stuck():
        tries.append(1)
        raise _deadlock()

    monkeypatch.setattr(db, "LOCK_ATTEMPTS", 2)
    monkeypatch.setattr(db, "LOCK_FIRST_WAIT", 0.001)
    with pytest.raises(psycopg.errors.DeadlockDetected):
        db.retrying_on_lock(stuck)
    assert len(tries) == 2
