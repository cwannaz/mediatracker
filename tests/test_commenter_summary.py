"""The commenter browser reads precomputed totals, a page at a time.

Aggregating 4.2M comments per request took 8 s for every page of 500, and the
anagram index another 3 s, all on the event loop the web app is answered on.
These pin the pieces that took that cost off the request path.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from mediatracker import anagrams, db, handles, nicknames, server


class _Cur:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0
        self.description = [("nick",)]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.sql.append(sql)

    def fetchone(self):
        return (self.conn.built,)

    def fetchall(self):
        return []


class _Conn:
    def __init__(self, built):
        self.built, self.sql, self.committed = built, [], False

    def cursor(self):
        return _Cur(self)

    def commit(self):
        self.committed = True


def test_a_page_is_read_from_the_summary_once_it_exists():
    conn = _Conn(built=True)
    db.browse_commenters(conn, limit=500, offset=500)
    page = conn.sql[-1]
    assert "FROM commenter_summary s" in page
    assert "GROUP BY" not in page, "a built summary must not be re-aggregated per page"
    # A tie-break, or rows with equal counts can swap across a page seam.
    assert "ORDER BY s.comments DESC, s.nick" in page


def test_before_the_first_build_the_page_is_aggregated_live():
    # An empty browser on a fresh database would read as an empty corpus.
    conn = _Conn(built=False)
    db.browse_commenters(conn, limit=500)
    page = conn.sql[-1]
    assert "GROUP BY c.author_nick" in page and "commenter_summary s" not in page


def test_a_refresh_writes_only_rows_whose_totals_moved():
    conn = _Conn(built=True)
    db.refresh_commenter_summary(conn)
    upsert = conn.sql[0]
    assert "ON CONFLICT (nick) DO UPDATE" in upsert and "IS DISTINCT FROM" in upsert
    assert "s.communities" in upsert, "a nickname moving community must reach the summary"
    assert any("DELETE FROM commenter_summary" in s for s in conn.sql)


def test_the_anagram_index_reads_its_handles_from_the_summary():
    conn = _Conn(built=True)
    anagrams.load(conn)
    read = conn.sql[-1]
    assert "unnest(communities)" in read and "FROM commenter_summary" in read
    assert not any("GROUP BY" in s for s in conn.sql), "4.2M comments re-read for a handle list"


@pytest.mark.parametrize("built, min_comments", [(False, 1), (True, 3)])
def test_the_live_read_remains_where_the_summary_cannot_answer(built, min_comments):
    # Not built yet, or a per-community threshold the summary keeps no counts for.
    conn = _Conn(built=built)
    anagrams.load(conn, min_comments=min_comments)
    assert "GROUP BY 1, 2" in conn.sql[-1]


@pytest.mark.parametrize("built", [True, False])
def test_the_handles_are_read_in_a_fixed_order(built):
    # `find` keeps one handle per letter sequence, so an unordered read made
    # 'À venir' and 'A. Venir' swap places from one build to the next.
    conn = _Conn(built=built)
    anagrams.load(conn)
    assert "ORDER BY 1, 2" in conn.sql[-1]


# --------------------------------------------------------------------------- #
# the keeper
# --------------------------------------------------------------------------- #

def _run_keeper(monkeypatch, rebuild, ticks):
    s = server.Server.__new__(server.Server)
    s.cfg, s.anagram_index, s._anagram_sig = object(), None, None
    monkeypatch.setattr(server.db, "connect", lambda cfg: object())
    monkeypatch.setattr(server, "_rebuild_browser_caches", rebuild)
    seen = 0
    real_sleep = asyncio.sleep

    async def fake_sleep(_seconds):
        nonlocal seen
        seen += 1
        if seen >= ticks:
            raise asyncio.CancelledError
        await real_sleep(0)
    monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)

    async def drive():
        with pytest.raises(asyncio.CancelledError):
            await s._summary_keeper()
    asyncio.run(drive())
    return s


def test_the_keeper_builds_at_start_not_a_quarter_hour_later(monkeypatch):
    calls = []

    def rebuild(conn, known):
        calls.append(conn)
        return {"changed": 1, "removed": 0}, "sig1", {"abc": {}}
    s = _run_keeper(monkeypatch, rebuild, ticks=1)
    assert len(calls) == 1, "the first pass must come before the first sleep"
    assert s.anagram_index == {"abc": {}}


def test_the_keeper_refreshes_on_every_tick(monkeypatch):
    calls = []
    _run_keeper(monkeypatch,
                lambda conn, known: calls.append(1) or ({"changed": 0, "removed": 0}, "s", {}),
                ticks=3)
    assert len(calls) == 3


def test_a_failing_refresh_does_not_stop_the_keeper(monkeypatch):
    n = 0

    def boom(conn, known):
        nonlocal n
        n += 1
        raise RuntimeError("summary on fire")
    _run_keeper(monkeypatch, boom, ticks=3)
    assert n == 3


def test_the_index_is_kept_while_the_handles_are_unchanged(monkeypatch):
    seen = []

    def rebuild(conn, known):
        seen.append(known)
        index = {"built": {}} if known != "sig1" else None
        return {"changed": 40, "removed": 0}, "sig1", index
    s = _run_keeper(monkeypatch, rebuild, ticks=3)
    assert seen == [None, "sig1", "sig1"], "each pass must be told what the index was built from"
    assert s.anagram_index == {"built": {}}, "an unchanged pass must not drop the index"


def test_a_pass_loads_the_index_only_when_the_handle_set_moved(monkeypatch):
    loads = []
    monkeypatch.setattr(server.db, "refresh_commenter_summary",
                        lambda conn: {"changed": 5, "removed": 0})
    monkeypatch.setattr(server.db, "commenter_handles_signature", lambda conn: "now")
    monkeypatch.setattr(server.anagrams, "load", lambda conn: loads.append(1) or {"x": {}})
    assert server._rebuild_browser_caches(object(), "now")[2] is None
    assert server._rebuild_browser_caches(object(), "before")[2] == {"x": {}}
    assert loads == [1], "totals moving is not a reason to rebuild the index"


# --------------------------------------------------------------------------- #
# the request
# --------------------------------------------------------------------------- #

def _browse(monkeypatch, msg, *, index):
    s = server.Server.__new__(server.Server)
    s.conn, s.anagram_index = object(), index
    loads = []
    monkeypatch.setattr(db, "browse_commenters", lambda conn, **kw: [{"nick": "202"}])
    monkeypatch.setattr(db, "count_commenters", lambda conn, **kw: 182_628)
    monkeypatch.setattr(db, "note_counts", lambda conn: {})
    monkeypatch.setattr(anagrams, "load", lambda conn: loads.append(1) or {})
    monkeypatch.setattr(anagrams, "annotate", lambda rows, index: rows)
    monkeypatch.setattr(handles, "annotate", lambda rows: rows)
    monkeypatch.setattr(nicknames, "annotate", lambda rows: rows)
    return json.loads(s._browse("browse_commenters", msg)), loads


def test_a_page_uses_the_kept_anagram_index(monkeypatch):
    _, loads = _browse(monkeypatch, {"cmd": "browse_commenters", "limit": 500}, index={"x": {}})
    assert loads == [], "3 s per page: the index must come from the keeper"


def test_a_request_before_the_keeper_lands_loads_the_index_once(monkeypatch):
    _, loads = _browse(monkeypatch, {"cmd": "browse_commenters", "limit": 500}, index=None)
    assert loads == [1]


def test_the_total_comes_with_the_first_page_only(monkeypatch):
    first, _ = _browse(monkeypatch, {"cmd": "browse_commenters", "limit": 500}, index={})
    later, _ = _browse(monkeypatch, {"cmd": "browse_commenters", "limit": 500,
                                     "offset": 500}, index={})
    assert first["total"] == 182_628
    assert "total" not in later
