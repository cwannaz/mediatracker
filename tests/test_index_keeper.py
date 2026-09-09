"""The daemon must keep the search index current on its own.

Nothing called search.refresh() for three days once; the index answered every
query from a corpus that had grown by 778,000 comments in the meantime. A
search box that is confidently stale is worse than one that is down, so these
pin the loop that prevents it.

No pytest-asyncio here (stdlib only), so each test drives the coroutine with
asyncio.run and stops it by making the sleep raise once it has ticked enough.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from mediatracker import server


def run_ticks(monkeypatch, *, refresh, connect, ticks):
    """Run _index_keeper for `ticks` passes and return, without real waiting."""
    s = server.Server.__new__(server.Server)
    s.cfg = object()
    monkeypatch.setattr(server.db, "connect", connect)
    monkeypatch.setattr(server.search, "refresh", refresh)

    seen = 0
    real_sleep = asyncio.sleep

    async def fake_sleep(_seconds):
        nonlocal seen
        seen += 1
        if seen > ticks:
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)

    async def drive():
        with pytest.raises(asyncio.CancelledError):
            await s._index_keeper()

    asyncio.run(drive())


def test_the_keeper_refreshes_the_index(monkeypatch):
    calls = []

    def refresh(conn, **kw):
        calls.append(kw)
        return {"article": {"docs": 3}}

    run_ticks(monkeypatch, refresh=refresh, connect=lambda cfg: object(), ticks=2)
    assert len(calls) == 2, "the keeper must refresh on every tick, not once"


def test_a_capped_pass_is_requested(monkeypatch):
    """One pass must not run unbounded: it commits batch after batch, and the
    web app is answered on the same loop."""
    calls = []

    def refresh(conn, **kw):
        calls.append(kw)
        return {}

    run_ticks(monkeypatch, refresh=refresh, connect=lambda cfg: object(), ticks=1)
    assert calls[0]["max_batches"] == server.INDEX_MAX_BATCHES


def test_a_failing_refresh_does_not_stop_the_keeper(monkeypatch):
    """A dead index must not take the daemon down with it."""
    n = 0

    def boom(conn, **kw):
        nonlocal n
        n += 1
        raise RuntimeError("index on fire")

    run_ticks(monkeypatch, refresh=boom, connect=lambda cfg: object(), ticks=3)
    assert n == 3, "the keeper must keep trying after a failure"


def test_a_failure_drops_the_connection_rather_than_reusing_it(monkeypatch):
    """psycopg leaves a connection unusable after an error; reusing it would
    turn one bad pass into a permanently broken keeper."""
    opened = []

    def connect(cfg):
        opened.append(object())
        return opened[-1]

    def boom(conn, **kw):
        raise RuntimeError("nope")

    run_ticks(monkeypatch, refresh=boom, connect=connect, ticks=3)
    assert len(opened) == 3, "each failed pass must reconnect"


def test_a_healthy_keeper_reuses_one_connection(monkeypatch):
    opened = []

    def connect(cfg):
        opened.append(object())
        return opened[-1]

    run_ticks(monkeypatch, refresh=lambda conn, **kw: {}, connect=connect, ticks=4)
    assert len(opened) == 1, "a working keeper must not open a connection per tick"


def test_no_database_is_survived(monkeypatch):
    run_ticks(monkeypatch, refresh=lambda conn, **kw: {},
              connect=lambda cfg: None, ticks=2)   # must not raise


def test_the_daemon_starts_the_keeper():
    """The loop is useless if run() never creates the task."""
    assert "_index_keeper" in inspect.getsource(server.Server.run)
