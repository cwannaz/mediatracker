"""Tiny read-only HTTP server for the image blob store.

The daemon's control surface is WebSocket/JSON, which cannot carry <img src>.
This serves GET /blob/<sha256> from the content-addressed store so the local web
app can reproduce an article's images offline. Bound to 127.0.0.1, read-only, and
it only ever serves paths derived from a 64-hex sha256 that exists in the DB.
"""
from __future__ import annotations

import logging
import re
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger(__name__)

_SHA_RE = re.compile(r"^/blob/([0-9a-f]{64})$")


class _Handler(BaseHTTPRequestHandler):
    server_version = "MediaTrackerBlobs/0.1"

    def __init__(self, *args, blob_root: Path, lookup, **kw):
        self._blob_root = blob_root
        self._lookup = lookup
        super().__init__(*args, **kw)

    def do_GET(self):  # noqa: N802 (http.server API)
        m = _SHA_RE.match(self.path)
        if not m:
            self.send_error(404, "not found")
            return
        sha = m.group(1)
        row = self._lookup(sha)
        if not row:
            self.send_error(404, "unknown blob")
            return
        storage_path, mime = row
        # Resolve and confirm the file stays inside the blob root.
        path = (self._blob_root / storage_path).resolve()
        if not str(path).startswith(str(self._blob_root.resolve())) or not path.is_file():
            self.send_error(404, "missing file")
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        log.debug("blob %s", fmt % args)


def start(cfg, lookup) -> ThreadingHTTPServer:
    """Start the blob server on cfg.port + 1 in a daemon thread."""
    port = cfg.port + 1
    handler = partial(_Handler, blob_root=cfg.blob_path, lookup=lookup)
    httpd = ThreadingHTTPServer((cfg.host, port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True, name="blobserver").start()
    log.info("blob server on http://%s:%s/blob/<sha256>", cfg.host, port)
    return httpd
