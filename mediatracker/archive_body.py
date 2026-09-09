"""Pulling the article text out of an archived page.

The era-specific readers in `archive_parse*` were written against the pages
this project fetched first: comment views (`print.html?comments=1`). Those
carry the lead and the thread, not the article, so their body patterns never
matched an article page -- measured, 0 bodies out of 60 captures spanning
2008-2020 and all three papers, with no error to say so.

Measured on the same captures fetched at their plain article URL, 14 of 15 do
carry the text. What varies between 2008 and 2020 is the container markup; what
does not vary is that the prose sits in <p> elements and the furniture around
it is identifiable by class. So this reads paragraphs and rejects furniture,
rather than hunting for a container that changed name four times.

The guard that matters is comments. An archived article page often includes its
thread inline, and a body that quietly swallows the thread would put readers'
words into the article's own text -- which then feeds entity extraction, search
and every count built on them. A comment is not an error here; it is worse,
because it reads like signal.
"""
from __future__ import annotations

import html
import re

MAX_CHARS = 20_000
MIN_PARAGRAPH = 25          # shorter runs are captions, bylines, menu items
# A comment view carries the lead and a "Plus..." link -- measured at 233
# chars, where the smallest real body in the same sample was 1,458. 400 sits
# between them, and a teaser stored as a body is worse than no body: it marks
# the article done.
MIN_BODY = 400

# Everything the pages wrap around the article. Matched against the whole
# opening tag, so it catches both class= and id= spellings.
_FURNITURE = (
    "smallprint", "disclaimer", "commentfb", "kommenttime", "kommentlinks",
    "comment", "komment", "reaction_text", "reactiontext", "caption",
    "legend", "byline", "author", "credit", "copyright", "footer", "nav",
    "menu", "teaser", "related", "sidebar", "newsletter", "advert", "banner",
    "cookie", "share", "social", "breadcrumb", "pagination", "tags",
)

# Regions to remove entirely before reading paragraphs: a comment thread is
# made of <p> like anything else, so filtering tag by tag is not enough.
# Attribute quoting is not consistent across fifteen years of archived markup
# -- double quotes, single quotes and bare values all occur, and matching only
# double quotes let a whole comment thread through as article prose.
_COMMENT_REGION = re.compile(
    r'(?is)<(div|section|ul|ol)\b[^>]*?(?:id|class)\s*=\s*'
    r'(?:"[^"]*(?:comment|komment|reaction|disqus|thread)[^"]*"'
    r"|'[^']*(?:comment|komment|reaction|disqus|thread)[^']*'"
    r'|[^\s>"\']*(?:comment|komment|reaction|disqus|thread)[^\s>]*)'
    r'[^>]*>'
)

_DROP = re.compile(r'(?is)<(script|style|noscript|head|form|select)[^>]*>.*?</\1>')

# Elements the page itself hides. The Newsnetz template keeps an email-form
# confirmation in a display:none div, so every recovered body opened with
# "Votre email a ete envoye." -- text no reader ever saw, on its way into
# the search index and the entity extractor.
_HIDDEN = re.compile(
    r'(?is)<(div|p|span|section)\b[^>]*style\s*=\s*["\'][^"\']*'
    r'display\s*:\s*none[^"\']*["\'][^>]*>.*?</\1>')
# <p> only. Headline and subhead have their own columns, and reading h2 here
# put the headline back into the body of every page that repeats it.
_PARA = re.compile(r'(?is)<(p)\b([^>]*)>(.*?)</\1>')
_TAG = re.compile(r'<[^>]+>')
_WS = re.compile(r'[ \t ]+')
_BLANKS = re.compile(r'\n{3,}')


def _strip_comment_regions(page: str) -> str:
    """Cut each comment container from its opening tag to its matching close.

    Regex cannot balance tags, so this walks nesting depth by hand. Erring
    towards cutting too much is right: a lost paragraph costs a little text, a
    kept thread corrupts the body.
    """
    out = []
    pos = 0
    for m in _COMMENT_REGION.finditer(page):
        if m.start() < pos:
            continue
        out.append(page[pos:m.start()])
        tag = m.group(1).lower()
        depth = 1
        i = m.end()
        opener = re.compile(rf'(?is)<{tag}\b'), re.compile(rf'(?is)</{tag}\s*>')
        while depth and i < len(page):
            nxt_o = opener[0].search(page, i)
            nxt_c = opener[1].search(page, i)
            if not nxt_c:
                i = len(page)
                break
            if nxt_o and nxt_o.start() < nxt_c.start():
                depth += 1
                i = nxt_o.end()
            else:
                depth -= 1
                i = nxt_c.end()
        pos = i
    out.append(page[pos:])
    return "".join(out)


def _clean(fragment: str) -> str:
    text = html.unescape(_TAG.sub(" ", fragment))
    text = text.replace("\r", "\n")
    text = _WS.sub(" ", text)
    return text.strip()


def extract_body(page: str) -> str | None:
    """The article's own prose, or None when the capture does not carry it.

    Returns None rather than a fragment: a 40-character 'body' is a lead or a
    cookie notice, and storing it would make an empty article look filled.
    """
    if not page:
        return None
    page = _DROP.sub(" ", page)
    page = _HIDDEN.sub(" ", page)
    page = _strip_comment_regions(page)

    parts: list[str] = []
    seen: set[str] = set()
    for m in _PARA.finditer(page):
        attrs = (m.group(2) or "").lower()
        if any(f in attrs for f in _FURNITURE):
            continue
        text = _clean(m.group(3))
        if len(text) < MIN_PARAGRAPH:
            continue
        # Archived pages repeat their lead in a meta block and again in the
        # article; keeping both would double the text of every short piece.
        key = text[:120]
        if key in seen:
            continue
        seen.add(key)
        parts.append(text)

    body = _BLANKS.sub("\n\n", "\n\n".join(parts)).strip()
    if len(body) < MIN_BODY:
        return None
    return body[:MAX_CHARS]
