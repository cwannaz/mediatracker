"""Fetch the counters for the years whose words are gone.

`community_counts` can read one archived `/api/communities/<id>` response.
This walks all of them.

The endpoint fed the little row of counters above each Le Matin article, and
the crawler kept hitting it long after it stopped following `?comments=1`. So
for 2017-2020 — four years from which the corpus holds essentially no comment
text — it yields the headline, the share figures and, above all, **how many
comments the thread had**. Not who wrote them and not what they said, but the
shape of the public over the years the words were lost.

A row written here carries a comment count and no comment rows, and that is
not an absence of comments: see the note at the top of `community_counts`.
The snapshot's `raw_meta.counts_only` is the flag that says so, and nothing
downstream may read a missing thread on such a row as an empty one.

Captures are NOT folded to one per article. Two captures of the same story a
week apart are two measurements of a growing thread, and the archive's own
content digest means an unchanged counter costs nothing to have asked twice.

    python3 -m mediatracker.community_backfill --year 2017 --year 2018
    python3 -m mediatracker.community_backfill --survey
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from . import backfill, community_counts, db, ids
from .config import load_config
from .wayback import GaveUp, WaybackClient, stats as client_stats

log = logging.getLogger("mediatracker.community_backfill")

SLUG = "lematin"
KIND = "communities"
# The path carries the story id, and a query string may follow it (`?_=1573…`
# cache-busters are common), so this is deliberately not anchored at the end.
PATTERN = r".*/api/communities/[0-9]+.*"
# 2016 and 2021 are in for the joins they make possible, not for themselves:
# both years have real threads elsewhere in the corpus, so a counter caught in
# them can be checked against a count we already hold. That is the only
# calibration available for the four years in between, where nothing else
# exists to check against.
YEARS = range(2016, 2022)


def ingest_one(conn, *, payload: str, original: str, timestamp: str,
               stats: backfill.Stats) -> bool:
    """Write one counter reading. True if it was a new measurement."""
    got = community_counts.parse(payload, captured_url=original)
    if not got:
        return False

    jid = ids.journal_id(SLUG)
    key = got["source_key"]
    # Prefer the article we already hold under this story id: the same story
    # reached through /story/<slug>-<id> lives at a different URL, and hanging
    # a second row off the short form would split one article in two.
    found = db.find_article_by_source_key(conn, jid, key)
    if found:
        aid, canonical = found
    else:
        canonical = ids.canonical_url(community_counts.article_url_for(key))
        aid = ids.article_id(SLUG, canonical)
    db.upsert_article(conn, aid=aid, journal_id=jid, canonical_url=canonical,
                      source_key=key, origin="wayback")
    stats.articles += 1

    refreshed = got["refreshed_at"]
    # The count is what changes; the headline rarely does. Hashing both means a
    # retitled story is kept as a second reading rather than silently dropped.
    chash = ids.content_hash(str(got["comment_count"]),
                             json.dumps(got["shares"], sort_keys=True),
                             got["headline"] or "")
    new = db.insert_article_snapshot(conn, article_id=aid, content_hash=chash, fields={
        "headline": got["headline"],
        "comment_count": got["comment_count"],
        "raw_meta": {
            "ingested_by": "wayback",
            # The load-bearing flag: this row's comment_count was read off a
            # counter, and no comment rows accompany it.
            "counts_only": True,
            "capture": timestamp,
            "captured_url": original,
            "shares": got["shares"],
            # The publisher's own clock, which can precede the capture by
            # hours and is the better date for the measurement.
            "refreshed_at": refreshed.isoformat() if refreshed else None,
            "article_uid": got["article_uid"],
        },
    })
    if new:
        stats.snapshots += 1
    return bool(new)


def run(conn, *, year: int, client: WaybackClient, stats: backfill.Stats,
        limit: int | None = None, deadline: float | None = None) -> backfill.Stats:
    domain = backfill.DOMAINS[SLUG]
    rows = backfill.cdx_cached(client, domain, kind=KIND, year=year, pattern=PATTERN)
    rows.sort(key=lambda r: r.get("timestamp", ""))
    log.info("[%s %s %d] %d counter captures listed", SLUG, KIND, year, len(rows))
    done = backfill.already_done(conn)
    leg_start = stats.captures

    for i, row in enumerate(rows):
        if limit and stats.captures - leg_start >= limit:
            break
        if deadline and time.monotonic() > deadline:
            log.info("[%s %s %d] time budget reached; stopping cleanly", SLUG, KIND, year)
            raise backfill.OutOfTime()
        digest = row.get("digest") or f"{row['timestamp']}:{row['original']}"
        if digest in done:
            stats.skipped += 1
            continue
        try:
            payload = client.snapshot(row["timestamp"], row["original"])
        except GaveUp:
            log.error("[%s %s %d] archive is unwell; stopping this leg", SLUG, KIND, year)
            raise
        except Exception as exc:
            stats.errors += 1
            if getattr(exc, "code", None) in (404, 403):
                stats.dead += 1
                backfill._record(conn, digest=digest, slug=SLUG, timestamp=row["timestamp"],
                                 original=row["original"], kind=KIND, comments=None, ok=False)
                done.add(digest)
            continue

        stats.captures += 1
        try:
            wrote = ingest_one(conn, payload=payload, original=row["original"],
                               timestamp=row["timestamp"], stats=stats)
        except Exception as exc:
            log.warning("ingest failed for %s: %s", row["original"], exc)
            stats.errors += 1
            continue
        if not wrote:
            # Either the body was not this shape, or the counter had not moved
            # since a reading we already hold. Neither is worth re-fetching.
            stats.empty += 1
        backfill._record(conn, digest=digest, slug=SLUG, timestamp=row["timestamp"],
                         original=row["original"], kind=KIND, comments=None)
        done.add(digest)

        if stats.captures % 25 == 0:
            log.info("[%s %s %d] %d/%d captures, %d readings stored",
                     SLUG, KIND, year, i + 1, len(rows), stats.snapshots)
    return stats


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mediatracker.community_backfill")
    p.add_argument("--year", type=int, action="append", default=None)
    p.add_argument("--survey", action="store_true", help="count captures; fetch nothing")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--delay", type=float, default=4.0)
    p.add_argument("--max-hours", type=float, default=None)
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    years = args.year or list(YEARS)
    client = WaybackClient(min_delay=args.delay)

    if args.survey:
        total = 0
        for year in years:
            rows = backfill.cdx_cached(client, backfill.DOMAINS[SLUG],
                                       kind=KIND, year=year, pattern=PATTERN)
            total += len(rows)
            log.info("[%s %d] %6d counter captures", SLUG, year, len(rows))
        log.info("survey: %d captures; %s", total, client_stats(client))
        return 0

    conn = db.connect(load_config())
    if conn is None:
        log.error("no database; refusing to fetch what we cannot store")
        return 2
    backfill.ensure_schema(conn)

    deadline = (time.monotonic() + args.max_hours * 3600) if args.max_hours else None
    totals = backfill.Stats()
    try:
        for year in years:
            run(conn, year=year, client=client, stats=totals,
                limit=args.limit, deadline=deadline)
            log.info("[%s %d] running totals: %s | archive: %s",
                     SLUG, year, totals.as_dict(), client_stats(client))
    except (KeyboardInterrupt, GaveUp, backfill.OutOfTime):
        log.info("interrupted; progress is recorded and the run is resumable")
    finally:
        log.info("FINAL %s | archive: %s", totals.as_dict(), client_stats(client))
    return 0


if __name__ == "__main__":
    sys.exit(main())
