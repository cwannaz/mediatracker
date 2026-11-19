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
import urllib.error
from dataclasses import dataclass

from . import db, fetch, ids
from .archive_body import (extract_body, extract_body_portal,
                           looks_like_portal)
from .archive_parse import looks_like_newsnetz
from .backfill import article_url_of
from .wayback import GaveUp, WaybackClient

log = logging.getLogger(__name__)

MARKER = "body_backfill"       # raw_meta key: this row has been attempted

# Which failures are worth asking about again. Measured on a random 12 of the
# 285 rows marked failed:HTTPError -- ten were 404 and two were 503. A 404 is
# the archive saying it holds no capture at that url and timestamp, which will
# not change; re-asking is spent politeness, not a recovered article. Anything
# here is the archive being briefly unable, which is exactly what a retry is
# for. Bare "HTTPError" is the legacy marker from before the code was
# recorded: retryable once so it gets reclassified into a code.
RETRYABLE = ("429", "500", "502", "503", "504",
             "TimeoutError", "URLError", "IncompleteRead", "HTTPException",
             "ReadTimeout", "HTTPError")


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
               min_capture: str | None = None, retry_failed: bool = False) -> list[tuple]:
    """Bodyless wayback snapshots that have not been attempted yet.

    Ordered by capture so a stint works through one era at a time: the eras
    differ in yield, and mixing them makes a run's progress unreadable.

    `min_capture` skips captures older than a "YYYY" (or fuller) prefix.

    It was added when the portal era was unreadable: every pre-2012 Le Matin
    capture -- 63,852 of the backlog -- was fetched, refused, and marked, so
    the gate avoided paying for pages whose capture date already said no.
    That is no longer why it exists. Those pages now have a reader, and the
    gate is simply how a stint is pointed at one era at a time: pass
    --min-capture 2012 for the Newsnetz material, or leave it off to work
    through the portal years, which is 63,852 requests of about 54 hours at a
    polite pace and so is a decision rather than a default.

    `retry_failed` re-opens rows a previous stint could not fetch, but only the
    ones worth re-asking: see RETRYABLE. Measured, 83% of the failures are 404
    -- the archive has no capture at that url and timestamp, and will not grow
    one. Re-fetching those spends requests on a donated server to be told the
    same thing.
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
          AND (NOT (s.raw_meta ? %(marker)s)
               {retry})
    """.format(retry="OR s.raw_meta->>'body_backfill' = ANY(%(retryable)s)"
                if retry_failed else "")
    params: dict = {"marker": MARKER}
    if retry_failed:
        params["retryable"] = [f"failed:{r}" for r in RETRYABLE]
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


def fetch_url_of(captured_url: str | None, canonical: str) -> str:
    """The url to ask the archive for: the article's own, never the thread view.

    Folding "?comments=1" and "/print.html" onto the article url is right, and
    measuring it is the only way to know. Where both views were captured, the
    article view yields 935 / 2,581 / 2,503 characters of prose and the
    comment view of the same capture yields 501 / 910 / 0. Where only the
    comment view was captured, it carries the article's LEAD and nothing else
    -- one paragraph of 117 to 175 characters, padded past the 400-character
    floor by poll questions, job adverts and sponsored blurbs that the
    furniture filter does not catch in that layout. Six of six sampled rows
    looked like this.

    So there is no fallback here. Asking the comment view would spend a
    request on a donated server to store a lead wrapped in advertising, which
    is the same trade the pre-2012 portal era is already refused for.

    The cost of that is 55 of the 2,267 outstanding rows, whose only capture
    is a comment view: the folded url names a record the archive does not
    hold, it answers 503, and they are marked unfetchable and stepped over.
    What must NOT happen is twelve of them at the head of the queue ending the
    stint, which is what the control capture in run() is for.
    """
    return article_url_of(captured_url or canonical)


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


def control_capture(conn, *, journal: str | None = None) -> tuple[str, str] | None:
    """A capture this backfill has already read: proof of what the archive can do.

    Used only to decide whether a run of failures is the archive's or the
    rows'. Any successfully read row will do, so the newest is taken -- it is
    the one most likely to still be served from a warm store.
    """
    sql = """
        SELECT s.raw_meta->>'capture', s.raw_meta->>'captured_url', a.canonical_url
        FROM article_snapshot s
        JOIN article a ON a.id = s.article_id
        JOIN journal j ON j.id = a.journal_id
        WHERE s.raw_meta->>'body_backfill' = 'ok'
          AND s.raw_meta->>'capture' IS NOT NULL
    """
    params: dict = {}
    if journal:
        sql += " AND j.slug = %(journal)s"
        params["journal"] = journal
    sql += " ORDER BY s.id DESC LIMIT 1"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    if not row:
        return None
    capture, captured_url, canonical = row
    return capture, fetch_url_of(captured_url, canonical)


def _archive_answers(client: WaybackClient, control: tuple[str, str]) -> bool:
    """Can the archive still serve a capture we have read before?

    A success here also resets the client's consecutive-failure counter, which
    is what lets the run carry on.
    """
    capture, url = control
    try:
        page = client.snapshot(capture, url)
    except Exception as exc:
        log.warning("control capture failed too (%s): the archive is unwell",
                    type(exc).__name__)
        return False
    if len(page) <= 1000:
        # An answer, but not the page we know that capture holds: treat a
        # stub or an error body as the archive being unwell rather than as
        # permission to carry on marking rows.
        log.warning("control capture answered only %d chars: not proof of "
                    "health", len(page))
        return False
    log.info("control capture answered %d chars: the archive is fine, so the "
             "failures belong to the rows", len(page))
    return True


def run(conn, *, client: WaybackClient, journal: str | None = None,
        limit: int | None = None, max_hours: float | None = None,
        min_capture: str | None = None, retry_failed: bool = False,
        progress=None) -> dict:
    """Fetch and store bodies until the list, the budget or the archive ends."""
    # Both reads are retried: at boot this stint starts in the same second as
    # the daemon, whose ensure_schema takes the same tables in the opposite
    # order, and the loser of that race is always this one.
    rows = db.retrying_on_lock(
        lambda: candidates(conn, journal=journal, limit=limit,
                           min_capture=min_capture, retry_failed=retry_failed),
        what="the backfill queue")
    log.info("body backfill: %d snapshots to attempt%s",
             len(rows), f" ({journal})" if journal else "")
    st = Stats()
    deadline = time.time() + max_hours * 3600 if max_hours else None
    control = db.retrying_on_lock(lambda: control_capture(conn, journal=journal),
                                  what="the control capture")

    for snap_id, aid, slug, capture, captured_url, canonical, headline, ccount in rows:
        if deadline and time.time() > deadline:
            log.info("budget reached; progress recorded and resumable")
            raise OutOfTime(st.as_dict())
        st.seen += 1
        plain = fetch_url_of(captured_url, canonical)
        try:
            page = client.snapshot(capture, plain)
        except GaveUp:
            # The client counts consecutive failures and cannot tell "the
            # archive is unwell" from "these twelve rows are unfetchable" --
            # both arrive as a 5xx. So ask it something we know it can answer:
            # a capture this backfill has already read. If that comes back,
            # the archive is fine and the failures belong to the rows, which
            # are then marked and stepped over instead of ending the stint.
            # Without this, a dozen bad rows at the head of the queue abort a
            # 2,267-row run after 12 attempts, which is what happened twice
            # today.
            if control and _archive_answers(client, control):
                st.failed += 1
                _mark(conn, snap_id, "failed:unfetchable")
                conn.commit()
                continue
            # The control failed too: the archive really does want us to stop.
            # Marking this row would lose an article to a bad night, so it
            # stays unattempted.
            log.warning("archive asked us to stop after %d rows", st.seen)
            break
        except urllib.error.HTTPError as exc:
            # The status code, not just the type: a 404 is permanent and a 503
            # is this minute's weather, and "HTTPError" cannot tell them apart.
            st.failed += 1
            _mark(conn, snap_id, f"failed:{exc.code}")
            conn.commit()
            continue
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
        # Each era gets the reader written for it. The generic reader keeps
        # the pre-2012 portal soup -- teasers and promos in bare <p>, which no
        # furniture rule tells from prose -- so that era is read through its
        # own template instead: one container holds the article's text and the
        # other stories sit outside it, so the soup is never reached rather
        # than filtered. Validated on 24 captures across 2008-2011: 24 bodies,
        # zero teasers, mean 2,100 characters.
        if looks_like_newsnetz(page):
            body = extract_body(page)
        elif looks_like_portal(page):
            body = extract_body_portal(page)
        else:
            # The marker is kept as it was spelt when it meant only this:
            # neither template recognised. Renaming it now would give one
            # outcome two names across the 1,416 rows already carrying it.
            st.wrong_era += 1
            _mark(conn, snap_id, "not_newsnetz")
            conn.commit()
            continue

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
    from .config import load_config

    p = argparse.ArgumentParser(prog="mediatracker.body_backfill")
    p.add_argument("--journal", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--delay", type=float, default=2.0)
    p.add_argument("--max-hours", type=float, default=None)
    p.add_argument("--min-capture", default=None,
                   help="skip captures older than this YYYY[MM...] prefix")
    p.add_argument("--retry-failed", action="store_true",
                   help="re-open rows a previous stint could not fetch")
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

    # Exit 0, not an error code: this also runs as mt-journals-bodies.service,
    # and a deliberate hold must not leave a failed unit on the fleet
    # dashboard. The printed line is what tells a person nothing was fetched.
    reason = fetch.hold_reason("the archive body backfill")
    if reason:
        print(reason)
        return 0

    client = WaybackClient(min_delay=a.delay)

    def show(st: Stats, total: int) -> None:
        print(f"  {st.seen:,}/{total:,}  bodies {st.bodies:,}  "
              f"no_body {st.no_body:,}  failed {st.failed:,}", flush=True)

    try:
        out = run(conn, client=client, journal=a.journal, limit=a.limit,
                  max_hours=a.max_hours, min_capture=a.min_capture,
                  retry_failed=a.retry_failed, progress=show)
    except OutOfTime as stop:
        out = stop.args[0]
        out["stopped"] = "budget"
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
