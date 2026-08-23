"""The MediaTracker daemon.

An asyncio websocket service on 127.0.0.1 (algotrade-style): a JSON control
surface plus one long-lived poll loop per configured journal that re-checks the
journal every `poll_interval_hours`, staggered at startup. Postgres schema is
ensured on boot; if Postgres is down the daemon still runs and mirrors writes to
the JSONL store.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

import websockets

from . import db, sources
from .config import Config, load_config
from .fetch import Fetcher
from .health import Health
from .images import BlobStore
from .pipeline import Pipeline
from .protocol import ProtocolError, error, ok, parse_request
from .store import JsonlStore

log = logging.getLogger(__name__)


class Server:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        # "-journals" distinguishes this from AlgoTrade's unrelated "mediatracker"
        # daemon in health output and logs (both run as `python3 -m mediatracker`).
        self.health = Health("mediatracker-journals")
        self.fetcher = Fetcher(cfg)
        self.blobs = BlobStore(cfg.blob_path)
        self.store = JsonlStore(cfg.jsonl_path)
        self.conn = None
        self._tasks: list[asyncio.Task] = []
        self._ingest_lock = asyncio.Lock()  # never two ingests at once
        self._last_stats: dict[str, dict] = {}

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #

    async def run(self) -> None:
        self.cfg.blob_path.mkdir(parents=True, exist_ok=True)
        self.cfg.jsonl_path.mkdir(parents=True, exist_ok=True)

        self.conn = db.connect(self.cfg)
        db.ensure_schema(self.conn)
        if self.conn is None:
            log.warning("running DEGRADED: Postgres unavailable, using JSONL store only")

        for slug in sources.all_slugs():
            self.health.attach_loop(f"poll:{slug}")
            self._tasks.append(asyncio.create_task(self._poll_loop(slug)))

        async with websockets.serve(self._handle, self.cfg.host, self.cfg.port):
            log.info("MediaTracker listening on ws://%s:%s (journals: %s)",
                     self.cfg.host, self.cfg.port, ", ".join(sources.all_slugs()) or "none")
            await asyncio.Future()  # run forever

    # ------------------------------------------------------------------ #
    # poll loop
    # ------------------------------------------------------------------ #

    async def _poll_loop(self, slug: str) -> None:
        name = f"poll:{slug}"
        # Stagger initial runs so all journals don't fire simultaneously.
        idx = sources.all_slugs().index(slug)
        await asyncio.sleep(self.cfg.startup_stagger_seconds * (idx + 1))
        interval = self.cfg.poll_interval_hours * 3600.0
        while True:
            try:
                await self._ingest_one(slug)
            except Exception as exc:  # a loop must never die
                self.health.loop_error(name)
                log.exception("[%s] poll iteration failed: %s", slug, exc)
            self.health.beat(name)
            await asyncio.sleep(interval)

    async def _ingest_one(self, slug: str) -> dict:
        cls = sources.get(slug)
        if cls is None:
            raise ValueError(f"unknown journal {slug!r}")
        async with self._ingest_lock:
            pipeline = Pipeline(conn=self.conn, blobs=self.blobs,
                                store=self.store, fetcher=self.fetcher)
            stats = await pipeline.ingest_journal(cls())
        result = {
            "articles_seen": stats.articles_seen,
            "article_snapshots": stats.article_snapshots,
            "comments_seen": stats.comments_seen,
            "comment_snapshots": stats.comment_snapshots,
            "images_new": stats.images_new,
            "errors": stats.errors,
        }
        self._last_stats[slug] = result
        log.info("[%s] ingest done: %s", slug, result)
        return result

    # ------------------------------------------------------------------ #
    # websocket control surface
    # ------------------------------------------------------------------ #

    async def _handle(self, ws) -> None:
        async for raw in ws:
            try:
                msg = parse_request(raw)
            except ProtocolError as exc:
                await ws.send(error("?", str(exc)))
                continue
            cmd = msg["cmd"]
            try:
                await self._dispatch(ws, cmd, msg)
            except Exception as exc:
                log.exception("command %s failed: %s", cmd, exc)
                await ws.send(error(cmd, str(exc)))

    async def _dispatch(self, ws, cmd: str, msg: dict) -> None:
        if cmd == "ping":
            await ws.send(ok("ping", pong=True))
        elif cmd == "health":
            await ws.send(ok("health", **self.health.verdict(
                stale_after_s=self.cfg.poll_interval_hours * 3600.0 * 2)))
        elif cmd == "status":
            await ws.send(ok("status",
                             degraded=self.conn is None,
                             journals=sources.all_slugs(),
                             last_stats=self._last_stats))
        elif cmd == "ingest_now":
            slug = msg.get("journal")
            slugs = [slug] if slug else sources.all_slugs()
            results = {}
            for s in slugs:
                results[s] = await self._ingest_one(s)
            await ws.send(ok("ingest_now", results=results))
        else:
            await ws.send(error(cmd, f"unknown command {cmd!r}"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mediatracker", description="MediaTracker daemon")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--config", default=None, help="path to config.toml")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    cfg = load_config(args.config)
    if args.host:
        cfg = _replace(cfg, host=args.host)
    if args.port:
        cfg = _replace(cfg, port=args.port)

    try:
        asyncio.run(Server(cfg).run())
    except KeyboardInterrupt:
        log.info("shutting down")
    return 0


def _replace(cfg: Config, **kw):
    from dataclasses import replace
    return replace(cfg, **kw)
