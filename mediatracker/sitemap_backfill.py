"""Walking the publisher's own back catalogue, one day at a time.

The Internet Archive stops being useful for Le Matin after 2016: asked for the
modern URL grammar it holds nothing at all for 2017 onward. But the paper
publishes what the archive lacks — `robots.txt` names
`/sitemaps/fr/articles.xml`, an index of one sitemap per day running from
2009-05-07 to the present, 365 files a year with no gaps after 2011. Roughly
485,000 article URLs, and every one of them still resolves.

That closes the discovery problem the whole 2017-2026 stretch was stuck on. It
does not close the *evidence* problem, and the difference matters:

  * A `live` row was fetched on a schedule while the thread was open. Absence
    of a comment means there was no comment.
  * A `sitemap` row is the same live site read years later. An empty thread may
    always have been empty, or may have been pruned, closed or migrated since.
    **Absence proves nothing**, exactly as with an archive capture — which is
    why this origin sits in `db.ARCHIVE_ORIGINS` and never joins the rescan
    work-list.

**Comments were switched off between 2017 and 2020.** Three independent lines
agree: the archive holds no captures, sampled pages of those years report
`commentingEnabled` false and a zero count across 32 articles, and Cedric's own
printed archive — the record of someone who was commenting throughout — stops
in 2015 and resumes in 2021. So those four years have articles and no public,
and the run is ordered to reach 2021 onward first.

Pace is deliberately slower than the daily scan's. The daemon is already
talking to this host, and this job has a quarter of a million pages to get
through; there is no version of that which should be hurried.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)

SITEMAP_DIR = Path("/mnt/storage/Projects/MediaTracker/sitemaps")
_LOC = re.compile(r"<loc>([^<]+)</loc>")
_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.xml$")

# The years the paper ran comments. 2016 is included because the Newsnetz
# platform was still serving threads into January of it, and 2017-2020 are
# excluded because there is nothing there to find.
YEARS_WITH_COMMENTS = tuple(range(2021, 2027))


def day_files(slug: str = "lematin", years: tuple[int, ...] | None = None) -> list[Path]:
    """Mirrored day sitemaps, newest first, optionally restricted to years."""
    root = SITEMAP_DIR / slug
    out = []
    for p in root.glob("*.xml"):
        m = _DAY.match(p.name)
        if not m:
            continue
        if years and int(m.group(1)) not in years:
            continue
        out.append(p)
    return sorted(out, reverse=True)


def urls_in(path: Path) -> list[str]:
    """Article URLs listed in one day's sitemap."""
    try:
        return _LOC.findall(path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        log.warning("unreadable sitemap %s: %s", path, exc)
        return []


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sitemap_seen (
                url          TEXT PRIMARY KEY,
                journal_slug TEXT NOT NULL,
                day          DATE,
                seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                ok           BOOLEAN
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS sitemap_seen_day ON sitemap_seen (day)")
    conn.commit()


def already_seen(conn, slug: str) -> set[str]:
    """URLs this backfill has already attempted.

    Recorded whether or not the fetch produced anything, so a page that simply
    has no comments is not re-fetched on every resume — the mistake that made
    the archive runs re-try their dead captures for two days.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT url FROM sitemap_seen WHERE journal_slug = %s", (slug,))
        return {r[0] for r in cur.fetchall()}


def mark_seen(conn, *, url: str, slug: str, day: str | None, ok: bool) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO sitemap_seen (url, journal_slug, day, ok)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (url) DO UPDATE SET seen_at = now(), ok = EXCLUDED.ok
        """, (url, slug, day, ok))


class OutOfTime(RuntimeError):
    """The run's budget expired; progress is recorded and resumable."""


async def run(*, slug: str = "lematin", years: tuple[int, ...] = YEARS_WITH_COMMENTS,
              delay: float = 5.0, max_hours: float | None = None,
              limit: int | None = None) -> dict:
    """Fetch every article the mirrored sitemaps list, newest day first.

    Newest first because 2021 onward is the stretch held today only as Cedric's
    printouts — a sample selected by his own participation — so an unselected
    reading of it is worth more per page than anything older, and a run cut
    short should have spent its time there.
    """
    from . import db, ids
    from .images import BlobStore
    from .config import load_config
    from .fetch import Fetcher
    from .pipeline import IngestStats, Pipeline
    from .sources import get
    from .store import JsonlStore

    cfg = load_config()
    conn = db.connect(cfg)
    if conn is None:
        raise RuntimeError("no database; refusing to fetch what we cannot store")
    db.ensure_schema(conn)
    ensure_schema(conn)

    cls = get(slug)
    source = cls()
    fetcher = Fetcher(cfg)
    pipe = Pipeline(conn=conn, blobs=BlobStore(cfg.blob_path),
                    store=JsonlStore(cfg.jsonl_path), fetcher=fetcher,
                    origin="sitemap")
    stats = IngestStats()
    seen = already_seen(conn, slug)
    deadline = (time.monotonic() + max_hours * 3600) if max_hours else None
    done = skipped = failed = 0

    try:
        for path in day_files(slug, years):
            day = path.stem
            for url in urls_in(path):
                if url in seen:
                    skipped += 1
                    continue
                if deadline and time.monotonic() > deadline:
                    raise OutOfTime()
                if limit and done >= limit:
                    raise OutOfTime()
                ok = True
                try:
                    await pipe._ingest_article(source, url, stats)
                except Exception as exc:
                    ok = False
                    failed += 1
                    log.warning("[%s] %s: %s", day, url[-60:], exc)
                mark_seen(conn, url=url, slug=slug, day=day, ok=ok)
                seen.add(url)
                done += 1
                if done % 25 == 0:
                    conn.commit()
                    log.info("[%s %s] %d fetched, %d skipped, %d failed | %s",
                             slug, day, done, skipped, failed, stats.as_dict()
                             if hasattr(stats, "as_dict") else stats)
                time.sleep(delay)
            conn.commit()
    except OutOfTime:
        log.info("budget reached; progress recorded and resumable")
    finally:
        conn.commit()
        close = getattr(fetcher, "close", None)
        if close:
            res = close()
            if hasattr(res, "__await__"):
                await res
    return {"fetched": done, "skipped": skipped, "failed": failed}


def main(argv=None) -> int:
    import argparse
    import asyncio
    p = argparse.ArgumentParser(prog="mediatracker.sitemap_backfill")
    p.add_argument("--journal", default="lematin")
    p.add_argument("--year", type=int, action="append", default=None)
    p.add_argument("--delay", type=float, default=5.0,
                   help="seconds between article fetches; the daemon is also "
                        "talking to this host, so slower than its own pace")
    p.add_argument("--max-hours", type=float, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--log-level", default="INFO")
    a = p.parse_args(argv)
    logging.basicConfig(level=getattr(logging, a.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    years = tuple(a.year) if a.year else YEARS_WITH_COMMENTS
    out = asyncio.run(run(slug=a.journal, years=years, delay=a.delay,
                          max_hours=a.max_hours, limit=a.limit))
    log.info("FINAL %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
