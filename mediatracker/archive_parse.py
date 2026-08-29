"""Reading the Tamedia "Newsnetz" pages the Internet Archive kept.

Le Matin, 24 heures and the Tribune all ran this stack until roughly 2017, and
it rendered its comment threads **server-side** — at `?comments=1`, and in a
`print.html?comments=1` variant with no pagination at all. That is the whole
reason the missing years are recoverable: a JavaScript comment widget would
have left the archive holding an empty div.

The markup is verbose but strictly regular. One comment looks like:

    <div class="commentRedesign evenParent" id="commentRedesign_17542845-7676256">
      <div class="komment" id="commentParent_7676256">
        <div class="kommentLeft">
          <!-- 17542845 7676256 0 -->            <- article, comment, parent
          <h4>Milton Jimenez</h4>
          <div class="kommentTime">21.09.2013, 22:56  Heures </div>
          ... <span id="recommendCommentCountNumber_7676256">2 </span>
              <span id="dislikeCommentCountNumber_7676256">6 </span>
      <p><span id="recommendCommentMessage_7676256">Et bien voilà…</span>

The HTML comment is the useful part: it carries the parent id, so reply
threading survives, which the visible markup alone does not give.

Regex rather than a parser because the house rule is stdlib-only and this
markup is machine-generated and never varied. Everything returns None rather
than guessing when a field is absent.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

PAPER_TZ = ZoneInfo("Europe/Zurich")

# One comment per chunk: the header opens the div and the body closes it.
_BLOCK = re.compile(r'<div class="commentRedesign[^"]*"[^>]*>')
_IDS = re.compile(r"<!--\s*(\d+)\s+(\d+)\s+(\d+)\s*-->")
_NICK = re.compile(r"<h4[^>]*>(.*?)</h4>", re.S)
_TIME = re.compile(r'class="kommentTime"[^>]*>(.*?)<', re.S)
_BODY = re.compile(r'id="recommendCommentMessage_(\d+)"[^>]*>(.*?)</span>', re.S)
_RECO = re.compile(r'id="recommendCommentCountNumber_(\d+)"[^>]*>\s*(\d+)')
_DISLIKE = re.compile(r'id="dislikeCommentCountNumber_(\d+)"[^>]*>\s*(\d+)')
# "21.09.2013, 22:56  Heures" — the German label survived the localisation.
_STAMP = re.compile(r"(\d{2})\.(\d{2})\.(\d{4}),\s*(\d{1,2}):(\d{2})")

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t ]+")


def _text(fragment: str | None) -> str:
    """Visible text of a fragment: tags out, entities decoded, space collapsed."""
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


def parse_comments(page: str) -> list[dict]:
    """Every comment rendered on an archived Newsnetz thread page.

    Returns dicts with `source_id`, `parent_id`, `author_nick`, `posted_at`,
    `body_text`, `like_count`, `dislike_count`. A block missing an id or a body
    is dropped rather than half-filled: the form template at the foot of the
    page looks enough like a comment to fool a looser reader.
    """
    out: list[dict] = []
    seen: set[str] = set()
    bounds = [m.start() for m in _BLOCK.finditer(page)] + [len(page)]
    for i in range(len(bounds) - 1):
        chunk = page[bounds[i]:bounds[i + 1]]

        body_m = _BODY.search(chunk)
        if not body_m:
            continue
        msg_id = body_m.group(1)
        body = _text(body_m.group(2))
        if not msg_id or msg_id in seen or not body:
            continue

        ids_m = _IDS.search(chunk)
        parent = None
        if ids_m and ids_m.group(3) not in ("0", msg_id):
            parent = ids_m.group(3)

        nick = _text(_NICK.search(chunk).group(1)) if _NICK.search(chunk) else None
        stamp = parse_timestamp(_TIME.search(chunk).group(1)
                                if _TIME.search(chunk) else None)

        likes = dislikes = None
        r = _RECO.search(chunk)
        if r and r.group(1) == msg_id:
            likes = int(r.group(2))
        d = _DISLIKE.search(chunk)
        if d and d.group(1) == msg_id:
            dislikes = int(d.group(2))

        seen.add(msg_id)
        out.append({
            "source_id": msg_id,
            "parent_id": parent,
            "author_nick": nick or None,
            "posted_at": stamp,
            "body_text": body,
            "like_count": likes,
            "dislike_count": dislikes,
        })
    return out


# --------------------------------------------------------------------------- #
# The article around the thread
# --------------------------------------------------------------------------- #

_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
_H2 = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S | re.I)
_COUNT = re.compile(r'class="comment_count"[^>]*>\s*(\d+)')
_COUNT2 = re.compile(r"(\d+)\s+Commentaires?", re.I)
_ARTICLE_ID = re.compile(r"/story/(\d+)|/(\d{6,})/")
_UPDATED = re.compile(r"Mis à jour le\s*([\d.]+)", re.I)
_AUTHOR = re.compile(r'/stichwort/autor/[^"]*"[^>]*>(.*?)</a>', re.S | re.I)
_BODY_BLOCK = re.compile(
    r'<div[^>]*class="[^"]*(?:articleContent|storyContent|maincontent)[^"]*"[^>]*>(.*?)</div>',
    re.S | re.I)


def parse_article(page: str, url: str) -> dict:
    """Headline, byline and comment count from an archived Newsnetz page.

    The comment count is worth as much as the text here: it is present on plain
    article snapshots where the thread itself was never captured, so a year
    with no recoverable comments can still yield how big its threads were.
    """
    headline = _text(_H1.search(page).group(1)) if _H1.search(page) else None
    if not headline and _TITLE.search(page):
        headline = _text(_TITLE.search(page).group(1)).split(" - ")[0].strip() or None

    count = None
    m = _COUNT.search(page) or _COUNT2.search(page)
    if m:
        count = int(m.group(1))

    author = _text(_AUTHOR.search(page).group(1)) if _AUTHOR.search(page) else None
    subhead = _text(_H2.search(page).group(1)) if _H2.search(page) else None

    body = None
    b = _BODY_BLOCK.search(page)
    if b:
        body = _text(b.group(1))[:20000] or None

    sid = None
    ms = _ARTICLE_ID.search(url)
    if ms:
        sid = ms.group(1) or ms.group(2)

    return {"headline": headline, "subhead": subhead, "author": author,
            "comment_count": count, "body_text": body, "source_id": sid}


def looks_like_newsnetz(page: str) -> bool:
    """Whether this snapshot is the era these parsers understand."""
    return ("newsnetz" in page[:20000].lower()
            or "commentRedesign" in page
            or "kommentTime" in page)
