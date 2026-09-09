"""Filling in the article text the first archive pass never read.

124,203 wayback-origin articles carry a headline and no body. The captures we
hold are comment views, which never contained the article; the text is in the
archive's capture of the plain article URL. See archive_body for why the old
readers returned nothing and said nothing.

Measured on 40 real captures spanning 2008-2020 and all three papers:
80% yield, 3.1s per article, one request each, ~118 KB per page. The whole
backlog is about 109 hours of fetching and 15 GB.

Deliberately NOT two requests. The obvious implementation asks CDX for the
plain URL's captures and then fetches one: measured, that is 18.2s per article
and 26 days. Every row here already stores the timestamp of a capture taken
near the article, and /web/<ts>/<url> redirects to the archive's nearest
capture of that url -- so the stored timestamp is a free CDX lookup.

Every attempt is marked, successful or not. A run that retried its failures
would spend the whole night on the 2017-2019 stubs, which are 2 KB redirects
that will never yield a body no matter how often they are asked for.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from . import ids
from .archive_body import extract_body
from .archive_parse import looks_like_newsnetz
from .backfill import article_url_of
from .wayback import GaveUp, WaybackClient

log = logging.getLogger(__name__)

MARKER = "body_backfill"       # raw_meta key: this row has been attempted


@dataclass
class Stats:
    seen: int = 0
    bodies: int = 0
    no_body: int = 0
    failed: int = 0
    conflict: int = 0
    wrong_era: int = 0
    chars: int = 0

    def as_dict(self) -> dict:
        return {"seen": self.seen, "bodies": self.bodies, "no_body": self.no_body,
                "failed": self.failed, "conflict": self.conflict,
                "wrong_era": self.wrong_era,
                "mean_body_chars": int(self.chars / self.bodies) if self.bodies else None,
                "yield_pct": round(100.0 * self.bodies / self.seen, 1) if self.seen else 0.0}


class OutOfTime(RuntimeError):
    """The stint's budget is spent; progress is committed and resumable."""


def candidates(conn, *, journal: str | None = None, limit: int | None = None,
               min_capture: str | None = None) -> list[tuple]:
    """Bodyless wayback snapshots that have not been attempted yet.

    Ordered by capture so a stint works through one era at a time: the eras
    differ in yield, and mixing them makes a run's progress unreadable.

    `min_capture` skips captures older than a "YYYY" (or fuller) prefix. The
    runtime gate already refuses the portal era, but only after paying for the
    page: measured, Le Matin's pre-2012 captures are 63,852 of the backlog and
    every one of them is refused. Fetching them to learn what the capture date
    already says would cost 54 hours and 64,000 requests on a donated server.
    The gate stays the authority on what is readable -- this only avoids asking
    for what is known unreadable.
    """
    sql = """
        SELECT s.id, a.id, j.slug,
               s.raw_meta->>'capture', s.raw_meta->>'captured_url', a.canonical_url,
               s.headline, s.comment_count
        FROM article_snapshot s
        JOIN article a ON a.id = s.article_id
        JOIN journal j ON j.id = a.journal_id
        WHERE a.origin = 'wayback'
          AND (s.body_text IS NULL OR length(s.body_text) < 200)
          AND s.raw_meta->>'capture' IS NOT NULL
          AND NOT (s.raw_meta ? %(marker)s)
    """
    params: dict = {"marker": MARKER}
    if journal:
        sql += " AND j.slug = %(journal)s"
        params["journal"] = journal
    if min_capture:
        sql += " AND s.raw_meta->>'capture' >= %(min_capture)s"
        params["min_capture"] = min_capture
    sql += " ORDER BY s.raw_meta->>'capture'"
    if limit:
        sql += " LIMIT %(limit)s"
        params["limit"] = limit
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _mark(conn, snap_id: int, outcome: str) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE article_snapshot
               SET raw_meta = coalesce(raw_meta, '{}'::jsonb) || %s::jsonb
             WHERE id = %s
        """, (json.dumps({MARKER: outcome}), snap_id))


def store_body(conn, *, snap_id: int, article_id: str, body: str,
               headline: str | None, comment_count) -> bool:
    """Write the recovered text onto the snapshot that was missing it.

    An UPDATE, not a new snapshot: this is the same capture we already
    recorded, read properly. Inserting a second row would put two documents
    per article into the search index, one of them known to be empty.

    content_hash is recomputed because it was derived from the empty body, and
    it is UNIQUE per article -- so a collision means a snapshot with exactly
    this content already exists, and the right move is to leave both alone.
    """
    chash = ids.content_hash(headline or "", body, str(comment_count or ""))
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM article_snapshot WHERE article_id = %s AND content_hash = %s",
                    (article_id, chash))
        if cur.fetchone():
            return False
        cur.execute("""
            UPDATE article_snapshot
               SET body_text = %s, content_hash = %s,
                   raw_meta = coalesce(raw_meta, '{}'::jsonb) || %s::jsonb
             WHERE id = %s
        """, (body, chash, json.dumps({MARKER: "ok"}), snap_id))
    return True


def run(conn, *, client: WaybackClient, journal: str | None = None,
        limit: int | None = None, max_hours: float | None = None,
        min_capture: str | None = None, progress=None) -> dict:
    """Fetch and store bodies until the list, the budget or the archive ends."""
    rows = candidates(conn, journal=journal, limit=limit, min_capture=min_capture)
    log.info("body backfill: %d snapshots to attempt%s",
             len(rows), f" ({journal})" if journal else "")
    st = Stats()
    deadline = time.time() + max_hours * 3600 if max_hours else None

    for snap_id, aid, slug, capture, captured_url, canonical, headline, ccount in rows:
        if deadline and time.time() > deadline:
            log.info("budget reached; progress recorded and resumable")
            raise OutOfTime(st.as_dict())
        st.seen += 1
        plain = article_url_of(captured_url or canonical)
        try:
            page = client.snapshot(capture, plain)
        except GaveUp:
            # The client has decided the archive wants us to stop. Marking this
            # row would lose an article to a bad night, so it stays unattempted.
            log.warning("archive asked us to stop after %d rows", st.seen)
            break
        except Exception as exc:
            st.failed += 1
            _mark(conn, snap_id, f"failed:{type(exc).__name__}")
            conn.commit()
            continue

        # Before 2012 Le Matin ran a portal template whose teasers, contest
        # promos and video captions sit in bare <p> exactly like article prose:
        # no class, no container, and low link density, so every generic
        # heuristic keeps them. Measured, one such page produced a 9,640-char
        # "body" made of a dozen unrelated stories. Nothing downstream could
        # tell that from an article, so the era is refused rather than guessed:
        # this predicate is True for all three Newsnetz papers and False for
        # the portal pages.
        if not looks_like_newsnetz(page):
            st.wrong_era += 1
            _mark(conn, snap_id, "not_newsnetz")
            conn.commit()
            continue

        body = extract_body(page)
        if not body:
            st.no_body += 1
            _mark(conn, snap_id, "no_body")
        elif store_body(conn, snap_id=snap_id, article_id=aid, body=body,
                        headline=headline, comment_count=ccount):
            st.bodies += 1
            st.chars += len(body)
        else:
            st.conflict += 1
            _mark(conn, snap_id, "duplicate")
        conn.commit()

        if progress and st.seen % 50 == 0:
            progress(st, len(rows))
    return st.as_dict()


def main(argv=None) -> int:
    import argparse
    from . import db
    from .config import load_config

    p = argparse.ArgumentParser(prog="mediatracker.body_backfill")
    p.add_argument("--journal", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--delay", type=float, default=2.0)
    p.add_argument("--max-hours", type=float, default=None)
    p.add_argument("--min-capture", default=None,
                   help="skip captures older than this YYYY[MM...] prefix")
    p.add_argument("--status", action="store_true")
    a = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    conn = db.connect(load_config())
    if conn is None:
        print("no database")
        return 1

    if a.status:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT j.slug, s.raw_meta->>%s AS outcome, count(*)
                FROM article_snapshot s
                JOIN article a ON a.id = s.article_id
                JOIN journal j ON j.id = a.journal_id
                WHERE a.origin = 'wayback'
                GROUP BY 1, 2 ORDER BY 1, 3 DESC
            """, (MARKER,))
            for slug, outcome, n in cur.fetchall():
                print(f"  {slug:10s} {str(outcome or '(not attempted)'):22s} {n:>8,}")
        return 0

    client = WaybackClient(min_delay=a.delay)

    def show(st: Stats, total: int) -> None:
        print(f"  {st.seen:,}/{total:,}  bodies {st.bodies:,}  "
              f"no_body {st.no_body:,}  failed {st.failed:,}", flush=True)

    try:
        out = run(conn, client=client, journal=a.journal, limit=a.limit,
                  max_hours=a.max_hours, min_capture=a.min_capture, progress=show)
    except OutOfTime as stop:
        out = stop.args[0]
        out["stopped"] = "budget"
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
