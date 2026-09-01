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

**2017-2020 had comments; they are simply not in the HTML.** I first read the
evidence as the paper having closed commenting, and Cedric corrected it: he was
reading those threads at the time, and the gap in his printed archive is a gap
in his printing, not in the public. The mechanism was different — comments
rendered by JavaScript from a live database rather than served in the page — so
an archive capture holds an empty container, and today's article page reports
zero because the count it prints belongs to the *current* comment system, which
those comments were never migrated into. Recovering them means finding the
system that held them, not fetching more pages.

The run starts at 2021 because that is where the present platform's own data
begins, not because the earlier years are empty.

**The sisters publish one too, and in a different shape.** 24 heures and the
Tribune name a few hundred sub-sitemaps by opaque hash instead of one file per
day, each holding up to ~2,000 URLs and spanning weeks or years; only the
`<lastmod>` on each record says when. 24 heures mirrors to 424,672 URLs running
2011-2026 — the same order as Le Matin. That matters because the archive route
for those two titles is thin and stops dead at 2016, which is the only reason
cross-title comparison has been bounded to 2012-2016. The bound is the
archive's, not the corpus's.

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
# One <url> record, from which both the address and its date are read. The
# sisters need this; Le Matin does not, because its filename IS the date.
_ENTRY = re.compile(r"<url>(.*?)</url>", re.S)
_LASTMOD = re.compile(r"<lastmod>(\d{4}-\d{2}-\d{2})")

# The years the CURRENT comment platform holds. 2017-2020 are excluded from
# this route only because their comments live somewhere this route cannot
# reach — a JavaScript widget backed by another database — not because they do
# not exist. See the module docstring; finding that system is open work.
YEARS_ON_CURRENT_PLATFORM = tuple(range(2021, 2027))
YEARS_WITH_COMMENTS = YEARS_ON_CURRENT_PLATFORM


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


def entries_in(path: Path) -> list[tuple[str, str | None]]:
    """(url, YYYY-MM-DD) for one sitemap, the date from its own <lastmod>.

    24 heures and the Tribune publish a few hundred sub-sitemaps under opaque
    hashed names rather than one file per day, each spanning weeks or years,
    so for them the filename says nothing and only the record carries a date.
    A record without one keeps None: undated is not the same as unknown-year,
    and pretending otherwise would file it under whichever year we guessed.
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("unreadable sitemap %s: %s", path, exc)
        return []
    out = []
    for block in _ENTRY.findall(raw):
        loc = _LOC.search(block)
        if not loc:
            continue
        mod = _LASTMOD.search(block)
        out.append((loc.group(1), mod.group(1) if mod else None))
    return out


def hashed_files(slug: str) -> list[Path]:
    """Sub-sitemaps that are not named for a day. `_`-prefixed files are ours."""
    root = SITEMAP_DIR / slug
    return sorted(p for p in root.glob("*.xml")
                  if not p.name.startswith("_") and not _DAY.match(p.name))


def day_index(slug: str) -> Path:
    """A date-sorted `day<TAB>url` index over a hashed mirror, built once.

    Sorting 400,000 records newest-first means reading every sub-sitemap, which
    is slow enough to be worth doing once rather than on every resume. Rebuilt
    whenever a sub-sitemap is newer than the index, so a re-mirrored title is
    picked up without anyone remembering to say so.
    """
    root = SITEMAP_DIR / slug
    idx = root / "_bydate.tsv"
    files = hashed_files(slug)
    if idx.exists() and files and idx.stat().st_mtime >= max(f.stat().st_mtime for f in files):
        return idx
    rows: list[tuple[str, str | None]] = []
    for f in files:
        rows.extend(entries_in(f))
    # Undated records sort last rather than being dropped: they are still real
    # articles, and a year filter will pass over them on its own.
    rows.sort(key=lambda r: r[1] or "", reverse=True)
    idx.write_text("".join(f"{d or ''}\t{u}\n" for u, d in rows), encoding="utf-8")
    log.info("[%s] built day index: %d urls across %d sub-sitemaps",
             slug, len(rows), len(files))
    return idx


def iter_entries(slug: str, years: tuple[int, ...] | None = None):
    """(url, day) for a title, newest first, whichever layout it publishes."""
    dated = day_files(slug, years)
    if dated:
        for path in dated:
            for url in urls_in(path):
                yield url, path.stem
        return
    for line in day_index(slug).read_text(encoding="utf-8").splitlines():
        day, _, url = line.partition("\t")
        if not url:
            continue
        if years and (not day or int(day[:4]) not in years):
            continue
        yield url, day or None


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

    last_day = None
    try:
        for url, day in iter_entries(slug, years):
            # A day boundary is a natural commit point in either layout, and
            # in the hashed one it is the only boundary there is.
            if day != last_day:
                conn.commit()
                last_day = day
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
