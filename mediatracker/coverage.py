"""What we could have seen, month by month, beside what we did see.

A profile's activity plot draws comments per month. Read alone it invites one
specific mistake: a flat stretch looks like someone went quiet, when often it
is the corpus that went quiet. Le Matin's 2017-2020 threads are not thin
because nobody wrote -- they are thin because the crawler stopped following
the thread URL. A plot that cannot tell those apart is a plot that lies.

So this measures, per journal and per month, how much of the paper we hold,
and the plot shades the shortfall. Every band means "do not read the gap under
me as silence".

Three bases, because the honest denominator changes with the era:

  published  the paper's own sitemap says it published N articles that month
             and we hold n of them. The strongest statement available, and it
             covers Le Matin from 2012 and the sisters from 2011. Used only
             when most of the month's days are actually mirrored -- see
             `_month_is_mirrored`, without which 2011's 31 scattered day files
             would claim Le Matin published two articles that November.
  archive    no sitemap reaches that far back, so the denominator is what the
             Internet Archive holds: of N captures it lists for that month we
             fetched n. This measures OUR retrieval, not the archive's own
             completeness against the world -- a month where the crawler never
             visited scores 100% on this basis while holding almost nothing.
             That is why the basis travels with the number.
  none       neither denominator exists. Coverage is NULL, not zero: we do not
             know how much we are missing, which is its own kind of warning.

`has_live` is separate and matters more than the fraction. A month with live
scanning was polled on a schedule while threads were open, so absence in it is
evidence of absence. In every other month absence proves nothing at all.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from pathlib import Path

from . import db
from .config import load_config
from .sitemap_backfill import SITEMAP_DIR, _DAY, _LOC, hashed_files, day_index

log = logging.getLogger(__name__)

SLUGS = ("lematin", "24heures", "tdg")
DOMAIN = {"lematin": "lematin.ch", "24heures": "24heures.ch", "tdg": "tdg.ch"}


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS coverage_month (
            journal_slug       TEXT   NOT NULL,
            month              CHAR(7) NOT NULL,          -- YYYY-MM
            articles_held      INTEGER NOT NULL DEFAULT 0,
            articles_published INTEGER,                   -- paper's own sitemap
            captures_listed    INTEGER,                   -- archive CDX index
            captures_fetched   INTEGER,
            coverage           REAL,                      -- 0..1, NULL if unknown
            basis              TEXT   NOT NULL,           -- published|archive|none
            has_live           BOOLEAN NOT NULL DEFAULT FALSE,
            computed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (journal_slug, month)
        );
        """)
    conn.commit()


# --------------------------------------------------------------------------- #
# denominators

def published_per_month(slug: str) -> tuple[Counter, dict[str, int | None]]:
    """Articles the paper itself lists per month, and how many days back it.

    The day count is the guard on the denominator. Le Matin's mirror holds all
    of 2012 onward but only 31 scattered files from 2011, so an unguarded count
    says the paper published two articles in November 2011 -- and a coverage
    figure against that is worse than none, because it draws a confident band
    over a month nobody measured. A month backed by a handful of days is not a
    denominator; `build` demotes it to the archive basis instead.

    None means "not applicable": the sisters date each record individually and
    publish no per-day files, so there is nothing to count days of.
    """
    out: Counter = Counter()
    days: dict[str, int | None] = {}
    root = SITEMAP_DIR / slug
    if not root.is_dir():
        return out, days
    dated = [p for p in root.glob("*.xml") if _DAY.match(p.name)]
    if dated:                                   # Le Matin: one file per day
        for p in dated:
            m = p.stem[:7]
            out[m] += len(_LOC.findall(p.read_text(encoding="utf-8", errors="replace")))
            days[m] = (days.get(m) or 0) + 1
        return out, days
    if hashed_files(slug):                      # the sisters: dated per record
        for line in day_index(slug).read_text(encoding="utf-8").splitlines():
            day, _, url = line.partition("\t")
            if url and day:
                out[day[:7]] += 1
                days.setdefault(day[:7], None)
    return out, days


def listed_per_month(slug: str) -> Counter:
    """Captures the archive's index lists, per month, from the CDX cache.

    Every kind pooled: they are different views of the same month's articles,
    and what the band answers is whether that month was reachable at all.
    """
    out: Counter = Counter()
    cache = Path(load_config().data_dir) / "cdx"
    for f in cache.glob(f"{DOMAIN[slug]}.*.json"):
        try:
            rows = json.loads(f.read_text())
        except ValueError:
            continue
        for r in rows:
            ts = str(r.get("timestamp") or "")
            if len(ts) >= 6:
                out[f"{ts[:4]}-{ts[4:6]}"] += 1
    return out


# --------------------------------------------------------------------------- #
# numerators

_HELD = """
WITH smin AS (
    SELECT article_id, min(published_at) AS t FROM article_snapshot
     WHERE published_at IS NOT NULL GROUP BY 1
), cmin AS (
    SELECT c.article_id, min(cs.posted_at) AS t
      FROM comment c JOIN comment_snapshot cs ON cs.comment_id = c.id
     WHERE cs.posted_at IS NOT NULL GROUP BY 1
)
SELECT j.slug, to_char(COALESCE(smin.t, cmin.t), 'YYYY-MM') AS m, count(*)
  FROM article a
  JOIN journal j ON j.id = a.journal_id
  LEFT JOIN smin ON smin.article_id = a.id
  LEFT JOIN cmin ON cmin.article_id = a.id
 WHERE COALESCE(smin.t, cmin.t) IS NOT NULL
 GROUP BY 1, 2
"""
# The paper's own date when it gave one, else the thread's first comment.
# Archive rows carry no published_at at all -- deliberately, since a capture
# time is not a publication time -- so for them the thread IS the only date,
# which suits a plot whose x-axis is comment months.


def held_per_month(conn) -> dict[str, Counter]:
    out: dict[str, Counter] = {s: Counter() for s in SLUGS}
    with conn.cursor() as cur:
        cur.execute(_HELD)
        for slug, m, n in cur.fetchall():
            if slug in out and m:
                out[slug][m] = n
    return out


def fetched_per_month(conn) -> dict[str, Counter]:
    out: dict[str, Counter] = {s: Counter() for s in SLUGS}
    with conn.cursor() as cur:
        cur.execute("""SELECT journal_slug, left(timestamp,4)||'-'||substr(timestamp,5,2),
                              count(*) FROM archive_capture WHERE ok GROUP BY 1,2""")
        for slug, m, n in cur.fetchall():
            if slug in out:
                out[slug][m] = n
    return out


def live_months(conn) -> dict[str, set]:
    """Months in which the daemon was actually scanning this title."""
    out: dict[str, set] = {s: set() for s in SLUGS}
    with conn.cursor() as cur:
        cur.execute("""SELECT j.slug, to_char(s.fetched_at, 'YYYY-MM')
                         FROM article_snapshot s
                         JOIN article a ON a.id = s.article_id
                         JOIN journal j ON j.id = a.journal_id
                        WHERE a.origin = 'live' GROUP BY 1,2""")
        for slug, m in cur.fetchall():
            if slug in out:
                out[slug].add(m)
    return out


# --------------------------------------------------------------------------- #

def build(conn) -> int:
    """Recompute every month for every title. Returns rows written."""
    ensure_schema(conn)
    held, fetched, live = held_per_month(conn), fetched_per_month(conn), live_months(conn)
    rows = []
    for slug in SLUGS:
        (pub, pub_days), listed = published_per_month(slug), listed_per_month(slug)
        typical = _typical_month(pub)
        months = set(held[slug]) | set(pub) | set(listed) | set(fetched[slug])
        for m in sorted(months):
            h, p = held[slug].get(m, 0), pub.get(m)
            li, f = listed.get(m), fetched[slug].get(m, 0)
            if (p and _month_is_mirrored(m, pub_days.get(m, 0))
                    and _month_is_plausible(p, typical)):
                cov, basis = min(1.0, h / p), "published"
            elif li:
                cov, basis = min(1.0, f / li), "archive"
            else:
                cov, basis = None, "none"
            rows.append((slug, m, h, p, li, f, cov, basis, m in live[slug]))
    with conn.cursor() as cur:
        cur.execute("DELETE FROM coverage_month")
        cur.executemany("""
            INSERT INTO coverage_month (journal_slug, month, articles_held,
                articles_published, captures_listed, captures_fetched,
                coverage, basis, has_live)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
    conn.commit()
    log.info("coverage: %d journal-months", len(rows))
    return len(rows)


def timeline(conn, journals: list[str] | None = None) -> list[dict]:
    """Per-month coverage, best across the given titles.

    Best, not average: the question a band answers is whether this person could
    have been seen at all that month, and one well-covered title they wrote in
    is enough for that. Averaging would darken a month because some OTHER paper
    they never touched was missing.
    """
    sql = "SELECT month, coverage, basis, has_live, articles_held, articles_published" \
          " FROM coverage_month"
    args: list = []
    if journals:
        sql += " WHERE journal_slug = ANY(%s)"
        args.append(list(journals))
    sql += " ORDER BY month"
    best: dict[str, dict] = {}
    with conn.cursor() as cur:
        cur.execute(sql, args)
        for m, cov, basis, live, held, pub in cur.fetchall():
            cur_best = best.get(m)
            row = {"month": m, "coverage": cov, "basis": basis,
                   "live": live, "held": held, "published": pub}
            if cur_best is None or _rank(row) > _rank(cur_best):
                best[m] = row
    return [best[m] for m in sorted(best)]


# A month needs most of its days mirrored before its article count can serve
# as a denominator. 80% rather than all of them: a paper that published
# nothing on a given day leaves no file, and demanding 31 of 31 would throw
# away good months to guard against bad ones.
def _month_is_mirrored(month: str, days_seen: int | None) -> bool:
    if days_seen is None:
        return True                       # not a per-day mirror; nothing to check
    import calendar
    y, mo = (int(x) for x in month.split("-"))
    return days_seen >= 0.8 * calendar.monthrange(y, mo)[1]


# The sisters publish no per-day files, so the day guard cannot see their
# partial years -- and 2011 is partial for them exactly as it is for Le Matin
# (1,564 URLs for the whole year against ~2,000 a month from 2012). Catch it
# by volume instead: a real news month does not fall to a quarter of the
# title's normal output, so one that has must be a partial listing.
def _month_is_plausible(count: int, typical: float) -> bool:
    return typical <= 0 or count >= 0.25 * typical


def _typical_month(pub: Counter) -> float:
    vals = sorted(v for v in pub.values() if v > 0)
    return vals[len(vals) // 2] if vals else 0.0


def _rank(r: dict) -> tuple:
    # Live beats any fraction; a known fraction beats an unknown one.
    return (1 if r["live"] else 0,
            1 if r["coverage"] is not None else 0,
            r["coverage"] or 0.0)


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="mediatracker.coverage")
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--show", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    conn = db.connect(load_config())
    if conn is None:
        return 2
    if a.rebuild:
        build(conn)
    if a.show:
        for r in timeline(conn):
            c = "  ?  " if r["coverage"] is None else f"{r['coverage']*100:4.0f}%"
            print(f"{r['month']}  {c}  {r['basis']:9s}"
                  f"{'  LIVE' if r['live'] else ''}  held={r['held']}"
                  f"{'' if r['published'] is None else '/'+str(r['published'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
