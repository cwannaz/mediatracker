"""The dated hold on fetching: nothing goes out, and it lifts itself."""
import asyncio
import time

import pytest

from mediatracker import fetch, scanner


def _engine(**kw):
    """A ScanEngine with nothing but its queue -- enough to test the gate."""
    eng = object.__new__(scanner.ScanEngine)
    eng.conn = None
    eng.queue = asyncio.Queue()
    eng.cfg = kw.get("cfg")
    return eng


# -- the hold itself ------------------------------------------------------- #

def test_a_future_date_is_a_hold_and_a_past_one_is_not(monkeypatch):
    monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "2099-01-01 00:00")
    assert fetch.paused_until() > time.time()
    monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "2020-01-01 00:00")
    assert fetch.paused_until() is None, "a hold that has passed is no hold"
    monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "")
    assert fetch.paused_until() is None and fetch.hold_reason() is None


def test_the_reason_names_the_date_and_how_to_change_it(monkeypatch):
    monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "2099-01-01 08:00")
    reason = fetch.hold_reason("the lematin crawl")
    assert "the lematin crawl is on hold until" in reason
    assert "01 Jan 08:00" in reason and "MT_FETCH_PAUSED_UNTIL" in reason


def test_the_shipped_default_is_the_week_cedric_asked_for():
    # The hold lives in the code, so it survives a restart and lifts itself.
    assert fetch.PAUSED_UNTIL == "2026-10-04 08:00"


# -- the crawl ------------------------------------------------------------- #

def test_a_manual_scan_is_refused_while_the_hold_is_on(monkeypatch):
    monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "2099-01-01 00:00")
    with pytest.raises(fetch.Paused, match="a manual scan of lematin is on hold"):
        _engine().enqueue("lematin", "manual")


def test_a_scan_is_queued_once_the_hold_has_passed(monkeypatch):
    monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "2020-01-01 00:00")
    eng = _engine()
    assert eng.enqueue("lematin", "manual") is None      # None: no database
    assert eng.queue.qsize() == 1


def test_an_unknown_journal_is_still_refused_before_the_hold_is_read(monkeypatch):
    monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "2099-01-01 00:00")
    with pytest.raises(ValueError, match="unknown journal"):
        _engine().enqueue("leparisien", "manual")


def test_the_scheduler_skips_its_slot_and_queues_nothing(monkeypatch):
    """The slot is skipped, not postponed: the next one is hours away anyway."""
    monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "2099-01-01 00:00")
    eng = _engine()
    eng.cfg = type("C", (), {"startup_stagger_seconds": 0})()
    slept, rounds = [], {"n": 0}

    async def sleep(s):
        slept.append(s)
        rounds["n"] += 1
        if rounds["n"] > 3:
            raise asyncio.CancelledError
    monkeypatch.setattr(scanner.asyncio, "sleep", sleep)
    monkeypatch.setattr(eng, "schedule_of", lambda slug: pytest.fail(
        "the schedule must not even be read while fetching is held"))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(eng._schedule_loop("lematin", 0))
    assert eng.queue.qsize() == 0
    assert slept[1:] == [300, 300, 300], "it re-checks rather than sleeping a week"


def test_the_scheduler_resumes_by_itself_when_the_hold_passes(monkeypatch):
    monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "2099-01-01 00:00")
    eng = _engine()
    eng.cfg = type("C", (), {"startup_stagger_seconds": 0})()
    queued = []
    monkeypatch.setattr(eng, "schedule_of", lambda slug: {"enabled": True})
    monkeypatch.setattr(eng, "_next_fire", lambda s, **k: scanner.datetime.now(
        scanner._UTC))
    monkeypatch.setattr(eng, "enqueue", lambda slug, trig: queued.append(trig))

    async def sleep(s):
        # Between the first check and the second, the hold expires.
        monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "2020-01-01 00:00")
        if queued:
            raise asyncio.CancelledError
    monkeypatch.setattr(scanner.asyncio, "sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(eng._schedule_loop("lematin", 0))
    assert queued == ["scheduled"], "no restart needed for the hold to lift"


# -- the backfill legs ----------------------------------------------------- #

def test_a_supervised_leg_waits_instead_of_exiting(monkeypatch):
    """Exiting quickly would have supervisor4.sh retire the leg for good."""
    monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "2099-01-01 00:00")
    waited = []
    assert fetch.wait_out_hold(cap_s=1200, sleep=waited.append) is True
    assert sum(waited) >= 1200, "it waits out its whole cap before giving up"
    assert max(waited) <= 300, "in steps, so a lifted hold is noticed"


def test_the_wait_ends_the_moment_the_hold_does(monkeypatch):
    monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "2099-01-01 00:00")
    steps = []

    def sleep(s):
        steps.append(s)
        monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "")
    assert fetch.wait_out_hold(cap_s=9999, sleep=sleep) is False
    assert len(steps) == 1


def test_with_no_hold_nothing_waits_at_all(monkeypatch):
    monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "")
    assert fetch.wait_out_hold(sleep=lambda s: pytest.fail("slept")) is False


def test_the_by_hand_body_backfill_refuses_and_says_why(monkeypatch, capsys):
    from mediatracker import body_backfill

    monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "2099-01-01 00:00")
    monkeypatch.setattr(body_backfill, "WaybackClient",
                        lambda **kw: pytest.fail("asked the archive anyway"))
    from mediatracker import db
    monkeypatch.setattr(db, "connect", lambda cfg: object())
    # 0, not an error: it also runs as a unit, and a hold is not a failure.
    assert body_backfill.main([]) == 0
    assert "on hold until" in capsys.readouterr().out
