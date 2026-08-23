"""Scan scheduling and execution.

Design:
  * One FIFO **queue** of scan requests and a **single worker** — so if several
    journals are due at the same instant, their scans serialize through one
    pipeline instead of hammering the network in parallel.
  * One **scheduler task per journal** computes the next theoretical fire time
    from its schedule (start-of-day + period through the day) and adds a random
    jitter of ±variability hours so access does not look robotic. Scheduled
    requests carry that jitter; **manual** requests are enqueued immediately with
    no offset.
  * A shared **progress** object reflects the currently running scan for the GUI
    progress bar. Each finished scan is recorded in `scan_run`.

Schedules live in each journal's `journal.config` (edited from the GUI); defaults
come from Config.default_schedule().
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import db, ids, sources
from .pipeline import IngestStats, Pipeline

log = logging.getLogger(__name__)

_UTC = ZoneInfo("UTC")


@dataclass
class ScanRequest:
    slug: str
    trigger: str            # 'manual' | 'scheduled'
    run_id: int | None = None


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        try:
            return ZoneInfo("Europe/Zurich")
        except Exception:
            return _UTC


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        h, m = value.split(":", 1)
        return max(0, min(23, int(h))), max(0, min(59, int(m)))
    except Exception:
        return 6, 0


class ScanEngine:
    def __init__(self, *, cfg, conn, blobs, store, fetcher, health) -> None:
        self.cfg = cfg
        self.conn = conn
        self.blobs = blobs
        self.store = store
        self.fetcher = fetcher
        self.health = health
        self.queue: asyncio.Queue[ScanRequest] = asyncio.Queue()
        self.current: dict | None = None       # progress of the running scan
        self.last_stats: dict[str, dict] = {}   # slug -> last result summary
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------ #
    # schedule access
    # ------------------------------------------------------------------ #

    def schedule_of(self, slug: str) -> dict:
        """Effective schedule dict for a journal (DB config over defaults)."""
        sched = dict(self.cfg.default_schedule())
        if self.conn is not None:
            cfg = db.get_journal_config(self.conn, ids.journal_id(slug))
            if isinstance(cfg, dict):
                sched.update({k: v for k, v in cfg.items() if v is not None or k == "base_url"})
        return sched

    def source_instance(self, slug: str):
        cls = sources.get(slug)
        if cls is None:
            raise ValueError(f"unknown journal {slug!r}")
        base = self.schedule_of(slug).get("base_url")
        return cls(base_url=base) if base else cls()

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._worker()))
        for i, slug in enumerate(sources.all_slugs()):
            self.health.attach_loop(f"scan:{slug}")
            self._tasks.append(asyncio.create_task(self._schedule_loop(slug, i)))

    def enqueue(self, slug: str, trigger: str) -> int | None:
        """Queue a scan. Returns the scan_run id (None when degraded)."""
        if sources.get(slug) is None:
            raise ValueError(f"unknown journal {slug!r}")
        run_id = None
        if self.conn is not None:
            run_id = db.create_scan_run(self.conn, journal_id=ids.journal_id(slug),
                                        slug=slug, trigger=trigger)
        self.queue.put_nowait(ScanRequest(slug, trigger, run_id))
        log.info("[%s] %s scan queued (run_id=%s, queue=%d)",
                 slug, trigger, run_id, self.queue.qsize())
        return run_id

    # ------------------------------------------------------------------ #
    # worker
    # ------------------------------------------------------------------ #

    async def _worker(self) -> None:
        while True:
            req = await self.queue.get()
            try:
                await self._run_scan(req)
            except Exception as exc:  # the worker must never die
                log.exception("[%s] scan crashed: %s", req.slug, exc)
            finally:
                self.queue.task_done()

    async def _run_scan(self, req: ScanRequest) -> None:
        self.current = {
            "slug": req.slug, "trigger": req.trigger, "run_id": req.run_id,
            "phase": "starting", "current": 0, "total": None,
            "started_at": datetime.now(_UTC).isoformat(),
        }
        if self.conn is not None and req.run_id is not None:
            db.start_scan_run(self.conn, req.run_id)

        def on_progress(done: int, total: int) -> None:
            if self.current is not None:
                self.current["current"] = done
                self.current["total"] = total
                self.current["phase"] = "scanning"

        pipeline = Pipeline(conn=self.conn, blobs=self.blobs,
                            store=self.store, fetcher=self.fetcher)
        status, note, stats = "done", None, IngestStats()
        try:
            src = self.source_instance(req.slug)
            stats = await pipeline.ingest_journal(
                src, since_days=self.cfg.active_rescan_days, on_progress=on_progress)
        except Exception as exc:
            status, note = "error", str(exc)
            log.exception("[%s] scan failed: %s", req.slug, exc)

        result = {
            "articles_seen": stats.articles_seen,
            "article_snapshots": stats.article_snapshots,
            "comments_seen": stats.comments_seen,
            "comment_snapshots": stats.comment_snapshots,
            "images_new": stats.images_new,
            "errors": stats.errors,
        }
        self.last_stats[req.slug] = {**result, "status": status,
                                     "at": datetime.now(_UTC).isoformat()}
        if self.conn is not None and req.run_id is not None:
            db.finish_scan_run(self.conn, req.run_id, status=status, stats=result, note=note)
        self.health.beat(f"scan:{req.slug}")
        log.info("[%s] scan %s: %s", req.slug, status, result)
        self.current = None

    # ------------------------------------------------------------------ #
    # scheduler
    # ------------------------------------------------------------------ #

    async def _schedule_loop(self, slug: str, idx: int) -> None:
        # Small startup stagger so schedulers don't all wake at once.
        await asyncio.sleep(self.cfg.startup_stagger_seconds * (idx + 1))
        while True:
            sched = self.schedule_of(slug)
            if not sched.get("enabled", True):
                await asyncio.sleep(300)  # re-check enable flag periodically
                continue
            fire_at = self._next_fire(sched)
            delay = (fire_at - datetime.now(_UTC)).total_seconds()
            log.info("[%s] next scheduled scan at %s (in %.0f min)",
                     slug, fire_at.isoformat(timespec="minutes"), delay / 60)
            await asyncio.sleep(max(1.0, delay))
            if self.schedule_of(slug).get("enabled", True):
                self.enqueue(slug, "scheduled")
            # Loop again to compute the next fire time.

    def next_theoretical(self, slug: str) -> str | None:
        """Next theoretical fire time (no jitter) for the GUI, as ISO UTC."""
        sched = self.schedule_of(slug)
        if not sched.get("enabled", True):
            return None
        return self._next_fire(sched, jitter=False).isoformat()

    def _next_fire(self, sched: dict, *, jitter: bool = True) -> datetime:
        tz = _tz(sched.get("timezone") or "Europe/Zurich")
        now = datetime.now(tz)
        h, m = _parse_hhmm(sched.get("scan_start_local") or "06:00")
        period_h = float(sched.get("scan_period_hours") or 24) or 24
        period = timedelta(hours=period_h)

        anchor = now.replace(hour=h, minute=m, second=0, microsecond=0)
        # Step the daily anchor forward by whole periods to the first slot > now.
        t = anchor
        if t <= now:
            steps = math.floor((now - anchor) / period) + 1
            t = anchor + steps * period
        while t <= now:
            t += period

        if jitter:
            v = float(sched.get("scan_variability_hours") or 0)
            if v > 0:
                t = t + timedelta(hours=random.uniform(-v, v))
            if t <= now:
                t = now + timedelta(seconds=30)
        return t.astimezone(_UTC)
