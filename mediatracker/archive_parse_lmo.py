"""Reading the Drupal-era pages that came *before* Newsnetz.

Between roughly 2009 and March 2012 the Tamedia romand titles ran a Drupal
site — the theme is literally `lmo`, Le Matin Online — and it too rendered its
threads server-side, on the article page itself rather than at a `?comments=1`
view. So the years before the Newsnetz material are recoverable as well, by a
different reader.

Two things make this era worth more per page than the Newsnetz one:

  * **Commenters have accounts with stable keys.** Every comment links to
    `/users/<key>`, and the key is the site's own normalised identifier while
    the `<strong>` beside it is the display form. `MountaiDiver` posts from
    `/users/mountaidiver`. Nothing in the 2012-2016 material carries any user
    id at all, so this is the only stretch of the corpus where two comments can
    be tied to one account without inference.
  * **The whole thread is on the article page**, so one fetch gets both.

What it does not carry: no reply threading (the list is flat), and no
like/dislike counts — both are Newsnetz additions. Fields absent here come
back None rather than zero, because zero is a measurement and None is not.

One comment looks like:

    <a id="comment-410104"></a>
    <div class="commentaire ">
      <a href="/users/affreujojo883">  <img class="vignette_comment" …/>
      </a>  <p>Oui!oui! Pitié! …</p>
      <p><small><ul class="links">… vous identifier …</ul></small></p>
      <p class="left"><small>13.06.2010 - 15:59 par
      <a href="/users/affreujojo883">  <strong>affreujojo883</strong>
      </a></small></p>
      <p class="right"><small> > <a href="/comment/410104/signal?…">…</a>

Regex rather than a parser, for the same reason as the Newsnetz reader: the
house rule is stdlib-only and this markup is machine-generated.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

PAPER_TZ = ZoneInfo("Europe/Zurich")

_ANCHOR = re.compile(r'<a\s+id="comment-(\d+)"\s*>\s*</a>', re.I)
_BLOCK = re.compile(r'<div class="commentaire[^"]*"[^>]*>', re.I)
_USER = re.compile(r'href="/users/([^"/?#]+)"', re.I)
_DISPLAY = re.compile(r'href="/users/[^"]*"[^>]*>\s*<strong>(.*?)</strong>', re.S | re.I)
_SIGNAL = re.compile(r'/comment/(\d+)/signal', re.I)
_NODE = re.compile(r'node%2F(\d+)|node/(\d+)', re.I)
_PARA = re.compile(r"<p([^>]*)>(.*?)</p>", re.S | re.I)
# "13.06.2010 - 15:59"
_STAMP = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})\s*-\s*(\d{1,2}):(\d{2})")

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t ]+")


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


def looks_like_lmo(page: str) -> bool:
    """Is this the Drupal era rather than Newsnetz or the PHP era before it?

    Deliberately does NOT test `class="commentaire`: the PHP era labels its
    count `class="commentaires"`, and the shorter string is a prefix of the
    longer one, so that test claims every 2008 page as Drupal. Each marker
    here belongs to this stack alone.
    """
    return bool(re.search(r'comments_titre|id="comment-\d|themes/lmo/', page, re.I))


def _body_of(chunk: str) -> str:
    """The comment text itself.

    The block holds four paragraphs and only the first is the comment: the
    others are the login notice, the byline and the abuse link. They are told
    apart by what they carry, not by position, because a block whose login
    notice is absent (a signed-in capture) would otherwise shift.
    """
    for attrs, inner in _PARA.findall(chunk):
        if "class=" in attrs.lower():
            continue
        if re.search(r"<small|<ul\b", inner, re.I):
            continue
        text = _text(inner)
        if text:
            return text
    return ""


def parse_comments(page: str) -> list[dict]:
    """Every comment on an archived Drupal-era article page.

    Returns `source_id`, `author_key`, `author_nick`, `posted_at`,
    `body_text`, and `parent_id`/`like_count`/`dislike_count` as None — this
    platform had none of the three.
    """
    out: list[dict] = []
    seen: set[str] = set()

    marks = [(m.start(), m.group(1)) for m in _ANCHOR.finditer(page)]
    if not marks:
        return out
    bounds = [p for p, _ in marks] + [len(page)]

    for i, (_, cid) in enumerate(marks):
        chunk = page[bounds[i]:bounds[i + 1]]
        if not _BLOCK.search(chunk):
            continue
        body = _body_of(chunk)
        if not body or cid in seen:
            continue

        # The abuse link repeats the id; when the two disagree the anchor wins,
        # because the anchor is what the page's own permalinks point at.
        sig = _SIGNAL.search(chunk)
        if sig and sig.group(1) != cid:
            log.debug("comment id mismatch: anchor %s, signal %s", cid, sig.group(1))

        key_m = _USER.search(chunk)
        disp_m = _DISPLAY.search(chunk)
        byline = ""
        for attrs, inner in _PARA.findall(chunk):
            if "left" in attrs and " par" in _text(inner):
                byline = inner
                break

        seen.add(cid)
        out.append({
            "source_id": cid,
            "author_key": key_m.group(1).lower() if key_m else None,
            "author_nick": _text(disp_m.group(1)) if disp_m else None,
            "posted_at": parse_timestamp(_text(byline) or chunk),
            "body_text": body,
            "parent_id": None,
            "like_count": None,
            "dislike_count": None,
        })
    return out


# --------------------------------------------------------------------------- #

_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
_COUNT = re.compile(r'comments_titre[^>]*>.*?<strong>\s*(\d+)\s*</strong>', re.S | re.I)
_URL_ID = re.compile(r"-(\d{5,})(?:$|[?#/])")


def parse_article(page: str, url: str) -> dict:
    """Headline, id and thread size from a Drupal-era article page."""
    node = _NODE.search(page)
    from_url = _URL_ID.search(url)
    source_id = (node.group(1) or node.group(2)) if node else (
        from_url.group(1) if from_url else None)

    head = _H1.search(page)
    headline = _text(head.group(1)) if head else ""
    if not headline:
        t = _TITLE.search(page)
        headline = _text(t.group(1)) if t else ""

    cnt = _COUNT.search(page)
    return {
        "source_id": source_id,
        "canonical_url": url,
        "headline": headline or None,
        "comment_count": int(cnt.group(1)) if cnt else None,
    }
