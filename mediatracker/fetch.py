"""Polite, stdlib-only HTTP fetching.

House rule: no requests/httpx/aiohttp. This wraps urllib.request with

  * a per-host minimum delay (politeness / rate limiting),
  * an optional robots.txt gate,
  * a descriptive User-Agent,
  * blocking I/O pushed onto a thread so the asyncio daemon never stalls.

For journals whose article body or comments are rendered only by client-side JS,
the source adapter can fall back to render.py (Playwright). Everything that can
be done with a plain GET should be done here.
"""
from __future__ import annotations

import asyncio
import gzip
import http.cookiejar
import json
import logging
import threading
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Response:
    url: str            # final URL after redirects
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def content_type(self) -> str | None:
        return self.headers.get("content-type")

    def text(self, default_encoding: str = "utf-8") -> str:
        enc = default_encoding
        ct = self.content_type or ""
        if "charset=" in ct:
            enc = ct.split("charset=", 1)[1].split(";", 1)[0].strip() or enc
        return self.body.decode(enc, errors="replace")


class FetchError(RuntimeError):
    pass


class Fetcher:
    def __init__(self, cfg) -> None:
        self._delay = cfg.request_delay_seconds
        self._timeout = cfg.request_timeout_seconds
        self._ua = cfg.user_agent
        self._respect_robots = cfg.respect_robots
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}
        # Some sites answer the first request with a cookie and a redirect back
        # to the same URL; without a jar that is an infinite loop returning an
        # empty body. Requests run on worker threads, so the jar is guarded.
        self._cookies = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookies))
        self._opener_lock = threading.Lock()

    def _host(self, url: str) -> str:
        return urlsplit(url).netloc.lower()

    def _lock(self, host: str) -> asyncio.Lock:
        lock = self._host_locks.get(host)
        if lock is None:
            lock = asyncio.Lock()
            self._host_locks[host] = lock
        return lock

    async def get(self, url: str, *, headers: dict[str, str] | None = None,
                  force_allow: bool = False) -> Response:
        """Fetch `url`. Per-host politeness delay always applies. robots.txt is
        honored unless `force_allow` is set — used only for the comment endpoints,
        which the site disallows for generic agents but which the user has
        explicitly opted to collect (see DOCTRINE.md / tamedia.fetch_comments)."""
        return await self._request(url, None, headers=headers, force_allow=force_allow)

    async def post_json(self, url: str, payload: dict, *,
                        headers: dict[str, str] | None = None,
                        force_allow: bool = False) -> Response:
        """POST a JSON body. Some sites render a fragment (comments, more-results)
        from an internal endpoint that only answers POST; that is still one
        request for one document, so it goes through the same politeness gate as
        a GET. This never writes anything on the site — see the adapters."""
        body = json.dumps(payload).encode("utf-8")
        hdrs = {"Content-Type": "application/json", "Accept": "*/*"}
        if headers:
            hdrs.update(headers)
        return await self._request(url, body, headers=hdrs, force_allow=force_allow)

    async def _request(self, url: str, data: bytes | None, *,
                       headers: dict[str, str] | None,
                       force_allow: bool) -> Response:
        host = self._host(url)
        if self._respect_robots and not force_allow and not await self._allowed(url):
            raise FetchError(f"robots.txt disallows {url}")
        # Serialize per host and honor the min-delay so we never hammer a site.
        async with self._lock(host):
            wait = self._delay - (time.monotonic() - self._last_hit.get(host, 0.0))
            if wait > 0:
                await asyncio.sleep(wait)
            resp = await asyncio.to_thread(self._blocking_get, url, headers, data)
            self._last_hit[host] = time.monotonic()
            return resp

    def _blocking_get(self, url: str, headers: dict[str, str] | None,
                      data: bytes | None = None) -> Response:
        req_headers = {
            "User-Agent": self._ua,
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "fr-CH,fr;q=0.9,en;q=0.7",
        }
        if headers:
            req_headers.update(headers)
        req = urllib.request.Request(url, data=data, headers=req_headers,
                                     method="POST" if data is not None else "GET")
        try:
            with self._opener_lock:
                opener = self._opener
            with opener.open(req, timeout=self._timeout) as r:
                raw = r.read()
                enc = (r.headers.get("Content-Encoding") or "").lower()
                body = _decompress(raw, enc)
                hdrs = {k.lower(): v for k, v in r.headers.items()}
                return Response(url=r.geturl(), status=r.status, headers=hdrs, body=body)
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read()
            except Exception:
                pass
            return Response(url=url, status=exc.code, headers=dict(exc.headers or {}), body=body)
        except urllib.error.URLError as exc:
            raise FetchError(f"fetch failed for {url}: {exc.reason}") from exc

    async def _allowed(self, url: str) -> bool:
        host = self._host(url)
        if host not in self._robots:
            self._robots[host] = await asyncio.to_thread(self._load_robots, url)
        rp = self._robots[host]
        if rp is None:  # robots unreachable -> do not block ourselves
            return True
        return rp.can_fetch(self._ua, url)

    def _load_robots(self, url: str) -> RobotFileParser | None:
        parts = urlsplit(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        rp = RobotFileParser()
        rp.set_url(robots_url)
        try:
            rp.read()
            return rp
        except Exception as exc:
            log.debug("could not read robots for %s: %s", robots_url, exc)
            return None


def _decompress(raw: bytes, encoding: str) -> bytes:
    if encoding == "gzip":
        try:
            return gzip.decompress(raw)
        except Exception:
            return raw
    if encoding == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw
