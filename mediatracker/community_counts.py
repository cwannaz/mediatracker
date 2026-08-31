"""How much was said in the years whose words are gone.

Le Matin's 2017-2020 comment threads are effectively unrecoverable: the read
path (`?comments=1`) still existed, but the crawler stopped following it —
2016 holds ~11,700 thread captures, 2017 holds 56, 2018 holds one. Seventy-odd
threads will not characterise a public.

What the archive did keep, nearly five thousand times, is the widget that
printed the counters above each article:

    GET /api/communities/<story id>
    {"communities":[
      {"type":"facebook","count":183,"url":"https://www.lematin.ch/10001941",
       "title":"Les primes maladie impayées…","article_id":"5dcd1942…"},
      {"type":"comment","count":3,"article_id":"5dcd1942…",
       "refresh":"14.11.2019 15:34:53"}]}

So for those years we can have the headline, the size of the thread, and the
share counts — everything except the comments themselves. That is enough to
answer the question the corpus most needs answered about the gap: whether the
commenting public grew, shrank or held steady between the 52,000 comments of
2013 and the handful after 2021.

**These rows carry a count and no comments, and that is not an absence of
comments.** An article recorded here as having had 3 comments has three
comments nobody holds. Nothing downstream may read a missing thread on one of
these rows as an empty one — which is why they are written with a comment
count and no comment rows at all, and why the snapshot records `counts_only`.

`refresh` is the moment the publisher computed the figure, which is nearer the
truth than the capture timestamp and can precede it by hours.
"""
from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

PAPER_TZ = ZoneInfo("Europe/Zurich")

_URL_ID = re.compile(r"lematin\.ch/(\d{4,})")
_PATH_ID = re.compile(r"/api/communities/(\d+)")
# "14.11.2019 15:34:53"
_STAMP = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})")


def parse_refresh(raw: str | None) -> datetime | None:
    m = _STAMP.search(raw or "")
    if not m:
        return None
    d, mo, y, h, mi, se = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d, h, mi, se, tzinfo=PAPER_TZ)
    except ValueError:
        return None


def parse(payload: str | bytes, *, captured_url: str = "") -> dict | None:
    """One archived /api/communities response.

    Returns `source_key`, `headline`, `comment_count`, `shares` and
    `refreshed_at`, or None when the body is not this shape. `comment_count`
    is None rather than 0 when the payload carries no comment row at all:
    "the widget did not report" and "the widget reported none" are different
    facts and only the second is a measurement.
    """
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", "replace")
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    rows = data.get("communities")
    if not isinstance(rows, list) or not rows:
        return None

    out = {"source_key": None, "headline": None, "comment_count": None,
           "shares": {}, "refreshed_at": None, "article_uid": None}

    for row in rows:
        if not isinstance(row, dict):
            continue
        kind, count = row.get("type"), row.get("count")
        if kind == "comment":
            out["comment_count"] = count if isinstance(count, int) else None
        elif isinstance(count, int) and kind:
            out["shares"][kind] = count
        out["article_uid"] = out["article_uid"] or row.get("article_id")
        out["refreshed_at"] = out["refreshed_at"] or parse_refresh(row.get("refresh"))
        if not out["headline"] and row.get("title"):
            out["headline"] = html.unescape(str(row["title"])).strip() or None
        if not out["source_key"]:
            m = _URL_ID.search(str(row.get("url") or ""))
            if m:
                out["source_key"] = m.group(1)

    # The request path names the story too, and it is there even when every
    # row lacks a url — which happens when sharing is switched off.
    if not out["source_key"]:
        m = _PATH_ID.search(captured_url)
        if m:
            out["source_key"] = m.group(1)

    return out if out["source_key"] else None


def article_url_for(source_key: str) -> str:
    """The canonical article URL for a story id.

    The short form the widget itself prints. It redirects to the slugged URL on
    the live site, and it is the only form this endpoint gives us.
    """
    return f"https://www.lematin.ch/{source_key}"
