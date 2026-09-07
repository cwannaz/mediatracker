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

from . import db, entities, search

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

    def __init__(self, *args, cfg=None, blob_base="", **kw):
        self.cfg = cfg
        self.blob_base = blob_base
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

    def do_OPTIONS(self):  # noqa: N802
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

    # -- endpoints ------------------------------------------------------ #

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


def start(cfg) -> ThreadingHTTPServer:
    """Serve the API on cfg.port + 2, in a daemon thread."""
    port = cfg.port + 2
    blob_base = f"http://{cfg.host}:{cfg.port + 1}"
    handler = partial(_Handler, cfg=cfg, blob_base=blob_base)
    httpd = ThreadingHTTPServer((cfg.host, port), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True, name="api").start()
    log.info("read-only API on http://%s:%s/v1/", cfg.host, port)
    return httpd
