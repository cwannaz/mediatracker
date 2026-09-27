"""What central-control shows about MediaTracker: the CC-API v1 endpoints.

central-control (http://127.0.0.1:29000) is one dashboard over every project on
this workstation. It already reads systemd unit state, backup state and disk
use from ryzen-control, so none of that is repeated here. What only this
project knows is whether the crawl is actually bringing anything back, and how
far the reading of the corpus has got -- a unit can be `active (running)` while
nothing has arrived for six hours, which is exactly what happened on 2026-09-25
when a stale cookie jar sent every request into a redirect loop.

Two constraints shape the module:

* `status` is polled every 30 s and must answer in under a second. The counts
  behind it cost ~3 s (search_doc holds 5M rows), so they are NOT taken on the
  request path: a background thread refreshes a snapshot and the handler serves
  whatever it last stored, with its age. Nothing here touches Postgres, the
  network, or the daemon's event loop while answering a GET.
* `/cc/v1` has no login. It is loopback-only, enforced in `api.py` against a
  reverse proxy as well as against the LAN.

The live half of the report -- the scan queue, the running scan, the last
result per journal -- needs no query at all: this module is imported by the
daemon that owns the scan engine, and `DaemonView` reads it from memory.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from . import db, entities, sources

log = logging.getLogger(__name__)

PROJECT_ID = "mediatracker"          # must equal the relay name
REVISION = "2026-09-26.1"            # bump on every manifest change

# How often the snapshot thread re-reads Postgres. The cheap half is per-minute
# because it carries the crawl's freshness; the counts move slowly and cost a
# hundred times more, so they go once per ten minutes.
CHEAP_INTERVAL_S = 60.0
HEAVY_INTERVAL_S = 600.0

# A journal is scanned about every 4 h (scan_period_hours, with jitter). Twice
# that is a quiet spell; a fifth of a day is something being wrong.
CRAWL_WARN_S = 8 * 3600
CRAWL_ERROR_S = 18 * 3600
# Scans take 4-26 min. One still running after two hours is stuck, not slow.
SCAN_STUCK_S = 2 * 3600
# The snapshot is stale if its own thread has not come round.
SNAPSHOT_STALE_S = 15 * 60

_LEVEL_ORDER = {"ok": 0, "info": 1, "unknown": 2, "warn": 3, "error": 4}


def _worst(levels) -> str:
    return max(levels, key=lambda l: _LEVEL_ORDER.get(l, 0), default="ok")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _iso(when) -> str | None:
    if when is None:
        return None
    if isinstance(when, str):
        return when
    if when.tzinfo is None:
        when = when.astimezone()
    return when.isoformat(timespec="seconds")


def _age_s(when) -> float | None:
    if when is None:
        return None
    if isinstance(when, str):
        try:
            when = datetime.fromisoformat(when)
        except ValueError:
            return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds()


def _human_age(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    if seconds < 90:
        return f"{int(seconds)} s ago"
    if seconds < 5400:
        return f"{int(seconds / 60)} min ago"
    if seconds < 36 * 3600:
        return f"{seconds / 3600:.1f} h ago"
    return f"{int(seconds / 86400)} d ago"


# --------------------------------------------------------------------------- #
# the snapshot
# --------------------------------------------------------------------------- #

class Snapshot:
    """The last successful read of Postgres, plus when it happened.

    Written by one background thread, read by the HTTP workers. The lock is
    held only to swap in a finished dict, never across a query.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict = {}
        self.cheap_at: float | None = None
        self.heavy_at: float | None = None
        self.error: str | None = None

    def put(self, part: dict, *, heavy: bool) -> None:
        with self._lock:
            self._data = {**self._data, **part}
            if heavy:
                self.heavy_at = time.time()
            else:
                self.cheap_at = time.time()
            self.error = None

    def fail(self, exc: BaseException) -> None:
        with self._lock:
            self.error = str(exc) or exc.__class__.__name__

    def read(self) -> tuple[dict, float | None, str | None]:
        with self._lock:
            return dict(self._data), self.cheap_at, self.error


def read_cheap(conn) -> dict:
    """The fast half: the crawl's state, and the counts that cost nothing."""
    out: dict = {}
    with conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT ON (slug) slug, status, requested_at,
                              finished_at, articles_seen, article_snapshots,
                              comments_seen, comment_snapshots, images_new,
                              errors, note
                       FROM scan_run ORDER BY slug, requested_at DESC""")
        out["last_run"] = [
            {"slug": r[0], "status": r[1], "requested_at": _iso(r[2]),
             "finished_at": _iso(r[3]), "articles_seen": r[4],
             "article_snapshots": r[5], "comments_seen": r[6],
             "comment_snapshots": r[7], "images_new": r[8], "errors": r[9],
             "note": r[10]}
            for r in cur.fetchall()]

        # The last COMPLETED run per journal, which is what freshness means: a
        # run that started and never finished says the opposite of health.
        cur.execute("""SELECT DISTINCT ON (slug) slug, finished_at, status,
                              articles_seen, comment_snapshots
                       FROM scan_run WHERE finished_at IS NOT NULL
                       ORDER BY slug, finished_at DESC""")
        out["last_done"] = {r[0]: {"at": _iso(r[1]), "status": r[2],
                                   "articles_seen": r[3],
                                   "comment_snapshots": r[4]}
                            for r in cur.fetchall()}

        cur.execute("""SELECT slug, status, requested_at, finished_at,
                              articles_seen, comment_snapshots, images_new, errors
                       FROM scan_run ORDER BY requested_at DESC LIMIT 20""")
        out["recent_runs"] = [
            {"slug": r[0], "status": r[1], "at": _iso(r[3] or r[2]),
             "articles_seen": r[4], "comment_snapshots": r[5],
             "images_new": r[6], "errors": r[7]}
            for r in cur.fetchall()]

        cur.execute("SELECT max(fetched_at) FROM article_snapshot")
        out["newest_article_fetch"] = _iso(cur.fetchone()[0])
        cur.execute("SELECT max(fetched_at) FROM comment_snapshot")
        out["newest_comment_fetch"] = _iso(cur.fetchone()[0])

        # The archive backfill, counted the way the backfill itself counts:
        # a snapshot it has already asked about is done, whatever the answer.
        cur.execute("""
            SELECT count(*) FILTER (WHERE s.body_text IS NOT NULL
                                      AND length(s.body_text) >= 200),
                   count(*) FILTER (WHERE (s.body_text IS NULL
                                           OR length(s.body_text) < 200)
                                      AND s.raw_meta->>'capture' IS NOT NULL
                                      AND NOT (s.raw_meta ? 'body_backfill')),
                   count(*)
            FROM article_snapshot s JOIN article a ON a.id = s.article_id
            WHERE a.origin = 'wayback'""")
        have, todo, total = cur.fetchone()
        out["bodies"] = {"with_text": have, "not_attempted": todo, "total": total}

        cur.execute("SELECT count(*) FROM author_profile")
        out["profiles"] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM persona")
        out["personas"] = cur.fetchone()[0]
        cur.execute("""SELECT count(*) FROM entity_done
                       WHERE read_at > now() - interval '1 hour'""")
        out["read_last_hour"] = cur.fetchone()[0]
    return out


def read_heavy(conn) -> dict:
    """The slow half: counts over the whole corpus. ~3 s, hence ten-minutely."""
    out: dict = {}
    with conn.cursor() as cur:
        cur.execute("SELECT kind, count(*) FROM search_doc GROUP BY kind")
        out["documents"] = {k: n for k, n in cur.fetchall()}
        cur.execute("""SELECT min(published_at)::date, max(published_at)::date
                       FROM search_doc WHERE published_at IS NOT NULL""")
        lo, hi = cur.fetchone()
        out["span"] = {"earliest": lo.isoformat() if lo else None,
                       "latest": hi.isoformat() if hi else None}
    out["coverage"] = entities.coverage(conn)
    return out


def refresh_loop(cfg, snap: Snapshot, stop: threading.Event) -> None:
    """Keep the snapshot current, forever, on its own connection.

    Its own connection because the API's are thread-local to request workers
    and the daemon's belongs to the event loop. A failure here is reported
    through the snapshot rather than raised: central-control would rather see
    "Postgres not answering" than nothing at all.
    """
    conn = None
    heavy_at = 0.0
    while not stop.is_set():
        try:
            if conn is None:
                conn = db.connect(cfg)
            if conn is None:
                raise RuntimeError("Postgres unavailable")
            snap.put(read_cheap(conn), heavy=False)
            if time.time() - heavy_at >= HEAVY_INTERVAL_S:
                snap.put(read_heavy(conn), heavy=True)
                heavy_at = time.time()
        except Exception as exc:
            log.warning("cc-api snapshot failed: %s", exc)
            snap.fail(exc)
            try:
                if conn is not None:
                    conn.rollback()
            except Exception:
                conn = None                      # take a fresh one next round
        stop.wait(CHEAP_INTERVAL_S)


# --------------------------------------------------------------------------- #
# the daemon's live state
# --------------------------------------------------------------------------- #

class DaemonView:
    """The scan engine as seen from an HTTP worker thread.

    Reading the engine's attributes is a plain dict/int read, safe enough
    across threads for a dashboard. Writing is not: `enqueue` touches an
    asyncio.Queue and the daemon's connection, so a scan is started on the
    event loop and waited for, briefly.
    """

    def __init__(self, server, loop) -> None:
        self.server = server
        self.loop = loop

    def live(self) -> dict:
        eng = getattr(self.server, "engine", None)
        cur = getattr(eng, "current", None)
        return {
            "degraded": getattr(self.server, "conn", None) is None,
            "queue": eng.queue.qsize() if eng is not None else 0,
            "current": dict(cur) if cur else None,
            "last_stats": dict(getattr(eng, "last_stats", {}) or {}),
            "journals": list(sources.all_slugs()),
        }

    def trigger_scan(self, slug: str) -> int | None:
        async def go():
            return self.server.engine.enqueue(slug, "manual")
        return asyncio.run_coroutine_threadsafe(go(), self.loop).result(timeout=10)


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #

def manifest(*, journals=()) -> dict:
    slugs = list(journals) or list(sources.all_slugs())
    return {
        "schema": "cc.manifest/1",
        "revision": REVISION,
        "project": {
            "id": PROJECT_ID,
            "title": "mediatracker",
            "summary": "Crawls three Swiss French-language papers and studies "
                       "their commenting public: articles, pictures, comments, "
                       "2007 to now.",
            "host": "ryzen",
            "links": [
                {"label": "Web app", "url": "http://127.0.0.1:55080/"},
                {"label": "Public API", "url": "http://127.0.0.1:55032/v1/"},
            ],
        },
        "poll": {"status_interval_s": 30},
        "widgets": [
            {"id": "comments", "type": "stat", "title": "Comments",
             "placement": ["dashboard", "tab:corpus"], "history": True,
             "help": "Reader comments stored, all three papers."},
            {"id": "crawl", "type": "status_list", "title": "Crawl",
             "placement": ["dashboard", "tab:crawl"],
             "help": "Last completed scan per paper. A paper scans about "
                     "every four hours."},
            {"id": "entity-reading", "type": "progress", "title": "Entity reading",
             "placement": ["dashboard", "tab:analysis"], "history": True,
             "help": "Articles with a real body that Claude has read for "
                     "named entities. Title-only stubs are excluded on purpose."},
            {"id": "bodies", "type": "stat", "title": "Archive bodies to fetch",
             "placement": ["dashboard", "tab:crawl"], "history": True,
             "help": "Wayback snapshots whose text has not been asked for yet. "
                     "Run by hand, not by a unit."},
            {"id": "scan-runs", "type": "table", "title": "Last scan per paper",
             "placement": ["tab:crawl"],
             "columns": [
                 {"key": "journal", "label": "Paper"},
                 {"key": "status", "label": "Status"},
                 {"key": "finished", "label": "Finished", "format": "relative_time"},
                 {"key": "articles", "label": "Articles", "format": "integer",
                  "align": "right"},
                 {"key": "comments", "label": "Comments", "format": "integer",
                  "align": "right"},
                 {"key": "images", "label": "Images", "format": "integer",
                  "align": "right"},
                 {"key": "errors", "label": "Errors", "format": "integer",
                  "align": "right"},
             ]},
            {"id": "scan-events", "type": "events", "title": "Recent scans",
             "placement": ["tab:crawl"]},
            {"id": "corpus", "type": "kv", "title": "Corpus",
             "placement": ["tab:corpus"]},
            {"id": "analysis", "type": "kv", "title": "What has been read",
             "placement": ["tab:analysis"]},
            {"id": "hold", "type": "text", "title": "Claude-backed work",
             "placement": ["tab:analysis"]},
        ],
        "tab": {"sections": [
            {"id": "crawl", "title": "Crawl"},
            {"id": "corpus", "title": "Corpus", "columns": 2},
            {"id": "analysis", "title": "Analysis", "columns": 2},
        ]},
        "actions": [
            {"id": "trigger-scan", "label": "Scan now",
             "description": "Queues a scan of one paper, or of all three. The "
                            "same thing the web app's Sources card does; the "
                            "crawler keeps its polite pace either way.",
             "placement": ["tab:crawl"],
             "confirm": "confirm", "danger": False, "long_running": False,
             "params": [{"name": "journal", "label": "Paper", "type": "enum",
                         "options": ["all", *slugs], "default": "all",
                         "required": True}]},
        ],
    }


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #

def _crawl_check(data: dict, live: dict) -> tuple[dict, list]:
    """One line per paper, and the check that reads the worst of them."""
    last_done = data.get("last_done") or {}
    items, levels = [], []
    for slug in live.get("journals") or sorted(last_done):
        done = last_done.get(slug)
        age = _age_s(done.get("at")) if done else None
        if age is None:
            level = "unknown"
            detail = "no completed scan on record"
        else:
            level = ("error" if age > CRAWL_ERROR_S else
                     "warn" if age > CRAWL_WARN_S else "ok")
            if done.get("status") == "error":
                level = _worst([level, "warn"])
            detail = (f"{done.get('status')}, {_human_age(age)}: "
                      f"{done.get('articles_seen') or 0} articles, "
                      f"{done.get('comment_snapshots') or 0} new comments")
        levels.append(level)
        items.append({"label": slug, "level": level, "detail": detail,
                      "since": (done or {}).get("at")})
    worst = _worst(levels)
    oldest = max((_age_s((last_done.get(i["label"]) or {}).get("at")) or 0)
                 for i in items) if items else None
    check = {"id": "crawl-fresh", "label": "Papers scanned recently",
             "level": worst,
             "detail": ("no paper configured" if not items else
                        f"oldest completed scan {_human_age(oldest)}")}
    return {"items": items}, [check]


def _entity_widget(data: dict) -> tuple[dict, dict]:
    cov = data.get("coverage") or {}
    read, total = cov.get("articles_read"), cov.get("articles_total")
    if read is None or not total:
        return ({"done": 0, "total": 0, "unit": "articles", "level": "unknown",
                 "note": "not measured yet"},
                {"id": "entity-reading", "label": "Entity reading",
                 "level": "unknown", "detail": "no measurement yet"})
    left = max(0, total - read)
    per_hour = data.get("read_last_hour") or 0
    widget = {"done": read, "total": total, "unit": "articles",
              "rate_per_hour": per_hour, "level": "ok",
              "note": f"{left:,} left; {cov.get('entities', 0):,} entities, "
                      f"{cov.get('mentions', 0):,} mentions"}
    if per_hour:
        widget["eta"] = _iso(datetime.now().astimezone()
                             + timedelta(hours=left / max(1, per_hour)))
    # Two decimals, not one: at 310,331 of 310,455 a single decimal rounds to
    # "100.0%" and the 124 unread articles disappear into the formatting.
    detail = (f"{read:,} of {total:,} articles with a body "
              f"({100 * read / total:.2f}%)")
    if per_hour:
        detail += f"; {per_hour:,} read in the last hour"
    elif left:
        detail += f"; idle, {left:,} left"
    else:
        detail += "; complete"
    # Nothing left to read is the goal, not a fault, and a corpus that keeps
    # growing means a handful always trails. Only a real backlog going nowhere
    # is worth a colour, and that is what the unit's own state would show.
    return widget, {"id": "entity-reading", "label": "Entity reading",
                    "level": "ok" if left < 2000 or per_hour else "info",
                    "detail": detail}


def status(snap: Snapshot, live: dict) -> dict:
    data, at, err = snap.read()
    age = None if at is None else time.time() - at
    checks: list[dict] = []

    # -- the database and the snapshot over it ------------------------- #
    if live.get("degraded"):
        checks.append({"id": "db", "label": "Postgres reachable", "level": "error",
                       "detail": "the daemon is running degraded, without Postgres"})
    elif err:
        checks.append({"id": "db", "label": "Postgres reachable", "level": "warn",
                       "detail": f"last read failed: {err}"})
    elif at is None:
        checks.append({"id": "db", "label": "Postgres reachable", "level": "unknown",
                       "detail": "first read not finished yet"})
    elif age is not None and age > SNAPSHOT_STALE_S:
        checks.append({"id": "db", "label": "Postgres reachable", "level": "warn",
                       "detail": f"figures last read {_human_age(age)}"})
    else:
        checks.append({"id": "db", "label": "Postgres reachable", "level": "ok",
                       "detail": f"figures read {_human_age(age)}"})

    crawl_widget, crawl_checks = _crawl_check(data, live)
    checks += crawl_checks

    # -- the scanner itself -------------------------------------------- #
    current, queued = live.get("current"), live.get("queue") or 0
    running_for = _age_s((current or {}).get("started_at"))
    if current and running_for and running_for > SCAN_STUCK_S:
        checks.append({"id": "scan-queue", "label": "Scanner",
                       "level": "warn",
                       "detail": f"{current.get('slug')} scanning for "
                                 f"{_human_age(running_for)[:-4]}, queue {queued}",
                       "since": _iso(current.get("started_at"))})
    elif queued > 3:
        checks.append({"id": "scan-queue", "label": "Scanner", "level": "warn",
                       "detail": f"{queued} scans waiting"})
    elif current:
        checks.append({"id": "scan-queue", "label": "Scanner", "level": "ok",
                       "detail": f"scanning {current.get('slug')} "
                                 f"({current.get('current') or 0}"
                                 f"/{current.get('total') or '?'}), queue {queued}"})
    else:
        checks.append({"id": "scan-queue", "label": "Scanner", "level": "ok",
                       "detail": "idle"})

    entity_widget, entity_check = _entity_widget(data)
    checks.append(entity_check)

    # -- the hold on Claude-backed work -------------------------------- #
    until = entities.paused_until()
    if until:
        when = time.strftime("%a %d %b %H:%M", time.localtime(until))
        checks.append({"id": "llm-hold", "label": "Claude-backed work",
                       "level": "info", "detail": f"held until {when}"})
        hold_text = (f"Analysis that calls Claude is **held until {when}**. "
                     "The crawl, the archive backfill and the web app are "
                     "unaffected.")
    else:
        checks.append({"id": "llm-hold", "label": "Claude-backed work",
                       "level": "ok",
                       "detail": "free to run, under 50% of a five-hour window "
                                 "and 40% of a week"})
        hold_text = ("Free to run. The account is never taken past **50%** of a "
                     "five-hour window or **40%** of a week.")

    # -- widgets -------------------------------------------------------- #
    docs = data.get("documents") or {}
    bodies = data.get("bodies") or {}
    cov = data.get("coverage") or {}
    span = data.get("span") or {}
    last_run = data.get("last_run") or []

    widgets = {
        "comments": {"value": docs.get("comment", 0), "unit": "comments",
                     "format": "integer"},
        "crawl": crawl_widget,
        "entity-reading": entity_widget,
        "bodies": {"value": bodies.get("not_attempted", 0), "unit": "snapshots",
                   "format": "integer", "level": "info",
                   "note": f"{bodies.get('with_text', 0):,} of "
                           f"{bodies.get('total', 0):,} archive snapshots have "
                           f"their text; started by hand"},
        "scan-runs": {"rows": [
            {"journal": r["slug"], "status": r["status"],
             "finished": r["finished_at"] or r["requested_at"],
             "articles": r["articles_seen"], "comments": r["comment_snapshots"],
             "images": r["images_new"], "errors": r["errors"],
             "_level": "warn" if r["status"] == "error" or (r["errors"] or 0)
                       else "ok"}
            for r in last_run]},
        "scan-events": {"items": [
            {"at": r["at"], "level": "warn" if r["status"] == "error" else "info",
             "text": (f"{r['slug']}: scan {r['status']}, "
                      f"{r['articles_seen'] or 0} articles, "
                      f"{r['comment_snapshots'] or 0} new comments, "
                      f"{r['images_new'] or 0} images")}
            for r in (data.get("recent_runs") or [])]},
        "corpus": {"items": [
            {"label": "Articles", "value": docs.get("article", 0),
             "format": "integer"},
            {"label": "Comments", "value": docs.get("comment", 0),
             "format": "integer"},
            {"label": "Pictures", "value": docs.get("image", 0),
             "format": "integer"},
            {"label": "Earliest", "value": span.get("earliest"), "format": "text"},
            {"label": "Latest", "value": span.get("latest"), "format": "text"},
            {"label": "Newest capture", "value": data.get("newest_article_fetch"),
             "format": "relative_time"},
        ]},
        "analysis": {"items": [
            {"label": "Entities", "value": cov.get("entities", 0),
             "format": "integer"},
            {"label": "Mentions", "value": cov.get("mentions", 0),
             "format": "integer"},
            {"label": "Documents read", "value": cov.get("documents_read", 0),
             "format": "integer"},
            {"label": "Commenter profiles", "value": data.get("profiles", 0),
             "format": "integer"},
            {"label": "Personas", "value": data.get("personas", 0),
             "format": "integer"},
        ]},
        "hold": {"markdown": hold_text},
    }

    level = _worst([c["level"] for c in checks])
    return {
        "schema": "cc.status/1",
        "generated_at": _now_iso(),
        "manifest_revision": REVISION,
        "health": {"level": level, "summary": _summary(level, checks, data, live)},
        "checks": checks,
        "widgets": widgets,
        "actions_state": {
            "trigger-scan": ({"enabled": False, "reason": "no database"}
                             if live.get("degraded") else
                             {"enabled": False,
                              "reason": f"{queued} scans already queued"}
                             if queued > 3 else {"enabled": True})},
    }


def _summary(level: str, checks: list[dict], data: dict, live: dict) -> str:
    """One line, derived from the checks rather than written beside them.

    The contract asks that the summary never disagrees with the checks, which
    is only reliable if it is computed from them.
    """
    bad = [c for c in checks if c["level"] in ("warn", "error")]
    if bad:
        return "; ".join(f"{c['label'].lower()}: {c.get('detail') or c['level']}"
                         for c in bad[:3])
    docs = data.get("documents") or {}
    cov = data.get("coverage") or {}
    pct = cov.get("pct")
    current = live.get("current")
    head = (f"Scanning {current.get('slug')}" if current else "Crawl idle")
    tail = (f", {pct:.2f}% of articles read" if isinstance(pct, (int, float)) else "")
    unread = [c for c in checks if c["level"] == "unknown"]
    if unread:
        tail += f"; {unread[0]['label'].lower()} unknown"
    return (f"{head}; {docs.get('article', 0):,} articles and "
            f"{docs.get('comment', 0):,} comments{tail}.")


# --------------------------------------------------------------------------- #
# actions
# --------------------------------------------------------------------------- #

class Actions:
    """`POST /cc/v1/actions/{id}`, with request_id used as an idempotency key.

    central-control may retry a request it did not see the answer to; the same
    request_id must not queue a second scan. The last hundred are remembered,
    which is more than a dashboard can produce between restarts.
    """

    KEEP = 100

    def __init__(self, view: DaemonView | None) -> None:
        self.view = view
        self._seen: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()

    def run(self, action_id: str, body: dict) -> tuple[int, dict]:
        if action_id != "trigger-scan":
            return 404, {"error": {"code": "unknown_action",
                                   "message": f"no action {action_id!r}"}}
        if self.view is None:
            return 409, {"error": {"code": "busy",
                                   "message": "the scan engine is not running"}}
        rid = str(body.get("request_id") or "")
        with self._lock:
            done = self._seen.get(rid) if rid else None
        if done is not None:
            return 200, done

        params = body.get("params") or {}
        slug = str(params.get("journal") or "all")
        known = list(sources.all_slugs())
        if slug != "all" and slug not in known:
            return 400, {"error": {"code": "bad_param",
                                   "message": f"journal must be 'all' or one of "
                                              f"{', '.join(known)}"}}
        targets = known if slug == "all" else [slug]
        try:
            runs = [self.view.trigger_scan(s) for s in targets]
        except Exception as exc:
            log.warning("cc-api trigger-scan failed: %s", exc)
            return 409, {"error": {"code": "busy", "message": str(exc)}}
        out = {"schema": "cc.action/1", "state": "succeeded",
               "message": f"Queued {len(targets)} scan"
                          f"{'s' if len(targets) != 1 else ''} "
                          f"({', '.join(targets)}); run ids "
                          f"{', '.join(str(r) for r in runs)}."}
        if rid:
            with self._lock:
                self._seen[rid] = out
                while len(self._seen) > self.KEEP:
                    self._seen.popitem(last=False)
        return 200, out
