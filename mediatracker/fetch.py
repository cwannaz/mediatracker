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
import os
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


# --------------------------------------------------------------------------- #
# A dated hold on fetching
# --------------------------------------------------------------------------- #
# 2026-09-27. Cedric: *"Put your fetchers on hold for a week please, we are
# getting low on usage left."* Everything that goes out to a newspaper or to an
# archive stops until the date below and then resumes by itself -- the crawl's
# schedule, a scan asked for by hand, and the archive backfill legs. Reading the
# corpus, the web app, the search index and the API are untouched: they cost
# nothing outside this machine.
#
# Set MT_FETCH_PAUSED_UNTIL to another "YYYY-MM-DD HH:MM" to move it, or to an
# empty string to lift it. The hold lifting needs no restart: each fetcher
# tests it when it is about to go out.
PAUSED_UNTIL = "2026-10-04 08:00"
# How long a long-running backfill leg waits for the hold to end before it
# exits and lets its supervisor relaunch it. Long enough that the run cannot be
# mistaken for "nothing left to fetch" and retired (supervisor4.sh, MIN_RUN).
HOLD_WAIT_CAP_S = 6 * 3600


class Paused(RuntimeError):
    """Fetching is on hold until a date Cedric set."""


def paused_until(now: float | None = None) -> float | None:
    """When the hold on fetching ends, or None if there is no hold in force."""
    raw = os.environ.get("MT_FETCH_PAUSED_UNTIL", PAUSED_UNTIL)
    if not (raw or "").strip():
        return None
    when = time.mktime(time.strptime(raw.strip(), "%Y-%m-%d %H:%M"))
    return when if when > (now if now is not None else time.time()) else None


def hold_reason(what: str = "fetching") -> str | None:
    """One sentence naming the hold, or None. For logs and for the dashboard."""
    until = paused_until()
    if until is None:
        return None
    return (f"{what} is on hold until "
            f"{time.strftime('%a %d %b %H:%M', time.localtime(until))} "
            f"(MT_FETCH_PAUSED_UNTIL changes it)")


def check_not_paused(what: str = "this fetch") -> None:
    """Raise while the hold is in force. Called BEFORE anything goes out."""
    reason = hold_reason(what)
    if reason:
        raise Paused(reason)


def wait_out_hold(cap_s: float = HOLD_WAIT_CAP_S, *, sleep=time.sleep) -> bool:
    """Wait for the hold to lift, up to `cap_s`. True if it is still in force.

    A leg that EXITS on the hold would be read by its supervisor as a leg with
    nothing left to fetch, and retired for good. So it waits instead, and if
    the hold outlasts the cap it exits after a run far too long to look like a
    quick finish.
    """
    waited = 0.0
    while True:
        until = paused_until()
        if until is None:
            return False
        if waited >= cap_s:
            return True
        # Counted, not clocked: what matters is how long this leg has waited,
        # and a step short enough that a lifted hold is noticed in minutes.
        step = min(300.0, cap_s - waited, max(1.0, until - time.time()))
        sleep(step)
        waited += step


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
