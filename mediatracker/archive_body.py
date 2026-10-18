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


# --------------------------------------------------------------------------- #
# the portal era, before 2012
# --------------------------------------------------------------------------- #
#
# Le Matin before March 2012 is why `extract_body` above refuses that era
# rather than guessing: its teasers, contest promos and video captions sit in
# bare <p> exactly like article prose -- no class, no container, low link
# density -- so every furniture rule keeps them, and one page produced a
# 9,640-char "body" made of a dozen unrelated stories.
#
# The way in is not a better heuristic but the template itself. Measured on 24
# captures sampled across 2008-2011, every page names the article's own text
# in a container, and the surrounding stories sit OUTSIDE it:
#
#   2008, the PHP era       <div class="article_text">   1 per page
#   2009-2011, Drupal/lmo   <div class="text_article">   1 per page
#
# The two names are mirror images of each other, which is a trap worth naming:
# "article_text" is 2008 and "text_article" is 2009 onwards. Both are read
# here, and the portal's own teasers live in `bloc_accroche` blocks which
# neither container encloses. That is what makes this exact instead of
# probable -- the teasers are not filtered out, they are never reached.
_PORTAL_BODY = re.compile(
    r'<div[^>]*class\s*=\s*["\']?(?:text_article|article_text)\b[^>]*>', re.I)
# The lead sits outside the text container, in its own paragraph, and is the
# first sentences of the article: dropping it would lose the part most likely
# to name the subject. In the Drupal era the page carries a dozen `chapo`
# paragraphs -- one per teaser -- so only the one inside `bloc_article` is the
# article's own. The PHP era carries exactly one, with no such wrapper.
# The tag carrying it changed with the template: a <div> or an <h3> in 2008,
# a <div> wrapping a <p class="chapo"> from 2009. Matching the class on any of
# the three and reading the element's contents covers all of them -- the 2008
# lead was lost while this looked only for a <p>, and it is the sentences most
# likely to name the subject.
_PORTAL_LEAD = re.compile(
    r'<(?P<tag>div|h3|p)[^>]*class\s*=\s*["\']?chapo\b[^>]*>', re.I)
_PORTAL_ARTICLE_BLOCK = re.compile(
    r'<div[^>]*class\s*=\s*["\']?bloc_article\b[^>]*>', re.I)


# The wire dateline that opens many 2008 dispatches, on its own line:
# "17/03/2008 NEW YORK (AFP)". Measured, it is the first paragraph of 5 of the
# first 24 portal bodies recovered. It is not prose and it is not the lead, but
# it clears the 25-character floor, so it needs naming rather than a length
# rule. Only a paragraph that is ENTIRELY a dateline is dropped: the same words
# inside a sentence are part of the article.
_DATELINE = re.compile(
    r"""^\s*(?:\d{1,2}[./]\d{1,2}[./]\d{2,4})?[\s\u00a0]*
         (?:[A-ZÀ-Ü][A-ZÀ-Ü\s\-'.]{1,30})?
         \(\s*(?:AFP|ATS|ATS/AFP|AFP/ATS|Reuters|AP|APA|DPA|SDA|Belga)\s*\)
         [\s\u00a0.,-]*$""",
    re.X)


def _is_dateline(text: str) -> bool:
    return bool(_DATELINE.match(text))


def looks_like_portal(page: str) -> bool:
    """Is this a pre-2012 Le Matin page whose text container can be found?

    False for the Newsnetz era, which has neither container, so the two
    readers cannot both claim a page.
    """
    return bool(page) and bool(_PORTAL_BODY.search(page))


def _enclosed(page: str, open_match: re.Match, tag_name: str = "div") -> str:
    """The contents of the element `open_match` opens, counting nested copies.

    Stopping at the first close would be enough for most of these pages and
    wrong for some: measured, 2 of 24 captures nest divs inside the text
    container, and the naive form truncated one body from 2,559 characters to
    1,296 -- silently, which is the kind of loss nothing downstream can see.
    """
    depth, i, end = 1, open_match.end(), len(page)
    tag = re.compile(rf"<(/?){tag_name}\b", re.I)
    while i < len(page):
        m = tag.search(page, i)
        if not m:
            break
        depth += -1 if m.group(1) else 1
        if not depth:
            # Up to where the closing tag STARTS. Ending after it instead
            # leaves a bare "</div" in the text, which no tag-stripping regex
            # matches because it has no ">" -- one sampled body opened with it.
            end = m.start()
            break
        i = m.end()
    return page[open_match.end():end]


def extract_body_portal(page: str) -> str | None:
    """The article's prose from a pre-2012 Le Matin page, or None.

    Returns None rather than a fragment for the same reason as `extract_body`:
    on these pages a short "body" is a teaser, and a teaser stored as a body
    marks the article done.
    """
    if not page:
        return None
    opened = _PORTAL_BODY.search(page)
    if not opened:
        return None

    page = _DROP.sub(" ", page)
    page = _HIDDEN.sub(" ", page)
    page = _strip_comment_regions(page)
    # The container has to be found again: the substitutions above change
    # every offset, and reusing the old match would slice the wrong bytes.
    opened = _PORTAL_BODY.search(page)
    if not opened:
        return None

    parts: list[str] = []
    lead = _article_lead(page)
    if lead:
        parts.append(lead)

    seen = {lead[:120]} if lead else set()
    for m in _PARA.finditer(_enclosed(page, opened)):
        attrs = (m.group(2) or "").lower()
        if any(f in attrs for f in _FURNITURE):
            continue
        text = _clean(m.group(3))
        if len(text) < MIN_PARAGRAPH or text[:120] in seen:
            continue
        if _is_dateline(text):
            continue
        seen.add(text[:120])
        parts.append(text)

    body = _BLANKS.sub("\n\n", "\n\n".join(parts)).strip()
    if len(body) < MIN_BODY:
        return None
    return body[:MAX_CHARS]


def _article_lead(page: str) -> str:
    """The article's own `chapo`, never a teaser's.

    In the Drupal era the article block wraps it; measured, those pages carry
    9 to 12 `chapo` paragraphs and exactly one `bloc_article`. In the PHP era
    there is one `chapo` and no wrapper, so the first is the article's.
    """
    block = _PORTAL_ARTICLE_BLOCK.search(page)
    region = _enclosed(page, block) if block else page
    m = _PORTAL_LEAD.search(region)
    if not m:
        return ""
    lead = _clean(_enclosed(region, m, m.group("tag")))
    # Judged like any other paragraph, and a wire dateline is not one. Some
    # 2008 captures hold nothing else in the chapo -- "12/03/2008 PARIS
    # (AFP)" and an empty <p> -- so both the length floor that rejects
    # captions and the dateline rule apply here, or the article opens with
    # its wire credit instead of its first sentence.
    if len(lead) < MIN_PARAGRAPH or _is_dateline(lead):
        return ""
    return lead


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
