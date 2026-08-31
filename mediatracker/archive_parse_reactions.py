"""Reading the PHP-era pages that came before Drupal — the "reaction" markup.

A third stack, and the oldest one that carried comments. Le Matin ran a PHP CMS
until roughly 2009 (`/membership/ma_page.php`, `/fr/actu/<section>/<slug>_9-282442`)
and it rendered whole threads server-side, unpaginated, with the count in the
heading. Threads were not small: 172 comments on the October 2008 UBS bailout,
196 on "Le Conseil federal nous sacrifie".

It is the best of the three eras for identity, and by some distance:

    <a href="/membership/ma_page.php?idUser=387782">…</a>
    … 16.10.2008 - 00:03 par <strong><a href="…idUser=387782">Ge1602</a></strong>

`idUser` is a **numeric account id**. The Drupal era's `/users/mountaidiver` is
a slug derived from the display name, so a rename moves it; a number does not
move at all. If a commenter changes how they sign, the id still ties the two
together — which is the one thing no amount of stylometry can establish on its
own. On the page measured, all 172 comments carried one: this platform had no
anonymous posting.

Structure is unusually kind. The template brackets every comment in
`<!-- BEGIN COMMENT HTML -->` / `<!-- END COMMENT HTML -->`, so blocks need no
guessing at boundaries, and `sign=` in the abuse link is unique per comment
(172 distinct on that page) while `idContent=` is constant and identifies the
article.

No reply threading and no like counts — like the Drupal era, and for the same
reason: both are Newsnetz additions. Those fields come back None, not zero.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

PAPER_TZ = ZoneInfo("Europe/Zurich")

_BLOCK = re.compile(r"<!--\s*BEGIN COMMENT HTML\s*-->(.*?)<!--\s*END COMMENT HTML\s*-->", re.S | re.I)
_UID = re.compile(r"idUser=(\d+)", re.I)
_SIGN = re.compile(r"idContent=(\d+)&(?:amp;)?sign=(\d+)", re.I)
_NICK = re.compile(r"par\s*<strong>\s*<a[^>]*>(.*?)</a>\s*</strong>", re.S | re.I)
_BODY = re.compile(r'<div class="reaction_text">\s*<p>(.*?)</p>', re.S | re.I)
_STAMP = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})\s*-\s*(\d{1,2}):(\d{2})")
_COUNT = re.compile(r'id="nbcomment"[^>]*>\s*<strong>\s*(\d+)\s*</strong>', re.I)

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t ]+")


def _text(fragment: str | None) -> str:
    if not fragment:
        return ""
    s = fragment.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    s = _TAG.sub("", s)
    s = html.unescape(s)
    s = _WS.sub(" ", s)
    return "\n".join(line.strip() for line in s.split("\n")).strip()


def parse_timestamp(raw: str | None) -> datetime | None:
    m = _STAMP.search(raw or "")
    if not m:
        return None
    d, mo, y, h, mi = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d, h, mi, tzinfo=PAPER_TZ)
    except ValueError:
        return None


def looks_like_reactions(page: str) -> bool:
    """Is this the PHP era rather than Drupal or Newsnetz?"""
    return bool(_BLOCK.search(page) or re.search(r'class="reaction_text"', page, re.I))


def parse_comments(page: str) -> list[dict]:
    """Every comment on an archived PHP-era article page.

    `author_key` is the numeric account id as a string, kept as the site wrote
    it. `source_id` is the per-comment `sign` value; a block without one is
    dropped rather than given a synthetic id, because a synthetic id would
    collide with itself on the next capture of the same thread.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for chunk in (m.group(1) for m in _BLOCK.finditer(page)):
        sign = _SIGN.search(chunk)
        if not sign:
            continue
        cid = sign.group(2)
        body_m = _BODY.search(chunk)
        body = _text(body_m.group(1)) if body_m else ""
        if not body or cid in seen:
            continue
        uid = _UID.search(chunk)
        nick = _NICK.search(chunk)
        seen.add(cid)
        out.append({
            "source_id": cid,
            "author_key": uid.group(1) if uid else None,
            "author_nick": _text(nick.group(1)) if nick else None,
            "posted_at": parse_timestamp(chunk),
            "body_text": body,
            "parent_id": None,
            "like_count": None,
            "dislike_count": None,
        })
    return out


# --------------------------------------------------------------------------- #

_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
_URL_ID = re.compile(r"[_-](\d{5,})(?:$|[?#/])")


def parse_article(page: str, url: str) -> dict:
    """Headline, id and thread size from a PHP-era article page.

    The id comes from the URL rather than from `idContent`: the URL's is the
    one the archive filed the capture under, and it is what every other view
    of the same article agrees on.
    """
    m = _URL_ID.search(url)
    head = _H1.search(page)
    headline = _text(head.group(1)) if head else ""
    if not headline:
        t = _TITLE.search(page)
        headline = _text(t.group(1)) if t else ""
    cnt = _COUNT.search(page)
    return {
        "source_id": m.group(1) if m else None,
        "canonical_url": url,
        "headline": headline or None,
        "comment_count": int(cnt.group(1)) if cnt else None,
    }
