"""The MediaTracker daemon.

An asyncio websocket service on 127.0.0.1 (algotrade-style). It seeds a journal
row per registered source, runs the scan engine (a single-worker queue fed by
per-journal schedulers, see scanner.py), and exposes a JSON control surface the
local web app uses to configure sources, trigger manual scans, watch progress and
read scan history. Postgres schema is ensured on boot; if Postgres is down the
daemon still runs and mirrors writes to the JSONL store (scheduling/history that
need the DB are limited in that mode).
"""
from __future__ import annotations

import argparse
import asyncio
import logging

import websockets

from . import (alias_candidates, blobserver, db, ids, newcomers, nicknames,
               proximity, sources)
from .config import Config, load_config
from .fetch import Fetcher
from .health import Health
from .images import BlobStore
from .protocol import ProtocolError, error, ok, parse_request
from .scanner import ScanEngine
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
        self.engine: ScanEngine | None = None

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
        else:
            self._seed_journals()

        self.engine = ScanEngine(cfg=self.cfg, conn=self.conn, blobs=self.blobs,
                                 store=self.store, fetcher=self.fetcher, health=self.health)
        await self.engine.start()

        if self.conn is not None:
            blobserver.start(self.cfg, self._blob_lookup)

        async with websockets.serve(self._handle, self.cfg.host, self.cfg.port):
            log.info("MediaTracker listening on ws://%s:%s (journals: %s)",
                     self.cfg.host, self.cfg.port, ", ".join(sources.all_slugs()) or "none")
            await asyncio.Future()  # run forever

    def _seed_journals(self) -> None:
        """Ensure a journal row (with a default schedule) exists for each source.
        Existing rows keep their saved config — only a first-time or empty config
        is initialized to defaults."""
        for slug in sources.all_slugs():
            cls = sources.get(slug)
            src = cls()
            jid = ids.journal_id(slug)
            db.upsert_journal(self.conn, jid=jid, slug=slug, name=src.name,
                              base_url=src.base_url, comment_system=src.comment_system,
                              config=self.cfg.default_schedule(),
                              community=src.community_key)
            if not (db.get_journal_config(self.conn, jid) or {}):
                db.update_journal_config(self.conn, jid, self.cfg.default_schedule())

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
                             last_stats=self.engine.last_stats if self.engine else {}))

        elif cmd == "list_sources":
            await ws.send(ok("list_sources", sources=self._list_sources()))

        elif cmd == "update_source":
            await ws.send(self._update_source(msg))

        elif cmd == "trigger_scan":
            slug = msg.get("journal")
            if not slug or sources.get(slug) is None:
                await ws.send(error(cmd, f"unknown or missing journal {slug!r}"))
                return
            run_id = self.engine.enqueue(slug, "manual")
            await ws.send(ok(cmd, journal=slug, run_id=run_id, queued=self.engine.queue.qsize()))

        elif cmd == "scan_status":
            await ws.send(ok("scan_status",
                             current=self.engine.current if self.engine else None,
                             queued=self.engine.queue.qsize() if self.engine else 0,
                             last_stats=self.engine.last_stats if self.engine else {}))

        elif cmd in ("dataset_stats", "browse_articles", "get_article",
                     "browse_commenters", "get_commenter", "browse_authors",
                     "browse_sources", "list_personas", "get_persona",
                     "create_persona", "add_alias", "remove_alias",
                     "delete_persona", "link_nicks", "alias_candidates",
                     "get_profile", "profile_overview",
                     "findings_overview", "proximity_pairs",
                     "proximity_neighbours", "proximity_timeline",
                     "proximity_calibration", "newcomers_overview",
                     "newcomers_predecessors", "list_notes", "add_note",
                     "update_note", "delete_note", "list_accounts",
                     "add_account", "update_account", "delete_account",
                     "elsewhere_overview"):
            if self.conn is None:
                await ws.send(error(cmd, "degraded: Postgres unavailable"))
                return
            try:
                await ws.send(self._browse(cmd, msg))
            except db.BadPattern as exc:
                await ws.send(error(cmd, f"invalid search pattern: {exc}", bad_pattern=True))

        elif cmd == "scan_history":
            if self.conn is None:
                await ws.send(ok("scan_history", runs=[], degraded=True))
                return
            runs = db.list_scan_runs(self.conn, slug=msg.get("journal"),
                                     limit=int(msg.get("limit", 50)))
            await ws.send(ok("scan_history", runs=runs))

        else:
            await ws.send(error(cmd, f"unknown command {cmd!r}"))

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def _list_sources(self) -> list[dict]:
        out = []
        for slug in sources.all_slugs():
            cls = sources.get(slug)
            sched = self.engine.schedule_of(slug) if self.engine else {}
            inst = self.engine.source_instance(slug) if self.engine else cls()
            out.append({
                "slug": slug,
                "name": cls.name,
                "base_url": inst.base_url,
                "comment_system": cls.comment_system,
                "comments_supported": inst.comments_supported,
                "schedule": sched,
                "next_scan_at": self.engine.next_theoretical(slug) if self.engine else None,
                "last": (self.engine.last_stats.get(slug) if self.engine else None),
            })
        return out

    def _blob_lookup(self, sha256: str):
        """(storage_path, mime) for a blob, or None. Used by the blob HTTP route.
        Runs on the blob server's thread, so it uses its own short-lived cursor."""
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT storage_path, mime FROM image WHERE sha256 = %s", (sha256,))
                return cur.fetchone()
        except Exception as exc:
            log.warning("blob lookup failed for %s: %s", sha256, exc)
            return None

    def _browse(self, cmd: str, msg: dict) -> str:
        """Read-only queries backing the Article Browser subtabs."""
        limit = min(int(msg.get("limit", 100)), 1000)
        offset = int(msg.get("offset", 0))
        q = msg.get("q") or None
        if cmd == "dataset_stats":
            return ok(cmd, **db.dataset_stats(self.conn))
        if cmd == "browse_articles":
            # The window is resolved here, not in the query, so the view can
            # name the days it is actually showing.
            days = int(msg.get("days") or 0)
            since = db.paper_window(days) if days else None
            # An empty list is a real filter — every box unticked — so it must
            # survive the trip as [] and not collapse to "no filter".
            journals = msg.get("journals")
            if not isinstance(journals, list):
                journals = None
            return ok(cmd,
                      since=str(since) if since else None,
                      today=str(db.paper_today()),
                      journals=db.window_journal_counts(self.conn, since) if days else None,
                      articles=db.browse_articles(
                          self.conn, q=q, journals=journals,
                          since=since, limit=limit, offset=offset))
        if cmd == "get_article":
            art = db.get_article(self.conn, msg["article_id"], msg.get("snapshot_id"))
            return ok(cmd, article=art) if art else error(cmd, "article not found")
        # Proximity is O(n²) over the subjects and runs in a fraction of a
        # second at this corpus size; if it ever stops being cheap it wants a
        # cache, not a smaller default.
        if cmd == "proximity_pairs":
            return ok(cmd, **proximity.pairs(
                self.conn, community=msg.get("community") or None,
                min_comments=int(msg.get("min_comments") or 8),
                limit=min(int(msg.get("limit") or 200), 1000),
                succession_only=bool(msg.get("succession_only")),
                cross_community=bool(msg.get("cross_community")),
                sort=msg.get("sort") or "score"))
        if cmd == "proximity_neighbours":
            return ok(cmd, **proximity.neighbours(
                self.conn, kind=msg["kind"], key=str(msg["key"]),
                community=msg.get("community") or None,
                min_comments=int(msg.get("min_comments") or 8),
                limit=min(int(msg.get("limit") or 25), 200)))
        if cmd == "proximity_timeline":
            subjects = msg.get("subjects") or []
            if not isinstance(subjects, list):
                return error(cmd, "subjects must be a list")
            return ok(cmd, **proximity.timeline(
                self.conn, subjects[:12], bucket=msg.get("bucket") or "month"))
        # Arrivals are measured from the comments, not from author_profile:
        # the accounts this answers about turned up after the last LLM pass and
        # would not be in that table yet.
        if cmd == "newcomers_overview":
            # A window in days is resolved on the papers' calendar, like the
            # article browser's. Left unset, the module picks the first day it
            # can defend instead of a round number of days.
            days = int(msg.get("days") or 0)
            since = msg.get("since") or (str(db.paper_window(days)) if days else None)
            return ok(cmd, **newcomers.overview(
                self.conn, community=msg.get("community") or "lematin",
                since=since,
                min_comments=int(msg.get("min_comments") or 3),
                limit=min(int(msg.get("limit") or 200), 1000)))
        if cmd == "newcomers_predecessors":
            return ok(cmd, **newcomers.predecessors(
                self.conn, community=msg.get("community") or "lematin",
                kind=msg["kind"], key=str(msg["key"]),
                min_gap_days=float(msg.get("min_gap_days") or 0.5),
                min_comments=int(msg.get("min_comments") or 3),
                observed_only=bool(msg.get("observed_only")),
                sort=msg.get("sort") or "lexical",
                limit=min(int(msg.get("limit") or 12), 100)))
        if cmd == "proximity_calibration":
            return ok(cmd, **proximity.calibrate(
                self.conn, min_comments=int(msg.get("min_comments") or 8)))
        if cmd == "browse_commenters":
            rows = db.browse_commenters(self.conn, q=q, limit=limit, offset=offset)
            return ok(cmd, commenters=nicknames.annotate(rows),
                      note_counts=db.note_counts(self.conn),
                      reference_coverage=nicknames.coverage(
                          r["nick"] for r in rows))
        if cmd == "get_commenter":
            return ok(cmd, **db.get_commenter(self.conn, msg["nick"], limit=limit))
        if cmd == "browse_authors":
            return ok(cmd, authors=db.browse_authors(self.conn, limit=limit))
        if cmd == "browse_sources":
            return ok(cmd, sources=db.browse_sources(self.conn, limit=limit))
        if cmd == "findings_overview":
            return ok(cmd, **db.findings_overview(self.conn))
        if cmd == "profile_overview":
            return ok(cmd, **db.profile_overview(self.conn, community=msg.get("community")))
        if cmd == "get_profile":
            pid = msg.get("persona_id")
            prof = db.get_profile(self.conn, nick=msg.get("nick"),
                                  persona_id=int(pid) if pid is not None else None,
                                  community=msg.get("community"))
            if prof:
                # A persona's own label may say nothing while one of its
                # handles carries the whole reference, so the aliases are
                # offered to the reading too.
                aliases = (db.persona_alias_nicks(self.conn, prof["subject_key"])
                           if prof.get("subject_kind") == "persona" else
                           [msg.get("nick")] if msg.get("nick") else [])
                prof["__aliases__"] = aliases
                nicknames.annotate([prof], field="label", aliases="__aliases__")
                prof.pop("__aliases__", None)
            return ok(cmd, profile=prof)
        if cmd in ("list_notes", "add_note", "update_note", "delete_note"):
            return self._notes(cmd, msg)
        if cmd == "elsewhere_overview":
            return ok(cmd, **db.elsewhere_overview(self.conn, msg.get("community")))
        if cmd in ("list_accounts", "add_account", "update_account", "delete_account"):
            return self._accounts(cmd, msg)
        return self._personas(cmd, msg, limit)

    @staticmethod
    def _subject(msg: dict) -> tuple[str, str]:
        """Which subject a request is about, keyed as author_profile keys it."""
        pid = msg.get("persona_id")
        if pid is not None:
            return "persona", str(pid)
        return "nick", str(msg["nick"])

    def _notes(self, cmd: str, msg: dict) -> str:
        """Hand-written notes on a subject.

        Every branch answers with the subject's whole note list rather than
        with what it just changed: there is no useful partial state here, and
        one shape means the view never has to merge a response into what it
        already had.
        """
        if cmd in ("update_note", "delete_note"):
            # An edit names a note, not a subject — the subject is looked up
            # from the note so a stale view cannot move one person's note onto
            # another's page.
            nid = int(msg["note_id"])
            found = db.note_subject(self.conn, nid)
            if found is None:
                return error(cmd, "note not found")
            kind, key, community = found
            if cmd == "delete_note":
                db.delete_note(self.conn, nid)
            else:
                db.update_note(self.conn, nid, body=msg.get("body"),
                               source=msg.get("source"))
        else:
            kind, key = self._subject(msg)
            community = msg.get("community") or db.subject_community(
                self.conn, kind=kind, key=key)
            if cmd == "add_note":
                db.add_note(self.conn, kind=kind, key=key, community=community,
                            body=msg.get("body"), source=msg.get("source"))
        return ok(cmd, kind=kind, key=key, community=community,
                  notes=db.list_notes(self.conn, kind=kind, key=key,
                                      community=community))

    def _accounts(self, cmd: str, msg: dict) -> str:
        """The same writer on networks we do not collect. Same shape and same
        subject rules as `_notes`."""
        fields = dict(url=msg.get("url"), platform=msg.get("platform"),
                      handle=msg.get("handle"),
                      confidence=msg.get("confidence") or "confirmed",
                      evidence=msg.get("evidence"))
        if cmd in ("update_account", "delete_account"):
            aid = int(msg["account_id"])
            found = db.account_subject(self.conn, aid)
            if found is None:
                return error(cmd, "account not found")
            kind, key, community = found
            if cmd == "delete_account":
                db.delete_account(self.conn, aid)
            else:
                db.update_account(self.conn, aid, **fields)
        else:
            kind, key = self._subject(msg)
            community = msg.get("community") or db.subject_community(
                self.conn, kind=kind, key=key)
            if cmd == "add_account":
                db.add_account(self.conn, kind=kind, key=key,
                               community=community, **fields)
        return ok(cmd, kind=kind, key=key, community=community,
                  accounts=db.list_accounts(self.conn, kind=kind, key=key,
                                            community=community))

    def _personas(self, cmd: str, msg: dict, limit: int) -> str:
        """Identity layer: group the nicknames of one person into a persona so
        analysis runs on the person rather than on each pseudonym."""
        if cmd == "alias_candidates":
            # Nicknames already claimed by a persona, so the GUI can mark them.
            with self.conn.cursor() as cur:
                cur.execute("SELECT nick FROM persona_alias")
                linked = {r[0] for r in cur.fetchall()}
            strong, weak = alias_candidates.find_groups(
                self.conn, int(msg.get("min_comments", 2)), linked=linked)
            return ok(cmd, strong=strong, weak=weak, linked=sorted(linked))
        if cmd == "list_personas":
            rows = db.list_personas(self.conn)
            return ok(cmd, personas=nicknames.annotate(
                rows, field="label", aliases="aliases"),
                reference_coverage=nicknames.coverage(r["label"] for r in rows))
        if cmd == "get_persona":
            p = db.get_persona(self.conn, int(msg["persona_id"]), limit=limit or 3000)
            return ok(cmd, persona=p) if p else error(cmd, "persona not found")
        if cmd == "create_persona":
            pid = db.create_persona(self.conn, label=msg["label"], note=msg.get("note"),
                                    community=msg.get("community") or "lematin")
            return ok(cmd, persona_id=pid)
        if cmd == "add_alias":
            db.add_alias(self.conn, persona_id=int(msg["persona_id"]), nick=msg["nick"],
                         journal_slug=msg.get("journal_slug", "*"),
                         confidence=msg.get("confidence", "confirmed"),
                         evidence=msg.get("evidence"),
                         added_by=msg.get("added_by", "manual"))
            return ok(cmd, persona=db.get_persona(self.conn, int(msg["persona_id"]), limit=0))
        if cmd == "remove_alias":
            db.remove_alias(self.conn, nick=msg["nick"],
                            journal_slug=msg.get("journal_slug", "*"))
            return ok(cmd, nick=msg["nick"])
        if cmd == "delete_persona":
            db.delete_persona(self.conn, int(msg["persona_id"]))
            return ok(cmd, persona_id=msg["persona_id"])

        # link_nicks: create-or-reuse a persona and attach several nicknames at once.
        nicks = msg.get("nicks") or []
        if not nicks:
            return error(cmd, "no nicks given")
        pid = msg.get("persona_id")
        # A new person belongs to the public their handles write in, not to a
        # default: linking two 24 heures nicknames must not mint a Le Matin
        # persona that no query for that community will ever find.
        pid = int(pid) if pid else db.create_persona(
            self.conn, label=msg.get("label") or nicks[0], note=msg.get("note"),
            community=msg.get("community") or db.subject_community(
                self.conn, kind="nick", key=nicks[0]))
        for n in nicks:
            db.add_alias(self.conn, persona_id=pid, nick=n,
                         confidence=msg.get("confidence", "confirmed"),
                         evidence=msg.get("evidence"),
                         added_by=msg.get("added_by", "manual"))
        return ok(cmd, persona=db.get_persona(self.conn, pid, limit=0))

    def _update_source(self, msg: dict) -> str:
        slug = msg.get("journal")
        if not slug or sources.get(slug) is None:
            return error("update_source", f"unknown or missing journal {slug!r}")
        if self.conn is None:
            return error("update_source", "degraded: cannot persist config without Postgres")
        patch = msg.get("schedule") or {}
        allowed = {"enabled", "base_url", "scan_start_local", "scan_period_hours",
                   "scan_variability_hours", "timezone"}
        jid = ids.journal_id(slug)
        current = db.get_journal_config(self.conn, jid) or self.cfg.default_schedule()
        merged = dict(current)
        for k, v in patch.items():
            if k in allowed:
                merged[k] = v
        db.update_journal_config(self.conn, jid, merged)
        log.info("[%s] schedule updated: %s", slug, merged)
        return ok("update_source", journal=slug, schedule=merged,
                  next_scan_at=self.engine.next_theoretical(slug) if self.engine else None)


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
