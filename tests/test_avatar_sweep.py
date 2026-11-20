"""Recovering an avatar history that was never stored, politely and resumably.

Two measured facts shape this and both are worth guarding. The comment API
answers only for modern content ids -- every id from 2008 to 2016 returns
HTTP 500 -- so there is a floor at November 2020 and no crawl reaches past it.
And what is wanted is nicknames rather than threads, so the queue is ordered by
first-sight yield: all 90,331 reachable nicknames in 10,190 threads instead of
47,463.
"""
import asyncio

import pytest

from mediatracker import avatar_sweep as sw


# --------------------------------------------------------------------------- #
# the plan: greedy set cover
# --------------------------------------------------------------------------- #

class _Cur:
    def __init__(self, conn): self.conn = conn
    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.conn.log.append((s, params))
        self._last = s

    def fetchone(self):
        return (0,)

    def fetchall(self):
        return self.conn.pairs if "author_nick" in self._last else []


class _Conn:
    def __init__(self, pairs=()):
        self.pairs, self.log = list(pairs), []
    def cursor(self): return _Cur(self)


def _planned(conn):
    """(article_id, rank, new_nicks) actually inserted, in order."""
    return [(p[0], p[2], p[3]) for s, p in conn.log
            if p and s.startswith("INSERT INTO avatar_sweep")]


def test_the_thread_showing_most_nicknames_first_is_ranked_first():
    conn = _Conn([("a", "n1"), ("a", "n2"), ("a", "n3"),
                  ("b", "n4"), ("b", "n5"),
                  ("c", "n6")])
    out = sw.plan(conn)
    assert out["planned"] == 3
    assert [r[0] for r in _planned(conn)] == ["a", "b", "c"]
    assert [r[2] for r in _planned(conn)] == [3, 2, 1]


def test_a_thread_whose_authors_are_all_seen_elsewhere_is_not_queued():
    """The whole saving: 37,000 threads that would teach us nothing."""
    conn = _Conn([("big", "n1"), ("big", "n2"), ("big", "n3"),
                  ("dup", "n1"), ("dup", "n2")])
    out = sw.plan(conn)
    assert out["planned"] == 1
    assert [r[0] for r in _planned(conn)] == ["big"]


def test_yield_is_recomputed_as_cover_grows_not_taken_from_the_first_estimate():
    """b looks worth 3 at the start and is worth 1 by its turn; c beats it."""
    conn = _Conn([("a", "n1"), ("a", "n2"), ("a", "n3"), ("a", "n4"),
                  ("b", "n1"), ("b", "n2"), ("b", "n5"),
                  ("c", "n6"), ("c", "n7")])
    sw.plan(conn)
    ranked = [(r[0], r[2]) for r in _planned(conn)]
    assert ranked == [("a", 4), ("c", 2), ("b", 1)]


def test_every_nickname_ends_up_covered_by_something():
    conn = _Conn([("a", "n1"), ("b", "n2"), ("c", "n3"), ("a", "n3")])
    out = sw.plan(conn)
    assert out["nicknames"] == 3


def test_nothing_reachable_is_said_rather_than_crashing():
    assert sw.plan(_Conn([]))["planned"] == 0


def test_the_queue_only_takes_ids_the_api_still_answers_for():
    """2008-2016 ids all return HTTP 500; a crawl over them is pure waste."""
    conn = _Conn([("a", "n1")])
    sw.plan(conn)
    select = next(s for s, _ in conn.log if "DISTINCT a.id" in s)
    assert sw.MODERN_ID in select
    assert "gone_at IS NULL" in select


def test_an_existing_plan_is_not_silently_replaced():
    class _Existing(_Conn):
        def cursor(self):
            cur = _Cur(self)
            cur.fetchone = lambda: (10_190,)
            return cur
    out = sw.plan(_Existing([("a", "n1")]))
    assert out["planned"] == 0 and "rebuild" in out["note"]


# --------------------------------------------------------------------------- #
# the run: politeness, budget, and surviving one bad thread
# --------------------------------------------------------------------------- #

class _Stats:
    def __init__(self, c=5, s=5, a=2):
        self.comments_seen, self.comment_snapshots, self.avatars_new = c, s, a


class _RunConn:
    """Serves a queue of rows, then records how each was finished."""
    def __init__(self, n=3):
        self.rows = [{"article_id": f"a{i}", "source_key": f"{10**11 + i}",
                      "canonical_url": f"https://x/{i}", "rank": i,
                      "new_nicks": 9 - i} for i in range(n)]
        self.finished = []

    def cursor(conn_self):
        class C:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, sql, params=None):
                self.sql = " ".join(sql.split())
                if self.sql.startswith("UPDATE avatar_sweep"):
                    conn_self.finished.append((params[2], params[0]))
            def fetchall(self): return [tuple(r.values()) for r in conn_self.rows]
            description = [("article_id",), ("source_key",), ("canonical_url",),
                           ("rank",), ("new_nicks",)]
        return C()


def _run(conn, refresh, **kw):
    import mediatracker.avatar_sweep as mod

    class _P:
        def __init__(self, **k): pass
        async def refresh_thread(self, source, aid, key, url):
            return refresh(aid)

    old = mod.Pipeline
    mod.Pipeline = _P
    try:
        return asyncio.run(mod.run(conn, blobs=None, store=None, fetcher=None,
                                   **kw))
    finally:
        mod.Pipeline = old


def test_it_works_the_queue_in_rank_order_and_totals_what_it_found():
    conn = _RunConn(3)
    out = _run(conn, lambda aid: _Stats())
    assert out["threads"] == 3 and out["avatars_new"] == 6
    assert [a for a, _ in conn.finished] == ["a0", "a1", "a2"]
    assert {s for _, s in conn.finished} == {"done"}


def test_one_bad_thread_is_marked_and_stepped_over():
    """A sweep that stopped on each failure would never finish."""
    def refresh(aid):
        if aid == "a1":
            raise RuntimeError("bad json")
        return _Stats()

    conn = _RunConn(3)
    out = _run(conn, refresh)
    assert out["threads"] == 2 and out["failed"] == 1
    assert ("a1", "failed:RuntimeError") in conn.finished


def test_a_hold_placed_mid_sweep_stops_it_there():
    """The same hold that stops any other crawl; a queue is not an exemption."""
    from mediatracker import fetch

    def refresh(aid):
        if aid == "a1":
            raise fetch.Paused("held")
        return _Stats()

    conn = _RunConn(3)
    out = _run(conn, refresh)
    assert out["stopped"] == "hold"
    assert out["threads"] == 1
    # The held thread stays queued, so the next run picks it up again.
    assert [a for a, _ in conn.finished] == ["a0"]


def test_a_hold_already_in_place_stops_it_before_any_request(monkeypatch):
    from mediatracker import fetch
    monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "2099-01-01 00:00")
    with pytest.raises(fetch.Paused):
        _run(_RunConn(2), lambda aid: _Stats())


def test_the_budget_leaves_the_rest_queued_rather_than_failing_them():
    import mediatracker.avatar_sweep as mod
    conn = _RunConn(5)
    clock = iter([0, 0, 1e9, 1e9, 1e9, 1e9, 1e9])
    monkey = mod.time.time
    mod.time.time = lambda: next(clock)
    try:
        out = _run(conn, lambda aid: _Stats(), max_hours=1)
    finally:
        mod.time.time = monkey
    assert out["stopped"] == "budget"
    assert out["threads"] < 5
    assert len(conn.finished) == out["threads"]


def test_an_empty_queue_is_a_finished_sweep_not_an_error():
    conn = _RunConn(0)
    out = _run(conn, lambda aid: _Stats())
    assert out["queued"] == 0 and out["threads"] == 0


def test_an_unknown_journal_is_refused():
    with pytest.raises(ValueError, match="unknown journal"):
        _run(_RunConn(1), lambda aid: _Stats(), journal="le-canard-enchaine")


# --------------------------------------------------------------------------- #
# the thread-only refresh
# --------------------------------------------------------------------------- #

def test_the_refresh_does_not_fetch_the_article_page():
    """Skipping it halves 10,190 threads' worth of requests."""
    import inspect

    from mediatracker.pipeline import Pipeline
    src = inspect.getsource(Pipeline.refresh_thread)
    assert "fetch_article" not in src
    assert "_safe_comments" in src


def test_the_refresh_writes_the_comments_it_already_paid_to_fetch():
    import inspect

    from mediatracker.pipeline import Pipeline
    src = inspect.getsource(Pipeline.refresh_thread)
    assert "_write_comment" in src and "_write_avatars" in src
