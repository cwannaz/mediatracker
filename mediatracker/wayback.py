"""A deliberately slow client for the Internet Archive.

The archive is the only surviving source for this corpus's missing years, it is
free, and it is run by a non-profit on donated hardware. Everything here is
built to take less than it is offered:

  * one request at a time, never concurrent, with a floor between them;
  * narrow queries only — per year, per URL pattern, never a bare wildcard,
    because a broad CDX query is what actually hurts (and 504s anyway);
  * exponential backoff on 429/503/504 and on the archive's "Temporarily
    Offline" HTML, which it serves with a 200;
  * a hard ceiling on consecutive failures, so a bad night stops the run
    instead of hammering through it.

`http.client.HTTPException` is in the retry set deliberately: an IncompleteRead
is not an OSError, so it used to escape the client entirely and be read one
level up as "this leg has nothing left", quietly abandoning a whole year.

Stdlib only, and synchronous on purpose: a backfill is a batch job, and running
it off the daemon's event loop keeps it from competing with live scanning.
"""
from __future__ import annotations

import gzip
import http.client
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

CDX_ENDPOINT = "http://web.archive.org/cdx/search/cdx"
WEB_ENDPOINT = "http://web.archive.org/web"

# The archive answers an overload with a 200 and this page, so the status code
# cannot be trusted on its own.
_OFFLINE_MARKERS = (b"Internet Archive: Temporarily Offline",
                    b"504 Gateway Time-out",
                    b"Too Many Requests")


class ArchiveBusy(RuntimeError):
    """The archive asked us to slow down or stop."""


class GaveUp(RuntimeError):
    """Too many consecutive failures; the run should stop rather than push on."""


@dataclass
class WaybackClient:
    """One request at a time, with a floor between them."""

    min_delay: float = 2.0          # seconds between requests
    # CDX legitimately takes a minute to enumerate a year, so it keeps the long
    # timeout. A single page does not: measured, a snapshot that has not
    # arrived in 45s usually never does, and at five retries one such URL cost
    # ten minutes of a night that only has a few.
    timeout: float = 120.0
    snapshot_timeout: float = 45.0
    max_retries: int = 5
    snapshot_retries: int = 2
    backoff_base: float = 8.0       # first retry waits this long, then doubles
    max_consecutive_failures: int = 12
    user_agent: str = ("MediaTracker/0.1 (private sociological research; "
                       "polite backfill; contact via github.com/cwannaz/mediatracker)")

    _last_request: float = field(default=0.0, init=False)
    _consecutive_failures: int = field(default=0, init=False)
    requests_made: int = field(default=0, init=False)
    bytes_fetched: int = field(default=0, init=False)
    sleep_seconds: float = field(default=0.0, init=False)

    # ------------------------------------------------------------------ #

    def _wait(self) -> None:
        gap = time.monotonic() - self._last_request
        if gap < self.min_delay:
            naptime = self.min_delay - gap
            time.sleep(naptime)
            self.sleep_seconds += naptime

    def get(self, url: str, *, timeout: float | None = None,
            retries: int | None = None) -> bytes:
        """One GET, paced and retried. Raises GaveUp when the archive has been
        failing long enough that continuing would just be rude."""
        timeout = timeout or self.timeout
        retries = retries or self.max_retries
        for attempt in range(retries):
            self._wait()
            req = urllib.request.Request(url, headers={
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
            })
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                    enc = (resp.headers.get("content-encoding") or "").lower()
                self._last_request = time.monotonic()
                if enc == "gzip":
                    raw = gzip.decompress(raw)
                elif enc == "deflate":
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                if any(m in raw[:4000] for m in _OFFLINE_MARKERS):
                    raise ArchiveBusy("archive served an overload page")
                self.requests_made += 1
                self.bytes_fetched += len(raw)
                self._consecutive_failures = 0
                return raw
            except (urllib.error.HTTPError, urllib.error.URLError,
                    http.client.HTTPException, ArchiveBusy, TimeoutError,
                    OSError) as exc:
                self._last_request = time.monotonic()
                status = getattr(exc, "code", None)
                # A 404 is an answer, not a failure: that snapshot is simply
                # not there, and retrying it four more times helps nobody.
                if status in (404, 403):
                    self._consecutive_failures = 0
                    raise
                self._consecutive_failures += 1
                if self._consecutive_failures >= self.max_consecutive_failures:
                    raise GaveUp(
                        f"{self._consecutive_failures} consecutive failures; "
                        f"stopping rather than pushing on ({exc})") from exc
                if attempt == retries - 1:
                    raise
                nap = self.backoff_base * (2 ** attempt)
                log.warning("archive said %s; backing off %.0fs (attempt %d/%d)",
                            status or type(exc).__name__, nap,
                            attempt + 1, retries)
                time.sleep(nap)
                self.sleep_seconds += nap
        raise GaveUp("unreachable")

    # ------------------------------------------------------------------ #

    def cdx(self, url_pattern: str, *, year: int | None = None,
            match_type: str = "domain", url_filter: str | None = None,
            collapse: str | None = "urlkey", limit: int = 30000,
            fields: str = "timestamp,original,digest,statuscode") -> list[dict]:
        """Enumerate captures. Always narrow: a year, a pattern, a filter.

        `url_filter` is a CDX regex applied to the original URL — the way to
        ask for "article pages with comments" without a wildcard query the
        archive will refuse.
        """
        q = {
            "url": url_pattern,
            "matchType": match_type,
            "filter": "statuscode:200",
            "limit": str(limit),
            "fl": fields,
            "output": "text",
        }
        if collapse:
            q["collapse"] = collapse
        if year:
            q["from"], q["to"] = f"{year}0101", f"{year}1231"
        parts = [(k, v) for k, v in q.items()]
        if url_filter:
            parts.append(("filter", f"original:{url_filter}"))
        url = f"{CDX_ENDPOINT}?{urllib.parse.urlencode(parts)}"

        body = self.get(url).decode("utf-8", errors="replace")
        names = fields.split(",")
        out = []
        for line in body.splitlines():
            cols = line.split(" ")
            if len(cols) == len(names):
                out.append(dict(zip(names, cols)))
        return out

    def snapshot(self, timestamp: str, original: str) -> str:
        """The captured page as it was served, without the archive's toolbar.

        The `id_` suffix asks for the original bytes: no injected banner, no
        rewritten links, which is what a parser wants.
        """
        raw = self.get(f"{WEB_ENDPOINT}/{timestamp}id_/{original}",
                       timeout=self.snapshot_timeout, retries=self.snapshot_retries)
        return raw.decode("utf-8", errors="replace")


def stats(client: WaybackClient) -> dict:
    return {"requests": client.requests_made,
            "mib": round(client.bytes_fetched / 1048576, 1),
            "slept_s": round(client.sleep_seconds)}
