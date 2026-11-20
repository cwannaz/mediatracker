"""Reading one article again, because a reader asked.

A thread keeps moving after a scan stops watching it. Votes are the clearest
case: hundreds can land on comments we already hold without the comment count
moving at all, which is exactly what a rescan's shortcut tests. So this path
must not take that shortcut, and it must stay inside the politeness the crawl
keeps.
"""
import asyncio

import pytest

from mediatracker import fetch
from mediatracker.config import load_config
from mediatracker.pipeline import Pipeline
from mediatracker.scanner import ScanEngine


def _engine(conn=None, fetcher=None):
    return ScanEngine(cfg=load_config(), conn=conn, blobs=None, store=None,
                      fetcher=fetcher, health=None)


class _Conn:
    """A corpus that knows one article."""
    def __init__(self, row=("lematin", "https://www.lematin.ch/a", None)):
        self.row = row

    def cursor(self):
        row = self.row
        class _Cur:
            description = [("slug",), ("canonical_url",), ("gone_at",)]
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, sql, params=None): self.params = params
            def fetchall(self): return [row] if row else []
        return _Cur()


# --------------------------------------------------------------------------- #
# the pipeline: one article, thread always read
# --------------------------------------------------------------------------- #

def test_ingest_one_never_takes_the_comment_count_shortcut():
    """`_thread_unchanged` would skip the very case this exists to catch."""
    seen = {}

    async def fake(self, source, url, stats, *, is_rescan=False):
        seen["url"], seen["is_rescan"] = url, is_rescan
        stats.comment_snapshots = 3

    p = Pipeline(conn=None, blobs=None, store=None, fetcher=None)
    p._ingest_article = fake.__get__(p, Pipeline)
    out = asyncio.run(p.ingest_one(object(), "https://x/a"))

    assert seen["url"] == "https://x/a"
    assert seen["is_rescan"] is False        # the thread is read, not compared
    assert out.comment_snapshots == 3


# --------------------------------------------------------------------------- #
# the engine: what it refuses, and what it shares
# --------------------------------------------------------------------------- #

def test_a_hold_on_fetching_stops_a_re_absorb(monkeypatch):
    """The same hold that stops a manual scan: one click is still a request."""
    monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "2099-01-01 00:00")
    with pytest.raises(fetch.Paused):
        asyncio.run(_engine(conn=_Conn()).reabsorb("a1"))


def test_an_unknown_article_is_refused_before_anything_goes_out():
    with pytest.raises(ValueError, match="unknown article"):
        asyncio.run(_engine(conn=_Conn(row=None)).reabsorb("nope"))


def test_an_article_of_a_journal_we_no_longer_read_is_refused():
    conn = _Conn(row=("gazette-de-nulle-part", "https://x/a", None))
    with pytest.raises(ValueError, match="unknown journal"):
        asyncio.run(_engine(conn=conn).reabsorb("a1"))


def test_without_postgres_it_says_so_rather_than_crashing():
    with pytest.raises(ValueError, match="Postgres"):
        asyncio.run(_engine(conn=None).reabsorb("a1"))


def test_it_runs_on_the_engine_fetcher_so_politeness_is_shared(monkeypatch):
    """A second fetcher would be a second tap on the same site.

    The per-host delay lives on the Fetcher instance, so a re-absorb that
    built its own would ignore a scan's pacing and hit the title twice at
    once. It must be handed the engine's.
    """
    built, sentinel = {}, object()

    class _P:
        def __init__(self, **kw): built.update(kw)
        async def ingest_one(self, source, url):
            class _S:
                articles_seen = 1
                article_snapshots = 1
                comments_seen = 7
                comment_snapshots = 7
                images_new = 0
                avatars_new = 0
                errors = 0
            return _S()

    monkeypatch.setattr("mediatracker.scanner.Pipeline", _P)
    eng = _engine(conn=_Conn(), fetcher=sentinel)
    monkeypatch.setattr(eng, "source_instance", lambda slug: object())
    out = asyncio.run(eng.reabsorb("a1"))

    assert built["fetcher"] is sentinel
    assert out["journal"] == "lematin"
    assert out["comment_snapshots"] == 7
    assert out["gone"] is False


def test_an_article_the_title_has_taken_down_is_reported_as_gone(monkeypatch):
    """Nothing parsed, so nothing was seen: say that, not "no changes"."""
    class _P:
        def __init__(self, **kw): pass
        async def ingest_one(self, source, url):
            class _S:
                articles_seen = 0
                article_snapshots = 0
                comments_seen = 0
                comment_snapshots = 0
                images_new = 0
                avatars_new = 0
                errors = 0
            return _S()

    monkeypatch.setattr("mediatracker.scanner.Pipeline", _P)
    eng = _engine(conn=_Conn(), fetcher=object())
    monkeypatch.setattr(eng, "source_instance", lambda slug: object())
    assert asyncio.run(eng.reabsorb("a1"))["gone"] is True
