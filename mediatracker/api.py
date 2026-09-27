"""A read-only HTTP/JSON API over the corpus, for other programs.

The daemon's own control surface is WebSocket/JSON and is shaped for the web
app: stateful, chatty, and free to change whenever a view changes. That is a
bad contract to hand another project. This is the stable one -- plain HTTP,
plain JSON, versioned under /v1, read-only, and documented by the service
itself at `GET /v1/`.

Written to be general rather than fitted to its first consumer. Ariane (a
document sorter with its own Postgres, its own entities and its own search) is
the first program to call it, and the obvious temptation was to return whatever
shape Ariane's index wants. That would have made the second consumer's life
worse and Ariane's barely better, so instead this returns what MediaTracker
actually knows, in one shape, and leaves the mapping to the caller.

Three things it always tells the truth about, because a federated search that
quietly under-reports is worse than one that is absent:

* `total` is capped, and `truncated` says when the cap was hit.
* `note` carries how a query was answered -- prefiltered, scanned, timed out.
* `entity_coverage` says what fraction of the corpus has been read for
  entities, since that work runs for days and a caller must not read an entity
  count as final.

Threading: every request gets its own connection from a thread-local pool, so a
slow query blocks one worker rather than the daemon's event loop. The API never
writes, so there is no transaction to coordinate.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import ccapi, db, entities, search

log = logging.getLogger(__name__)

VERSION = "1.0"
MAX_LIMIT = 200
DEFAULT_LIMIT = 25

_local = threading.local()


def _search_kinds() -> tuple:
    """The document kinds a caller may ask for. Named once, so the API, its
    error messages and its tests cannot drift apart."""
    return search.KINDS


def _conn(cfg):
    """One connection per worker thread. psycopg connections are not shareable."""
    c = getattr(_local, "conn", None)
    if c is None or getattr(c, "closed", False):
        c = _local.conn = db.connect(cfg)
    return c


def _csv(value: str | None) -> tuple:
    """Accept `kind=article,image` and repeated `kind=` alike."""
    if not value:
        return ()
    return tuple(v.strip() for v in value.split(",") if v.strip())


class _Handler(BaseHTTPRequestHandler):
    server_version = f"MediaTrackerAPI/{VERSION}"
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, cfg=None, blob_base="", snap=None, actions=None,
                 view=None, **kw):
        self.cfg = cfg
        self.blob_base = blob_base
        # central-control's view of the project: a snapshot thread, the live
        # daemon state, and the buttons. All three are absent when the API is
        # started outside the daemon, and /cc/v1 then answers 503.
        self.snap = snap
        self.actions = actions
        self.view = view
        super().__init__(*args, **kw)

    # -- plumbing ------------------------------------------------------- #

    def _send(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The service is bound to a private interface and cannot write
        # anything, so a browser-based consumer (Ariane's UI is one) is allowed
        # to call it directly rather than proxy every request through its own
        # server for no gain.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, status, message, **extra):
        self._send({"ok": False, "error": message, **extra}, status)

    def _send_cc(self, obj, status=200):
        """A /cc/v1 reply: the same JSON, and pointedly no CORS header.

        The rest of this service invites browsers in. central-control's half
        must not: a page on any origin could otherwise read the project's
        state, and a custom header is only a barrier while CORS is refused.
        """
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cc_error(self, status, code, message):
        self._send_cc({"error": {"code": code, "message": message}}, status)

    def _loopback_only(self) -> bool:
        """Whether this request really came from a process on this machine.

        Three tests, because each alone is wrong: the peer address misses a
        reverse proxy (every proxied client arrives from 127.0.0.1), the
        forwarding headers miss a proxy that strips them, and the Host header
        misses nothing but is trivially forged. Together they cover the two
        ways /cc/v1 actually leaks -- a server on 0.0.0.0, and a Caddy in
        front of it.
        """
        peer = (self.client_address or ("",))[0]
        if peer not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return False
        for header in ("X-Forwarded-For", "Forwarded", "X-Real-IP"):
            if self.headers.get(header) is not None:
                return False
        port = self.server.server_address[1]
        return (self.headers.get("Host") or "").strip() in (
            f"127.0.0.1:{port}", f"localhost:{port}")

    def do_OPTIONS(self):  # noqa: N802
        # No preflight for /cc/v1: it is not for browsers at all.
        if self.path.startswith("/cc/"):
            return self._cc_error(403, "loopback_only", "not available to browsers")
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):
        log.debug("api %s", fmt % args)

    # -- routing -------------------------------------------------------- #

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        multi = urllib.parse.parse_qs(parsed.query)

        if parts[:1] == ["cc"]:
            return self._cc_get(parts[1:])
        if not parts or parts[0] != "v1":
            return self._fail(404, "unknown path; the API lives under /v1/")
        route = parts[1:]
        try:
            conn = _conn(self.cfg)
            if conn is None:
                return self._fail(503, "database unavailable")
            if not route:
                return self._send(self._index(conn))
            if route == ["health"]:
                return self._send(self._health(conn))
            if route == ["search"]:
                return self._send(self._search(conn, q, multi))
            if route == ["entities"]:
                return self._send(self._entities(conn, q))
            if len(route) == 2 and route[0] == "entities":
                return self._send(self._entity(conn, route[1], q))
            if len(route) == 3 and route[0] == "document":
                return self._send(self._document(conn, route[1], route[2]))
            return self._fail(404, f"unknown endpoint /{'/'.join(parts)}")
        except Exception as exc:                     # never leak a traceback
            log.exception("api error on %s", self.path)
            try:
                _conn(self.cfg).rollback()
            except Exception:
                pass
            return self._fail(500, str(exc))

    def do_POST(self):  # noqa: N802
        parts = [p for p in urllib.parse.urlparse(self.path).path.strip("/").split("/") if p]
        if parts[:1] != ["cc"]:
            return self._fail(405, "this API is read-only; nothing here takes a POST")
        if not self._loopback_only():
            return self._cc_error(403, "loopback_only",
                                  "/cc/v1 answers processes on this machine only")
        if parts[1:2] != ["v1"] or parts[2:3] != ["actions"] or len(parts) != 4:
            return self._cc_error(404, "not_found", f"no such endpoint /{'/'.join(parts)}")
        # The header is what stops a browser: it cannot be set cross-origin
        # without the CORS this service refuses on /cc.
        if self.headers.get("X-Central-Control") != "1":
            return self._cc_error(403, "forbidden", "X-Central-Control: 1 required")
        if self.actions is None:
            return self._cc_error(503, "unavailable", "the daemon is not serving actions")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except (ValueError, json.JSONDecodeError) as exc:
            return self._cc_error(400, "bad_param", str(exc))
        try:
            status, payload = self.actions.run(parts[3], body)
        except Exception as exc:
            log.exception("cc action failed")
            return self._cc_error(500, "internal", str(exc))
        return self._send_cc(payload, status)

    # -- endpoints ------------------------------------------------------ #

    def _cc_get(self, rest: list) -> None:
        """central-control's two endpoints, served without touching Postgres."""
        if not self._loopback_only():
            return self._cc_error(403, "loopback_only",
                                  "/cc/v1 answers processes on this machine only")
        if rest[:1] != ["v1"]:
            return self._cc_error(404, "not_found", "the CC-API lives under /cc/v1")
        route = rest[1:]
        if route == ["manifest"]:
            return self._send_cc(ccapi.manifest())
        if route == ["status"]:
            if self.snap is None or self.view is None:
                return self._cc_error(503, "unavailable",
                                      "the daemon is not reporting yet")
            return self._send_cc(ccapi.status(self.snap, self.view.live()))
        return self._cc_error(404, "not_found", f"no such endpoint /cc/{'/'.join(rest)}")

    def _index(self, conn) -> dict:
        """Self-description, so a consumer needs no out-of-band documentation."""
        return {
            "ok": True,
            "service": "mediatracker",
            "version": VERSION,
            "describes": "Swiss French-language newspapers: articles, pictures, "
                         "and reader comments, 2008 to now.",
            "corpus": _corpus(conn),
            "endpoints": [
                {"path": "/v1/health", "returns": "liveness and corpus size"},
                {"path": "/v1/search",
                 "params": {
                     "q": "search terms; \"quoted phrase\" is one block, "
                          "~word excludes it",
                     "mode": "text (default, stemmed and accent-folded) or regex "
                             "(POSIX, case-insensitive)",
                     "kind": "article, image, comment; comma-separated or repeated",
                     "journal": "lematin, 24heures, tdg; comma-separated",
                     "year_from": "inclusive year",
                     "year_to": "inclusive year",
                     "entity_id": "restrict to documents mentioning this entity",
                     "limit": f"1-{MAX_LIMIT}, default {DEFAULT_LIMIT}",
                     "offset": "for paging",
                 },
                 "returns": "results[], total, truncated, note"},
                {"path": "/v1/entities",
                 "params": {"q": "substring of the name", "kind":
                            "person, organization, place, event, topic",
                            "limit": "default 25"},
                 "returns": "entities[] with ids usable as search entity_id"},
                {"path": "/v1/entities/{id}",
                 "returns": "one entity and the documents mentioning it"},
                {"path": "/v1/document/{kind}/{ref}",
                 "returns": "one document in full, including body text"},
            ],
            "notes": [
                "Read-only. No endpoint writes anything.",
                "total is capped; check `truncated`.",
                "`note` says how a query was answered, including when it was "
                "cut short by the time limit.",
                "Entity data is still being extracted; see entity_coverage.",
            ],
        }

    def _health(self, conn) -> dict:
        return {"ok": True, "service": "mediatracker", "version": VERSION,
                "corpus": _corpus(conn),
                "entity_coverage": entities.coverage(conn)}

    def _search(self, conn, q, multi) -> dict:
        kinds = _csv(q.get("kind")) or tuple(multi.get("kind", ()))
        journals = _csv(q.get("journal")) or tuple(multi.get("journal", ()))
        bad = [k for k in kinds if k not in search.KINDS]
        if bad:
            return {"ok": False, "error": f"unknown kind {bad[0]!r}",
                    "allowed": list(_search_kinds())}
        mode = q.get("mode", "text")
        if mode not in ("text", "regex"):
            return {"ok": False, "error": "mode must be 'text' or 'regex'"}
        try:
            limit = min(int(q.get("limit", DEFAULT_LIMIT)), MAX_LIMIT)
            offset = int(q.get("offset", 0))
        except ValueError:
            return {"ok": False, "error": "limit and offset must be integers"}

        started = time.time()
        out = search.query(
            conn, q=q.get("q", ""), mode=mode, kinds=kinds, journals=journals,
            year_from=q.get("year_from"), year_to=q.get("year_to"),
            entity_id=q.get("entity_id"), limit=limit, offset=offset)
        return {
            "ok": True,
            "query": {"q": q.get("q", ""), "mode": mode, "kind": list(kinds),
                      "journal": list(journals), "limit": limit, "offset": offset},
            "total": out["total"],
            "truncated": out["truncated"],
            "note": out.get("note"),
            "took_ms": int(1000 * (time.time() - started)),
            "results": [self._hit(r) for r in out["rows"]],
        }

    def _hit(self, r: dict) -> dict:
        """One result, in the same shape whatever kind it is."""
        x = r.get("extra") or {}
        hit = {
            "kind": r["kind"],
            "id": r["ref"],
            "journal": r.get("journal"),
            "date": r.get("when"),
            "title": r.get("title"),
            "snippet": (r.get("snippet") or "").replace("<<", "").replace(">>", ""),
            "highlighted": r.get("snippet"),
            "score": r.get("rank"),
            "source_url": x.get("url"),
            "document_url": f"/v1/document/{r['kind']}/{r['ref']}",
        }
        if r["kind"] == "image":
            hit["image"] = {
                "blob_url": f"{self.blob_base}/blob/{r['ref']}",
                "thumbnail_url": f"{self.blob_base}/thumb/t/{r['ref']}",
                "preview_url": f"{self.blob_base}/thumb/m/{r['ref']}",
                "width": x.get("width"), "height": x.get("height"),
                "bytes": x.get("bytes"), "credit": x.get("credit"),
                "ran_under": x.get("headline"),
            }
        elif r["kind"] == "comment":
            hit["comment"] = {"author": x.get("nick"),
                              "is_reply": x.get("is_reply"),
                              "under_headline": x.get("headline")}
        else:
            hit["article"] = {"section": x.get("section"), "byline": x.get("author"),
                              "comment_count": x.get("comments"),
                              "origin": x.get("origin")}
        return hit

    def _entities(self, conn, q) -> dict:
        kind = q.get("kind") or None
        if kind and kind not in entities.KINDS:
            return {"ok": False, "error": f"unknown kind {kind!r}",
                    "allowed": list(entities.KINDS)}
        try:
            limit = min(int(q.get("limit", DEFAULT_LIMIT)), MAX_LIMIT)
        except ValueError:
            return {"ok": False, "error": "limit must be an integer"}
        found = entities.lookup(conn, q=q.get("q"), kind=kind, limit=limit)
        return {"ok": True, "entities": found,
                "entity_coverage": entities.coverage(conn)}

    def _entity(self, conn, eid: str, q) -> dict:
        try:
            eid_i = int(eid)
        except ValueError:
            return {"ok": False, "error": "entity id must be an integer"}
        with conn.cursor() as cur:
            cur.execute("""SELECT id, kind, name, mentions, first_seen, last_seen
                           FROM entity WHERE id = %s""", (eid_i,))
            row = cur.fetchone()
        if not row:
            return {"ok": False, "error": "no such entity"}
        try:
            limit = min(int(q.get("limit", DEFAULT_LIMIT)), MAX_LIMIT)
        except ValueError:
            limit = DEFAULT_LIMIT
        docs = search.query(conn, q="", kinds=(), journals=(),
                            entity_id=eid_i, limit=limit, offset=0)
        return {"ok": True,
                "entity": {"id": row[0], "kind": row[1], "name": row[2],
                           "mentions": row[3],
                           "first_seen": row[4].date().isoformat() if row[4] else None,
                           "last_seen": row[5].date().isoformat() if row[5] else None},
                "total": docs["total"], "truncated": docs["truncated"],
                "documents": [self._hit(r) for r in docs["rows"]]}

    def _document(self, conn, kind: str, ref: str) -> dict:
        if kind not in search.KINDS:
            return {"ok": False, "error": f"unknown kind {kind!r}",
                    "allowed": list(search.KINDS)}
        with conn.cursor() as cur:
            cur.execute("""SELECT kind, ref, journal, lang, published_at, title,
                                  body, extra
                           FROM search_doc WHERE kind = %s AND ref = %s""",
                        (kind, ref))
            row = cur.fetchone()
        if not row:
            return {"ok": False, "error": "no such document"}
        k, r, journal, lang, when, title, body, extra = row
        doc = {"kind": k, "id": r, "journal": journal, "language": lang,
               "date": when.date().isoformat() if when else None,
               "title": title, "text": body, "meta": extra or {}}
        if k == "image":
            doc["image"] = {"blob_url": f"{self.blob_base}/blob/{r}",
                            "thumbnail_url": f"{self.blob_base}/thumb/t/{r}",
                            "preview_url": f"{self.blob_base}/thumb/m/{r}"}
        with conn.cursor() as cur:
            cur.execute("""SELECT e.id, e.kind, e.name FROM entity_mention m
                           JOIN entity e ON e.id = m.entity_id
                           WHERE m.doc_kind = %s AND m.doc_ref = %s
                           ORDER BY e.mentions DESC LIMIT 50""", (kind, ref))
            doc["entities"] = [{"id": i, "kind": kk, "name": n}
                               for i, kk, n in cur.fetchall()]
        return {"ok": True, "document": doc}


def _corpus(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""SELECT kind, count(*) FROM search_doc GROUP BY kind""")
        by_kind = {k: n for k, n in cur.fetchall()}
        cur.execute("SELECT slug FROM journal ORDER BY slug")
        journals = [r[0] for r in cur.fetchall()]
        cur.execute("""SELECT min(published_at)::date, max(published_at)::date
                       FROM search_doc WHERE published_at IS NOT NULL""")
        lo, hi = cur.fetchone()
    return {"documents": by_kind, "journals": journals,
            "earliest": lo.isoformat() if lo else None,
            "latest": hi.isoformat() if hi else None}


def start(cfg, view=None) -> ThreadingHTTPServer:
    """Serve the API on cfg.port + 2, in a daemon thread.

    With a `view` onto the running daemon it also serves /cc/v1 for
    central-control, and starts the thread that keeps that report's snapshot
    of Postgres current. Without one (the API started on its own), /cc/v1
    answers 503 and no extra thread runs.
    """
    port = cfg.port + 2
    blob_base = f"http://{cfg.host}:{cfg.port + 1}"
    snap = actions = None
    if view is not None:
        snap = ccapi.Snapshot()
        actions = ccapi.Actions(view)
        threading.Thread(target=ccapi.refresh_loop, args=(cfg, snap, threading.Event()),
                         daemon=True, name="cc-snapshot").start()
    handler = partial(_Handler, cfg=cfg, blob_base=blob_base, snap=snap,
                      actions=actions, view=view)
    httpd = ThreadingHTTPServer((cfg.host, port), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True, name="api").start()
    log.info("read-only API on http://%s:%s/v1/%s", cfg.host, port,
             " (+ /cc/v1 for central-control)" if view is not None else "")
    return httpd
