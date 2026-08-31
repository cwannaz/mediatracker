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
import re
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
    # 30s, not 45, and one attempt rather than two. A page the archive has not
    # begun sending in half a minute is usually one it will not send at all,
    # and after a few thousand requests in a day it appears to answer by
    # holding connections open rather than by refusing outright. Spending 98
    # seconds discovering that costs more than the page is worth.
    snapshot_timeout: float = 30.0
    # A hard ceiling on ONE response, start to finish. The socket timeout
    # only measures inactivity, so a response that trickles a byte at a
    # time never trips it — which is how two listings hung for half an
    # hour today without raising. This is the clock that does not reset.
    total_budget: float = 300.0
    max_retries: int = 5
    snapshot_retries: int = 1
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
            retries: int | None = None, budget: float | None = None) -> bytes:
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
                    raw = _read_within(resp, budget or self.total_budget)
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
            frm: str | None = None, to: str | None = None,
            match_type: str = "domain", url_filter: str | None = None,
            collapse: str | None = "urlkey", limit: int = 30000,
            fields: str = "timestamp,original,digest,statuscode") -> list[dict]:
        """Enumerate captures. Always narrow: a span, a pattern, a filter.

        `url_filter` is a CDX regex applied to the original URL — the way to
        ask for "article pages with comments" without a wildcard query the
        archive will refuse.

        `frm`/`to` take YYYYMMDD and override `year`. They exist because a
        whole-year query against a busy domain is the one shape the search API
        reliably fails at: it does not error, it trickles, and a socket timeout
        measures inactivity rather than total time, so the call can hang for
        half an hour and never raise. A month is small enough to come back.
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
        if frm and to:
            q["from"], q["to"] = frm, to
        elif year:
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
        return decode(raw)


_CHARSET = re.compile(rb"""charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.I)
_LATIN = {"iso-8859-1", "iso8859-1", "latin-1", "latin1", "windows-1252", "cp1252"}


def decode(raw: bytes) -> str:
    """Text of a captured page, in whatever the page was actually written in.

    Not every era of these sites was UTF-8. The pre-2009 PHP pages declare
    `charset=iso-8859-1` and mean it, and decoding those as UTF-8 turns every
    accent into a replacement character — silently, because errors="replace"
    does not raise. That corrupts exactly the accent measures the profiling
    pass reads, so the encoding is taken from the page's own declaration and
    only guessed when it makes none.
    """
    m = _CHARSET.search(raw[:4096])
    if m:
        name = m.group(1).decode("ascii", "replace").strip().lower()
        if name in _LATIN:
            return raw.decode("cp1252", errors="replace")
        try:
            return raw.decode(name)
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        # Undeclared and not UTF-8: cp1252 is the only other thing these
        # servers ever sent, and it cannot itself fail.
        return raw.decode("cp1252", errors="replace")


class ReadTimeout(TimeoutError):
    """A response that began but would not finish inside its budget."""


def _read_within(resp, budget: float) -> bytes:
    """Read a response with a ceiling on total elapsed time, not idle time.

    `urlopen(timeout=...)` arms an inactivity timer: every byte that arrives
    resets it. The archive under load answers a heavy listing by dribbling,
    which satisfies that timer indefinitely. Two legs were lost to it in one
    day, each hanging around half an hour and never raising, so the retry and
    fallback paths above them never ran. Reading in chunks against a wall
    clock is what turns that silence into an error something can act on.
    """
    chunks = []
    deadline = time.monotonic() + budget
    while True:
        if time.monotonic() > deadline:
            raise ReadTimeout(f"response still arriving after {budget:.0f}s")
        chunk = resp.read(65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def stats(client: WaybackClient) -> dict:
    return {"requests": client.requests_made,
            "mib": round(client.bytes_fetched / 1048576, 1),
            "slept_s": round(client.sleep_seconds)}
