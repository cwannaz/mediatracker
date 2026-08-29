"""`python3 -m mediatracker.backfill_cli` — pull the missing years in.

Separate from the daemon on purpose. This is a batch job that runs for hours
against a third party's servers; it should be startable, watchable and killable
without touching live scanning.

    # what is out there, without fetching any of it
    python3 -m mediatracker.backfill_cli --survey

    # one leg, paced
    python3 -m mediatracker.backfill_cli --journal lematin --kind threads --year 2015

    # everything worth having, in value order
    python3 -m mediatracker.backfill_cli --all
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from . import backfill, db
from .config import load_config
from .wayback import GaveUp, WaybackClient, stats as client_stats

log = logging.getLogger("mediatracker.backfill")


def survey(client: WaybackClient, journals, kinds) -> list[dict]:
    """Count what the archive holds without downloading a single page."""
    out = []
    for slug in journals:
        for kind in kinds:
            spec = backfill.KINDS[kind]
            for year in spec["years"]:
                try:
                    rows = client.cdx(backfill.DOMAINS[slug], year=year,
                                      url_filter=spec["filter"], fields="timestamp")
                except Exception as exc:
                    log.warning("[%s %s %d] survey failed: %s", slug, kind, year, exc)
                    continue
                if rows:
                    out.append({"journal": slug, "kind": kind,
                                "year": year, "captures": len(rows)})
                    log.info("[%s %-13s %d] %6d captures", slug, kind, year, len(rows))
    return out


def status(conn) -> int:
    """Where the backfill has got to. Reads only; safe while a run is going."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*), max(processed_at) FROM archive_capture")
        n, last = cur.fetchone()
        print(f"captures processed : {n}   last write {last}")
        cur.execute("""SELECT journal_slug, kind, count(*), sum(comments)
                       FROM archive_capture GROUP BY 1,2 ORDER BY 3 DESC""")
        for slug, kind, c, cm in cur.fetchall():
            print(f"  {slug:10s} {kind:13s} {c:6d} captures  {cm or 0:7d} comments")
        cur.execute("""SELECT count(*) FROM comment c JOIN article a ON a.id=c.article_id
                       WHERE a.origin='wayback'""")
        print(f"comments recovered : {cur.fetchone()[0]}")
        cur.execute("""SELECT count(DISTINCT c.author_nick) FROM comment c
                       JOIN article a ON a.id=c.article_id WHERE a.origin='wayback'""")
        print(f"nicknames recovered: {cur.fetchone()[0]}")
        cur.execute("SELECT count(*), count(DISTINCT author_nick) FROM comment")
        c, nk = cur.fetchone()
        print(f"CORPUS now         : {c} comments, {nk} nicknames")
        cur.execute("""SELECT EXTRACT(YEAR FROM cs.posted_at)::int y, count(*)
                       FROM comment c JOIN comment_snapshot cs ON cs.comment_id=c.id
                       JOIN article a ON a.id=c.article_id WHERE a.origin='wayback'
                       GROUP BY 1 ORDER BY 1""")
        print("by comment year    :", {y: n for y, n in cur.fetchall() if y})
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mediatracker.backfill_cli")
    p.add_argument("--journal", action="append",
                   choices=sorted(backfill.DOMAINS), default=None)
    p.add_argument("--kind", action="append",
                   choices=sorted(backfill.KINDS), default=None)
    p.add_argument("--year", type=int, action="append", default=None)
    p.add_argument("--all", action="store_true",
                   help="every journal, every kind, threads before articles")
    p.add_argument("--survey", action="store_true",
                   help="count captures only; fetch nothing")
    p.add_argument("--status", action="store_true",
                   help="what the archive has yielded so far; touches nothing")
    p.add_argument("--limit", type=int, default=None,
                   help="stop after this many captures per leg (for a trial run)")
    p.add_argument("--delay", type=float, default=2.0,
                   help="minimum seconds between requests to the archive")
    p.add_argument("--max-hours", type=float, default=None,
                   help="stop cleanly after this long; progress is resumable")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    journals = args.journal or sorted(backfill.DOMAINS)
    # Threads first everywhere: they are the irreplaceable half, and article
    # pages remain available long after a night's budget runs out.
    kinds = args.kind or (["threads", "threads-html", "articles"]
                          if args.all or args.survey else ["threads"])

    conn = db.connect(load_config())
    if conn is None:
        log.error("no database; refusing to fetch what we cannot store")
        return 2
    backfill.ensure_schema(conn)

    if args.status:
        return status(conn)

    client = WaybackClient(min_delay=args.delay)

    if args.survey:
        rows = survey(client, journals, kinds)
        total = sum(r["captures"] for r in rows)
        log.info("survey: %d captures across %d legs; %s", total, len(rows),
                 client_stats(client))
        return 0

    deadline = (time.monotonic() + args.max_hours * 3600) if args.max_hours else None
    totals = backfill.Stats()
    try:
        for kind in kinds:
            years = args.year or list(backfill.KINDS[kind]["years"])
            for slug in journals:
                for year in years:
                    try:
                        backfill.run(conn, slug=slug, kind=kind, year=year,
                                     client=client, limit=args.limit,
                                     stats=totals, deadline=deadline)
                    except GaveUp as exc:
                        log.error("stopping: %s", exc)
                        raise
                    log.info("[%s %s %d] running totals: %s | archive: %s",
                             slug, kind, year, totals.as_dict(), client_stats(client))
    except (KeyboardInterrupt, GaveUp, backfill.OutOfTime):
        log.info("interrupted; progress is recorded and the run is resumable")
    finally:
        log.info("FINAL %s | archive: %s", totals.as_dict(), client_stats(client))
    return 0


if __name__ == "__main__":
    sys.exit(main())
