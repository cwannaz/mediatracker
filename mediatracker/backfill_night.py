"""An unattended backfill run, ordered by what is worth having most.

The archive holds far more than one night can politely fetch: Le Matin's 2015
threads alone are ~16,000 articles, and at one request every two seconds that
is fourteen hours for a single year. So the order matters more than the speed.

Breadth before depth. A sociological corpus with two thousand threads from each
of six years and three titles is worth more than nine thousand from one year of
one paper: the point of the material is comparison across time and across
publics, and a single deep year supports neither. Each leg therefore takes a
capped bite, and the rounds come back for more only once every leg has had one.

Legs are ordered by what the corpus is missing:

  1. Le Matin 2013-2016 — the hole. The corpus jumps from 2012 to 2021.
  2. The Tribune and 24 heures, any year — live scanning of those began in
     August 2026, so their entire history is missing, not merely a stretch.
  3. Le Matin 2012 — already represented by the printed archive, so the least
     valuable comment-bearing year, but it validates against known material.

Everything is resumable: each capture is recorded by digest, so a later round
of the same leg continues rather than repeats, and so does a later night.
"""
from __future__ import annotations

import logging
import time

from . import backfill, db
from .config import load_config
from .wayback import GaveUp, WaybackClient, stats as client_stats

log = logging.getLogger("mediatracker.night")

# (journal, year), most valuable first. Threads only — article pages keep.
LEGS: list[tuple[str, int]] = [
    ("lematin", 2015), ("lematin", 2016), ("lematin", 2014), ("lematin", 2013),
    ("tdg", 2014), ("tdg", 2015), ("24heures", 2014), ("24heures", 2015),
    ("tdg", 2013), ("tdg", 2016), ("24heures", 2013), ("24heures", 2016),
    ("lematin", 2012), ("tdg", 2012), ("24heures", 2012),
    ("lematin", 2011), ("tdg", 2011), ("24heures", 2011),
    ("lematin", 2010), ("lematin", 2009), ("lematin", 2008),
]

# Each round takes this many captures per leg before moving on.
ROUNDS = (150, 400, 800, 1600, 3200)


def run(hours: float = 7.0, delay: float = 2.0, kind: str = "threads") -> int:
    conn = db.connect(load_config())
    if conn is None:
        log.error("no database; refusing to fetch what we cannot store")
        return 2
    backfill.ensure_schema(conn)

    client = WaybackClient(min_delay=delay)
    deadline = time.monotonic() + hours * 3600
    totals = backfill.Stats()
    exhausted: set[tuple[str, int]] = set()

    try:
        for cap in ROUNDS:
            log.info("=== round: up to %d captures per leg ===", cap)
            for slug, year in LEGS:
                if (slug, year) in exhausted:
                    continue
                if time.monotonic() > deadline:
                    raise backfill.OutOfTime()
                before = totals.captures
                try:
                    backfill.run(conn, slug=slug, kind=kind, year=year,
                                 client=client, limit=cap, stats=totals,
                                 deadline=deadline)
                except backfill.OutOfTime:
                    raise
                except GaveUp:
                    log.error("archive is unwell; stopping the night here")
                    raise
                except Exception as exc:
                    log.warning("[%s %d] leg failed: %s", slug, year, exc)
                    exhausted.add((slug, year))
                    continue
                took = totals.captures - before
                if took < cap:
                    # Nothing left in this leg that we have not already stored.
                    exhausted.add((slug, year))
                log.info("[%s %s %d] +%d captures | totals %s | archive %s",
                         slug, kind, year, took, totals.as_dict(), client_stats(client))
    except (KeyboardInterrupt, GaveUp, backfill.OutOfTime):
        log.info("stopping cleanly; every capture is recorded and resumable")
    finally:
        log.info("NIGHT FINAL %s | archive %s", totals.as_dict(), client_stats(client))
    return 0


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="mediatracker.backfill_night")
    p.add_argument("--hours", type=float, default=7.0)
    p.add_argument("--delay", type=float, default=2.0)
    p.add_argument("--kind", default="threads", choices=sorted(backfill.KINDS))
    p.add_argument("--log-level", default="INFO")
    a = p.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, a.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    return run(hours=a.hours, delay=a.delay, kind=a.kind)


if __name__ == "__main__":
    raise SystemExit(main())
