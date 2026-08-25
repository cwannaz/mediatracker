"""Shared adapter for the TX Group titles that have moved to Astro (24heures.ch,
and by the site's own JS the Tribune de Genève too).

This is the second TX Group platform, not a variant of the first. Where
`tamedia.py` reads a single `__NEXT_DATA__` blob, an Astro page is
server-rendered HTML with small JSON islands, so everything here is parsed out
of markup:

  * identity and desk from `<script id="page-data">`;
  * headline, lead, dates, author and section from the `NewsArticle` ld+json;
  * the body from the `article-element` nodes inside `<article data-article-body>`;
  * comments from an internal endpoint that renders them as an HTML fragment.

Comments are the part that differs most. Le Matin serves JSON from
`api.lematin.ch` with numeric ids, a `parentCommentId` and a `totalCount`. Here
the site POSTs to its own `/api/content/load-comment-container` and gets HTML
back: ids are UUIDs, replies are flattened into the list with only an
`@nickname` marker of who they answer, and paging walks a timestamp cursor.
Both those losses are recorded rather than papered over — see `parse_comments`
and `link_replies`.

Parsing uses html.parser from the stdlib, in keeping with the house rule: the
markup is nested and attribute-bearing, which is exactly where regex scraping
starts silently dropping content.
"""
from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime
from html.parser import HTMLParser

from .base import ParsedArticle, ParsedComment, ParsedImage, Source
# Agency detection is platform-independent: both TX Group front-ends byline
# the same wire services the same way, so the vocabulary is shared.
from .tamedia import _detect_source

log = logging.getLogger(__name__)

# Article URLs are a slug followed by the numeric content id: no /story/ prefix
# the way Next.js had one, so the id suffix is the only reliable signal.
_ARTICLE_HREF_RE = re.compile(r'href="(/[a-z0-9][^"?#]*-\d{6,})"')
_JSON_ISLAND_RE = re.compile(
    r'<script id="([\w-]+)" type="application/json">(.*?)</script>', re.S)
_LD_JSON_RE = re.compile(
    r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
# The comment flyout carries the three values the comment endpoint needs.
_COMMENT_ANCHOR_RE = re.compile(
    r'data-article-id="(\d+)"\s+data-article-type="(\w+)"\s+data-comment-count="(\d+)"')
_CURSOR_RE = re.compile(r'data-pagination-cursor="([^"]+)"')
_LAST_INDEX_RE = re.compile(r'data-last-comment-index="(\d+)"')
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.S)
_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub("", fragment))).strip()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def json_island(page: str, island_id: str) -> dict | None:
    """Return the parsed `<script id="…" type="application/json">` island."""
    for m in _JSON_ISLAND_RE.finditer(page):
        if m.group(1) == island_id:
            try:
                return json.loads(m.group(2))
            except ValueError:
                return None
    return None


def news_article_ld(page: str) -> dict | None:
    """The NewsArticle block out of the several ld+json blocks a page carries."""
    for m in _LD_JSON_RE.finditer(page):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("@type") in ("NewsArticle", "Article"):
            return d
    return None


# --------------------------------------------------------------------------- #
# Article body
# --------------------------------------------------------------------------- #

class _BodyParser(HTMLParser):
    """Collect the article's own content nodes.

    Astro marks every body node with the `article-element` class, which is what
    makes this tractable: ads, teasers to other stories, newsletter boxes and
    the comment flyout all live in the same DOM and would otherwise be scraped
    in as if they were the article. Only paragraphs, crossheads and images are
    kept; `aside` (DynamicTeaser — a link to a different article) is skipped for
    the same reason `_SKIP_ELEMENTS` exists in the Next.js parser.
    """

    KEEP = {"p": "p", "h2": "h2", "h3": "h3", "h4": "h4", "blockquote": "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.images: list[ParsedImage] = []
        self.text_blocks = 0     # paragraphs/crossheads actually served
        self._depth = 0          # >0 while inside a kept text node
        self._tag: str | None = None
        self._buf: list[str] = []
        self._fig = 0            # >0 while inside a kept <figure>
        self._cap: list[str] = []
        self._credit: list[str] = []
        self._in_cap = 0
        self._in_credit = 0
        self._pending: dict | None = None

    # -- helpers ---------------------------------------------------------- #
    @staticmethod
    def _classes(attrs) -> set[str]:
        for k, v in attrs:
            if k == "class" and v:
                return set(v.split())
        return set()

    @staticmethod
    def _attr(attrs, name) -> str | None:
        for k, v in attrs:
            if k == name:
                return v
        return None

    # -- HTMLParser hooks ------------------------------------------------- #
    def handle_starttag(self, tag, attrs):
        cls = self._classes(attrs)

        if self._fig:
            self._fig += 1 if tag == "figure" else 0
            if tag == "img" and self._pending is not None and not self._pending.get("src"):
                # data-image-url is the unresized original; src carries the same
                # URL but srcset variants would pin us to one rendition.
                self._pending["src"] = (self._attr(attrs, "data-image-url")
                                        or self._attr(attrs, "src"))
                self._pending["alt"] = self._attr(attrs, "alt")
            elif tag == "figcaption":
                self._in_cap += 1
            elif self._in_cap and "contentcredit" in cls:
                self._in_credit += 1
            return

        if self._depth:
            self._depth += 1 if tag == self._tag else 0
            return

        if "article-element" not in cls:
            return
        if tag == "figure":
            self._fig = 1
            self._pending = {"src": None, "alt": None}
            return
        if tag in self.KEEP:
            self._depth = 1
            self._tag = tag
            self._buf = []

    def handle_endtag(self, tag):
        if self._fig:
            if tag == "figcaption":
                self._in_cap = max(0, self._in_cap - 1)
            elif tag == "div" and self._in_credit:
                self._in_credit = max(0, self._in_credit - 1)
            elif tag == "figure":
                self._fig -= 1
                if self._fig == 0:
                    self._flush_figure()
            return
        if self._depth and tag == self._tag:
            self._depth -= 1
            if self._depth == 0:
                text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
                if text:
                    self.parts.append(f"<{self._tag}>{html.escape(text)}</{self._tag}>")
                    self.text_blocks += 1
                self._tag, self._buf = None, []

    def handle_data(self, data):
        if self._in_credit:
            self._credit.append(data)
        elif self._in_cap:
            self._cap.append(data)
        elif self._depth:
            self._buf.append(data)

    def _flush_figure(self) -> None:
        p, self._pending = self._pending, None
        cap = re.sub(r"\s+", " ", "".join(self._cap)).strip() or None
        credit = re.sub(r"\s+", " ", "".join(self._credit)).strip() or None
        self._cap, self._credit = [], []
        if not p or not p.get("src"):
            return
        pos = len(self.images)
        full = " — ".join(x for x in (cap, credit) if x) or None
        self.images.append(ParsedImage(
            orig_url=p["src"], role="hero" if pos == 0 else "inline", position=pos,
            alt_text=p.get("alt") or cap, caption=full,
        ))
        self.parts.append(
            f'<figure><img src="{html.escape(p["src"])}" '
            f'alt="{html.escape(p.get("alt") or "")}">'
            + (f"<figcaption>{html.escape(full)}</figcaption>" if full else "")
            + "</figure>")


def parse_body(page: str) -> tuple[str, list[ParsedImage], int]:
    """Extract body HTML, images, and how many text blocks were actually served.

    That last number is the paywall signal. A premium article renders its
    figures and captions but not one paragraph, so a body built from markup
    alone comes back looking like a very short article instead of an absent
    one. The caller uses the count to tell those apart.
    """
    m = re.search(r"<article[^>]*\bdata-article-body\b.*?</article>", page, re.S)
    scope = m.group(0) if m else page
    p = _BodyParser()
    p.feed(scope)
    p.close()
    return "\n".join(p.parts), p.images, p.text_blocks


def parse_article(page: str, url: str) -> ParsedArticle | None:
    """Build a ParsedArticle from a rendered Astro article page."""
    meta = json_island(page, "page-data") or {}
    ld = news_article_ld(page) or {}
    article_id = str(meta.get("articleId") or ld.get("articleId") or "") or None
    if not article_id:
        return None

    authors = ld.get("author")
    if isinstance(authors, dict):
        authors = [authors]
    authors = [a for a in (authors or []) if isinstance(a, dict)]
    author = ", ".join(a.get("name", "").strip() for a in authors if a.get("name"))
    author = author or (meta.get("authorName") or None)

    body_html, images, text_blocks = parse_body(page)
    # With no paragraph served there is no body — only the picture captions,
    # which must not be stored as if they were the article.
    body_text = (_strip_html(body_html) or None) if text_blocks else None
    paywalled = ld.get("isAccessibleForFree") is False or str(meta.get("articlePremium")) == "1"

    if not images and ld.get("thumbnailUrl"):
        images.append(ParsedImage(orig_url=ld["thumbnailUrl"], role="hero", position=0))

    # The desk path is richer than the ld+json section: "savoirs/sciences".
    section = meta.get("articleCategory") or ld.get("articleSection")
    if meta.get("articleSubcategory"):
        section = f"{section}/{meta['articleSubcategory']}" if section else meta["articleSubcategory"]

    # `authorType` is not exposed here the way Next.js exposed it, so agency
    # detection falls back to matching the byline and a trailing "(AFP)".
    source = _detect_source({}, [{"name": a.get("name")} for a in authors], body_text)

    comment_count = None
    anchor = _COMMENT_ANCHOR_RE.search(page)
    if anchor:
        comment_count = int(anchor.group(3))

    return ParsedArticle(
        url=ld.get("url") or meta.get("canonicalUrl") or url,
        source_key=article_id,
        headline=ld.get("headline") or meta.get("articleTitle"),
        subhead=ld.get("description") or None,
        author=author or None,
        source=source,
        section=section,
        lang=ld.get("inLanguage") or meta.get("tenantLang") or "fr",
        published_at=_parse_dt(ld.get("datePublished")),
        updated_at=_parse_dt(ld.get("dateModified")),
        body_text=body_text,
        body_html=body_html or None,
        comment_count=comment_count,
        images=images,
        raw_meta={
            "platform": "astro",
            "articleType": anchor.group(2) if anchor else meta.get("articleType"),
            "alternativeHeadline": ld.get("alternativeHeadline"),
            "titleHeader": meta.get("articleTitle"),
            "category": meta.get("articleCategory"),
            "subcategory": meta.get("articleSubcategory"),
            "premium": meta.get("articlePremium"),
            "isAccessibleForFree": ld.get("isAccessibleForFree"),
            # True when the page served no body at all. Comments are public
            # either way, so such an article is still worth tracking — it just
            # cannot contribute its text.
            "paywalled": paywalled,
            "bodyWithheld": bool(paywalled and not text_blocks),
            "textBlocks": text_blocks,
            "readingTimeSeconds": meta.get("articleReadingTime"),
            "keywords": ld.get("keywords"),
            "unityTags": meta.get("unity_tags"),
            "userNeeds": meta.get("userNeeds"),
            "authors": [{"name": a.get("name"), "id": a.get("@id")} for a in authors],
            "authorId": meta.get("authorId"),
        },
    )


# --------------------------------------------------------------------------- #
# Comments
# --------------------------------------------------------------------------- #

class _CommentParser(HTMLParser):
    """Pull the comment records out of a rendered comment fragment.

    One record is a `<section class="CommentItem">` whose inner wrapper carries
    the UUID and the nickname; inside it are a `<time datetime>`, a `.text`
    block whose paragraphs are the comment, an optional `.parent-nickname` if
    the comment is a reply, and three vote counters.
    """

    # Tags that never close, so they must not go on the element stack.
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
            "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self._cur: dict | None = None
        self._stack: list[str] = []
        # (stack depth at entry, field) for each region we are inside. The reply
        # marker is nested *inside* the body block, so this has to be a stack:
        # with a single current-sink the marker's closing tag ended the body too
        # and every reply came out with no text.
        self._sinks: list[tuple[int, str]] = []
        self._item_depth = -1
        self._text_parts: list[str] = []

    @staticmethod
    def _classes(attrs) -> set[str]:
        for k, v in attrs:
            if k == "class" and v:
                return set(v.split())
        return set()

    @staticmethod
    def _attr(attrs, name) -> str | None:
        for k, v in attrs:
            if k == name:
                return v
        return None

    @property
    def _sink(self) -> str | None:
        return self._sinks[-1][1] if self._sinks else None

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)   # self-closing: never pushed, never popped

    def handle_starttag(self, tag, attrs):
        cls = self._classes(attrs)
        cid = self._attr(attrs, "id") or ""
        void = tag in self.VOID
        if not void:
            self._stack.append(tag)
        depth = len(self._stack)

        if cid.startswith("comment-item-"):
            self._flush()          # defensive: an unterminated previous item
            self._cur = {
                "id": cid[len("comment-item-"):],
                "nickname": self._attr(attrs, "data-nickname"),
                "parent_nickname": None, "posted_at": None,
                "paragraphs": [], "up": None, "down": None, "bulb": None,
            }
            self._item_depth = depth
            self._text_parts = []
            return
        if self._cur is None:
            return

        if tag == "time" and self._cur["posted_at"] is None:
            self._cur["posted_at"] = self._attr(attrs, "datetime")
        elif void:
            return
        elif "parent-nickname" in cls:
            self._sinks.append((depth, "parent"))
        elif "text" in cls and not any(s == "text" for _, s in self._sinks):
            self._sinks.append((depth, "text"))
        elif "upvotes" in cls:
            self._sinks.append((depth, "up"))
        elif "downvotes" in cls:
            self._sinks.append((depth, "down"))
        elif "lightbulbs" in cls:
            self._sinks.append((depth, "bulb"))
        elif self._sink == "text" and tag == "p":
            self._text_parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        # Unwind to the matching open tag; malformed nesting must not desync the
        # depths the sinks are keyed on.
        if tag in self._stack:
            while self._stack and self._stack.pop() != tag:
                pass
        depth = len(self._stack)
        while self._sinks and self._sinks[-1][0] > depth:
            self._sinks.pop()
        if self._cur is not None and depth < self._item_depth:
            self._flush()

    def handle_data(self, data):
        sink = self._sink
        if self._cur is None or not sink:
            return
        if sink == "text":
            self._text_parts.append(data)
        elif sink == "parent":
            self._cur["parent_nickname"] = (data or "").strip().lstrip("@").strip() or None
        else:
            v = (data or "").strip()
            if v.isdigit():
                self._cur[sink] = int(v)

    def close(self):
        super().close()
        self._flush()              # a fragment may end mid-item

    def _flush(self) -> None:
        cur, self._cur = self._cur, None
        self._sinks, self._item_depth = [], -1
        if not cur:
            return
        text = "".join(self._text_parts)
        # The reply marker is rendered inside the same .text block; it is
        # metadata, not something the commenter typed, so it is not body text.
        if cur["parent_nickname"]:
            text = text.replace("@" + cur["parent_nickname"], "", 1)
        paras = [re.sub(r"[ \t]+", " ", p).strip() for p in text.split("\n")]
        cur["paragraphs"] = [p for p in paras if p]
        self._text_parts = []
        self.items.append(cur)


def parse_comments(fragment: str) -> tuple[list[ParsedComment], str | None, int | None]:
    """Parse one rendered comment page.

    Returns (comments, next_pagination_cursor, last_comment_index). A cursor of
    None means this was the last page.
    """
    body = _STYLE_RE.sub("", _SCRIPT_RE.sub("", fragment))
    p = _CommentParser()
    p.feed(body)
    p.close()

    out: list[ParsedComment] = []
    for it in p.items:
        text = "\n\n".join(it["paragraphs"]) or None
        votes = {k: it[k] for k in ("up", "down", "bulb") if it[k] is not None}
        out.append(ParsedComment(
            source_key=it["id"],
            author_nick=it["nickname"],
            author_key=None,          # nickname is all the platform exposes
            body_text=text,
            body_html="".join(f"<p>{html.escape(x)}</p>" for x in it["paragraphs"]) or None,
            # The fragment names the parent only by nickname, so there is no id
            # to link to here. The caller resolves what it can; the raw marker
            # is kept either way so an unresolved reply is still visible as one.
            parent_source_key=None,
            posted_at=_parse_dt(it["posted_at"]),
            like_count=it["up"],
            reply_count=None,         # replies are flattened, not counted
            raw_meta={"votes": votes, "reply_to_nickname": it["parent_nickname"]},
        ))

    cursor = _CURSOR_RE.search(body)
    idx = _LAST_INDEX_RE.findall(body)
    return out, (cursor.group(1) if cursor else None), (int(idx[-1]) if idx else None)


def link_replies(comments: list[ParsedComment]) -> None:
    """Best-effort parent linkage, in place.

    The platform gives a reply only the nickname it answers. That resolves to a
    comment id when exactly one earlier comment in the thread carries that
    nickname; when the same nickname appears more than once above the reply, the
    honest answer is that we do not know which, and the link is left unset
    rather than guessed at the nearest one.
    """
    by_nick: dict[str, list[str]] = {}
    for c in sorted(comments, key=lambda x: (x.posted_at is None, x.posted_at)):
        nick = (c.raw_meta or {}).get("reply_to_nickname")
        if nick:
            candidates = by_nick.get(nick) or []
            if len(candidates) == 1:
                c.parent_source_key = candidates[0]
            else:
                c.raw_meta["parent_ambiguous"] = len(candidates)
        if c.author_nick:
            by_nick.setdefault(c.author_nick, []).append(c.source_key)


# --------------------------------------------------------------------------- #
# Source
# --------------------------------------------------------------------------- #

class TxAstroSource(Source):
    """TX Group title served by Astro. Subclasses set slug/name/base_url."""

    comment_system = "native"

    # 24 heures and the Tribune de Genève are two front-ends over one content
    # pool AND one comment backend: the same article id on both sites returns
    # the same thread, comment UUIDs included. They are therefore one community
    # of commenters, not two — verified by fetching one article's thread from
    # both hosts and getting identical ids and nicknames back.
    community = "tx-romandie"
    # Those ids are UUIDs, unique across the whole backend, so a comment seen
    # through either title is one row rather than one per title.
    comment_ids_global = True

    # Section fronts crawled alongside the homepage. The homepage surfaces the
    # day's selection only; without these, whole desks are never seen.
    sections: tuple[str, ...] = ()
    comment_sort = "newestFirst"
    _comment_max = 5000       # safety cap on comments fetched per article
    _comment_max_pages = 60

    @property
    def comments_supported(self) -> bool:
        # The Astro comment endpoints need no per-title configuration; if the
        # adapter runs at all, comments are collectable.
        return True

    def _api(self, path: str) -> str:
        return f"{self.base_url}/api/content/{path}"

    async def discover(self, fetcher) -> list[str]:
        seen: dict[str, None] = {}
        for path in ("/",) + tuple(self.sections):
            try:
                resp = await fetcher.get(self.base_url + path)
            except Exception as exc:
                log.warning("[%s] front %s failed: %s", self.slug, path, exc)
                continue
            if resp.status != 200:
                log.warning("[%s] front %s returned %s", self.slug, path, resp.status)
                continue
            for href in _ARTICLE_HREF_RE.findall(resp.text()):
                seen.setdefault(self.base_url + href, None)
        log.info("[%s] discovered %d article URLs", self.slug, len(seen))
        return list(seen)

    async def fetch_article(self, fetcher, url: str) -> ParsedArticle | None:
        resp = await fetcher.get(url)
        if resp.status != 200:
            return None
        page = resp.text()
        article = parse_article(page, url)
        if article is None:
            log.debug("[%s] no page-data/ld+json at %s", self.slug, url)
            return None
        # The comment endpoint wants the article's own type ("article",
        # "slideshow", …), which only the flyout markup states.
        anchor = _COMMENT_ANCHOR_RE.search(page)
        if anchor:
            article.raw_meta["commentArticleType"] = anchor.group(2)
        return article

    async def fetch_comments(self, fetcher, article: ParsedArticle) -> list[ParsedComment]:
        """Fetch the whole thread from the site's own comment endpoints.

        These are POST endpoints under /api/content/ that render the comment
        list as an HTML fragment — the same documents a reader's browser loads
        when the comment flyout opens. robots.txt disallows /api/ for generic
        agents; the user has explicitly opted to collect comments, so these pass
        force_allow=True, and the per-host politeness delay still applies.
        """
        if not article.source_key:
            return []
        if article.comment_count == 0:
            return []

        meta = article.raw_meta or {}
        payload = {
            "articleId": article.source_key,
            "articleType": meta.get("commentArticleType") or meta.get("articleType") or "article",
            "commentCount": article.comment_count or 0,
            "currentUrl": article.url,
            "sortOrder": self.comment_sort,
        }
        resp = await fetcher.post_json(self._api("load-comment-container"), payload,
                                       headers={"Referer": article.url}, force_allow=True)
        if resp.status != 200:
            log.warning("[%s] comment container %s for %s",
                        self.slug, resp.status, article.source_key)
            return []

        collected: list[ParsedComment] = []
        by_id: set[str] = set()

        def absorb(page_comments: list[ParsedComment]) -> int:
            added = 0
            for c in page_comments:
                if c.source_key and c.source_key not in by_id:
                    by_id.add(c.source_key)
                    collected.append(c)
                    added += 1
            return added

        page, cursor, last_index = parse_comments(resp.text())
        absorb(page)

        pages = 0
        while cursor and pages < self._comment_max_pages and len(collected) < self._comment_max:
            pages += 1
            # userInfo is required by the endpoint but describes the *reader*;
            # empty is the anonymous case, which is what we are.
            more = {"articleId": article.source_key, "sortOrder": self.comment_sort,
                    "userInfo": {}, "paginationCursor": cursor}
            if last_index is not None:
                more["lastCommentIndex"] = last_index
            resp = await fetcher.post_json(self._api("comment-list"), more,
                                           headers={"Referer": article.url},
                                           force_allow=True)
            if resp.status != 200:
                log.warning("[%s] comment page %s for %s (after %d)",
                            self.slug, resp.status, article.source_key, len(collected))
                break
            page, cursor, last_index = parse_comments(resp.text())
            if absorb(page) == 0:
                break     # a page that adds nothing new would loop forever

        link_replies(collected)
        if article.comment_count and len(collected) < article.comment_count:
            log.info("[%s] %s: got %d of %d comments", self.slug,
                     article.source_key, len(collected), article.comment_count)
        return collected
