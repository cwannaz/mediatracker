"""Recovering the years nobody scanned, from the Internet Archive.

Le Matin's corpus has a hole: live scanning began 2026-08-22 and everything
before it is a printed archive of selected threads. The hole is fillable
because the paper ran Tamedia's Newsnetz stack until ~2017 and that stack
rendered comment threads server-side, so the Wayback Machine holds them as
plain HTML. Measured over lematin.ch, distinct captures of a thread page:

    2014  11,533     2017      7
    2015  16,395     2018      7
    2016  11,725     2019    120

The cliff is the platform change, not the archive losing interest. After it,
the recoverable material is the article page — which still carries the thread
size and a two-comment preview — rather than the thread.

Everything written here is marked `origin='wayback'`. Archive capture is not
live capture: a snapshot is one moment a crawler happened to visit, so a thread
caught at noon is missing its afternoon, and absence in this material never
means absence in the world. Keeping the origin on the row is what lets every
later query say which kind of evidence it is standing on.

Runs are resumable. Each processed capture is recorded by its content digest,
so re-running skips what is already in and a night that dies halfway costs one
page rather than the run.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from . import archive_parse as ap
from . import db, ids, sources
from .config import load_config
from .wayback import GaveUp, WaybackClient

log = logging.getLogger(__name__)

DOMAINS = {"lematin": "lematin.ch", "24heures": "24heures.ch", "tdg": "tdg.ch"}

# What to ask the archive for, narrowest first. Each is one CDX filter, applied
# a single year at a time — the only shape of query the archive reliably serves.
KINDS: dict[str, dict] = {
    # The prize: server-rendered threads, whole.
    "threads": {
        "filter": r".*comments=1.*",
        "years": range(2006, 2018),
        "what": "Newsnetz thread pages (?comments=1)",
    },
    # Its successor pattern, barely crawled but free to try.
    "threads-html": {
        "filter": r".*comments\.html.*",
        "years": range(2016, 2022),
        "what": "comments.html?article= thread pages",
    },
    # Article pages: headline, byline, thread SIZE, and a two-comment preview
    # that the template renders even when the thread page was never captured.
    "articles": {
        "filter": r".*/story/[0-9]+.*",
        "years": range(2006, 2022),
        "what": "article pages (count + 2-comment preview)",
    },
}

_PRINT = re.compile(r"/print\.html$", re.I)


def article_url_of(snapshot_url: str) -> str:
    """The article a thread page belongs to.

    `?comments=1` and `/print.html` are two views of one article, and the
    archive holds both. Folding them to the article's own URL is what keeps a
    thread attached to the row the article page already created instead of
    minting a third.
    """
    parts = urlsplit(snapshot_url)
    query = "&".join(q for q in parts.query.split("&")
                     if q and not q.startswith(("comments=", "article=")))
    path = _PRINT.sub("", parts.path) or "/"
    # ".../print.html/25175132/print.html" — the archive holds some mangled
    # paths where a relative link was resolved twice.
    path = re.sub(r"(/\d{6,})(?:/\1)+", r"\1", path)
    return urlunsplit((parts.scheme or "http", parts.netloc, path, query, ""))


# "/story/?comments=1" with nothing after it is a section index, not an
# article — the archive holds thousands and they never carry a thread.
_HAS_ARTICLE_ID = re.compile(r"/story/\d+|/\d{6,}")
# Thumbnails live under /files/imagecache/<size>/story/<something>.jpg, and the
# digits in a filename like "090109_Faitdiv.jpg" satisfy the article-id pattern
# exactly as a story id does. Left unfiltered, an early-year article leg is
# entirely JPEGs: the 2009-2011 24 heures enumerations were 100% images.
_ASSET = re.compile(r"\.(?:jpe?g|png|gif|svg|webp|ico|css|js|pdf|xml|zip)$", re.I)


def worth_fetching(url: str) -> bool:
    path = urlsplit(url).path
    if _ASSET.search(path):
        return False
    return bool(_HAS_ARTICLE_ID.search(path))


def _rank(row: dict) -> tuple:
    """Which capture of one article to spend the request on.

    `print.html?comments=1` first: it is the whole thread on one page, where the
    story view paginates and gives back the first screen only — measured on one
    2014 Tribune thread, 29 comments against a count of 31. Then the latest,
    because a thread only grows.
    """
    return ("print.html" in row["original"], row.get("timestamp", ""))


def newest_per_article(rows: list[dict]) -> list[dict]:
    """One capture per article: the fullest view, latest taken.

    The archive holds `?comments=1` and `print.html?comments=1` of the same
    thread, often several times each, and they are all the same article. Every
    extra fetch buys nothing — the comment ids dedupe on write — so folding
    them here is the difference between a night that finishes a year and one
    that does not.
    """
    best: dict[str, dict] = {}
    for r in rows:
        if not worth_fetching(r["original"]):
            continue
        key = ids.canonical_url(article_url_of(r["original"]))
        prev = best.get(key)
        if prev is None or _rank(r) > _rank(prev):
            best[key] = r
    return sorted(best.values(), key=lambda r: r.get("timestamp", ""))


def cdx_cached(client: WaybackClient, domain: str, *, kind: str, year: int,
               cache_dir: Path | None = None) -> list[dict]:
    """Enumerate a leg, remembering the answer.

    A CDX query for one year takes the better part of a minute and returns the
    same thing every time. Caching it means a resumed run starts fetching
    immediately instead of spending half an hour re-listing what it already
    knows, and it takes that load off the archive too.
    """
    cache_dir = cache_dir or Path(load_config().data_dir) / "cdx"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{domain}.{kind}.{year}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except ValueError:
            log.warning("unreadable cdx cache %s; re-querying", path)
    rows = client.cdx(domain, year=year, url_filter=KINDS[kind]["filter"])
    path.write_text(json.dumps(rows))
    return rows


@dataclass
class Stats:
    captures: int = 0
    skipped: int = 0
    articles: int = 0
    snapshots: int = 0
    comments: int = 0
    comment_snapshots: int = 0
    empty: int = 0
    errors: int = 0
    dead: int = 0
    started: float = field(default_factory=time.monotonic)

    def as_dict(self) -> dict:
        return {**self.__dict__, "elapsed_s": round(time.monotonic() - self.started)}


# --------------------------------------------------------------------------- #

def ensure_schema(conn) -> None:
    """One extra table: what we have already pulled out of the archive."""
    with conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS archive_capture (
            digest       TEXT PRIMARY KEY,   -- the archive's own content digest
            journal_slug TEXT NOT NULL,
            timestamp    TEXT NOT NULL,      -- capture time, YYYYMMDDhhmmss
            original     TEXT NOT NULL,      -- the URL as captured
            kind         TEXT NOT NULL,
            comments     INTEGER,
            processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS archive_capture_journal_idx
            ON archive_capture (journal_slug, timestamp);

        -- A capture the CDX index lists but the replay engine will not serve.
        -- Recording the dead ones is what makes a resume cheap: without it the
        -- todo list keeps them forever, and every restart grinds through the
        -- same 404s before reaching anything fetchable.
        ALTER TABLE archive_capture ADD COLUMN IF NOT EXISTS ok BOOLEAN NOT NULL DEFAULT TRUE;
        """)


def already_done(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT digest FROM archive_capture")
        return {r[0] for r in cur.fetchall()}


def _record(conn, *, digest, slug, timestamp, original, kind, comments,
            ok: bool = True) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO archive_capture
                (digest, journal_slug, timestamp, original, kind, comments, ok)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (digest) DO NOTHING
        """, (digest, slug, timestamp, original, kind, comments, ok))


# --------------------------------------------------------------------------- #

def ingest(conn, *, slug: str, page: str, original: str, timestamp: str,
           stats: Stats) -> int:
    """Write one archived page's article and comments. Returns comments found."""
    # sources.get returns the CLASS; community_key is a property, so it has to
    # be instantiated or the shared-id path hashes a property object.
    cls = sources.get(slug)
    src = cls() if cls else None
    community = src.community_key if src else slug
    shared = bool(src and src.comment_ids_global)

    canonical = ids.canonical_url(article_url_of(original))
    aid = ids.article_id(slug, canonical)
    jid = ids.journal_id(slug)

    art = ap.parse_article(page, original)
    comments = ap.parse_comments(page)

    db.upsert_article(conn, aid=aid, journal_id=jid, canonical_url=canonical,
                      source_key=art.get("source_id"), origin="wayback")
    stats.articles += 1

    # The capture time is the only date the archive guarantees. A published_at
    # is left NULL rather than guessed from it: a 2016 crawl of a 2012 article
    # would otherwise date the article to the crawl.
    chash = ids.content_hash(art.get("headline") or "", art.get("body_text") or "",
                             str(art.get("comment_count") or ""))
    if db.insert_article_snapshot(conn, article_id=aid, content_hash=chash, fields={
            "headline": art.get("headline"), "subhead": art.get("subhead"),
            "author": art.get("author"), "body_text": art.get("body_text"),
            "comment_count": art.get("comment_count"),
            "raw_meta": {"ingested_by": "wayback", "capture": timestamp,
                         "captured_url": original}}):
        stats.snapshots += 1

    for c in comments:
        key = c["source_id"]
        cid = (ids.shared_comment_id(community, key) if shared
               else ids.comment_id(slug, aid, key))
        parent = None
        if c["parent_id"]:
            parent = (ids.shared_comment_id(community, c["parent_id"]) if shared
                      else ids.comment_id(slug, aid, c["parent_id"]))
        db.upsert_comment(conn, cid=cid, article_id=aid, source_key=key,
                          parent_id=parent, author_nick=c["author_nick"],
                          author_key=None)
        stats.comments += 1
        chash = ids.content_hash(c["body_text"] or "", str(c["like_count"] or ""),
                                 str(c["dislike_count"] or ""))
        if db.insert_comment_snapshot(conn, comment_id=cid, content_hash=chash, fields={
                "posted_at": c["posted_at"], "body_text": c["body_text"],
                "body_html": None, "like_count": c["like_count"],
                "reply_count": None,
                "raw_meta": {"ingested_by": "wayback", "capture": timestamp,
                             "dislike_count": c["dislike_count"]}}):
            stats.comment_snapshots += 1
    return len(comments)


class OutOfTime(RuntimeError):
    """The run's wall-clock budget is spent. Progress is recorded."""


def run(conn, *, slug: str, kind: str, year: int, client: WaybackClient,
        limit: int | None = None, stats: Stats | None = None,
        deadline: float | None = None) -> Stats:
    """One journal, one capture kind, one year."""
    stats = stats or Stats()
    spec = KINDS[kind]
    domain = DOMAINS[slug]
    # `limit` caps THIS leg. Shared stats across legs would otherwise turn a
    # per-leg trial cap into a global one and starve every later leg.
    leg_start = stats.captures

    rows = cdx_cached(client, domain, kind=kind, year=year)
    listed = len(rows)
    rows = newest_per_article(rows)
    log.info("[%s %s %d] %d captures listed, %d after folding views of the "
             "same article", slug, kind, year, listed, len(rows))
    done = already_done(conn)

    for i, row in enumerate(rows):
        if limit and stats.captures - leg_start >= limit:
            break
        if deadline and time.monotonic() > deadline:
            log.info("[%s %s %d] time budget reached; stopping cleanly", slug, kind, year)
            raise OutOfTime()
        digest = row.get("digest") or f"{row['timestamp']}:{row['original']}"
        if digest in done:
            stats.skipped += 1
            continue
        try:
            page = client.snapshot(row["timestamp"], row["original"])
        except GaveUp:
            log.error("[%s %s %d] archive is unwell; stopping this leg", slug, kind, year)
            raise
        except Exception as exc:
            stats.errors += 1
            # A 404 or 403 is the archive saying this capture is not replayable,
            # which will still be true tomorrow — record it so no future run
            # spends a request on it again. A timeout might be this minute's
            # weather, so it is left to be retried.
            if getattr(exc, "code", None) in (404, 403):
                stats.dead += 1
                _record(conn, digest=digest, slug=slug, timestamp=row["timestamp"],
                        original=row["original"], kind=kind, comments=None, ok=False)
                done.add(digest)
            else:
                log.debug("retryable failure on %s: %s", row["original"], exc)
            continue

        stats.captures += 1
        try:
            found = ingest(conn, slug=slug, page=page, original=row["original"],
                           timestamp=row["timestamp"], stats=stats)
        except Exception as exc:
            log.warning("ingest failed for %s: %s", row["original"], exc)
            stats.errors += 1
            continue
        if not found:
            stats.empty += 1
        _record(conn, digest=digest, slug=slug, timestamp=row["timestamp"],
                original=row["original"], kind=kind, comments=found)
        done.add(digest)

        if stats.captures % 25 == 0:
            log.info("[%s %s %d] %d/%d captures, %d comments so far",
                     slug, kind, year, i + 1, len(rows), stats.comment_snapshots)
    return stats
