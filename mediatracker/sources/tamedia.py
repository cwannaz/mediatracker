"""Shared adapter for TX Group / Tamedia journals (lematin.ch, 24heures.ch, …).

These sites are Next.js apps that embed the full article as JSON in a
`<script id="__NEXT_DATA__">` tag, so articles are parsed from structured data
rather than scraped from HTML. Concrete journals subclass `TamediaSource` and set
`slug` / `name` / `base_url`; the parsing is identical.

Article paths look like `/story/<slug>-<numericId>` and are allowed by robots.txt
for a generic user-agent. Comments are a native TX Group community system loaded
client-side from a separate endpoint that robots.txt disallows for generic
agents — see `fetch_comments` and DOCTRINE.md for the (pending) policy.

The parser is split into pure functions (`extract_next_data`, `parse_article`)
so it can be unit-tested against a fixture without any network.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from . import register  # noqa: F401  (re-exported for subclass modules)
from .base import ParsedArticle, ParsedComment, ParsedImage, Source

log = logging.getLogger(__name__)

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)
_STORY_HREF_RE = re.compile(r'href="(/story/[^"?#]+)')
_TAG_RE = re.compile(r"<[^>]+>")

# Body element types that are represented elsewhere (as ParsedArticle fields) or
# are not editorial content — skipped when building the body.
_SKIP_ELEMENTS = {"publishDate", "title", "lead", "authors", "ad", "separator",
                  "newsletter", "recommendations", "banner"}


def extract_next_data(html: str) -> dict | None:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _strip_html(html: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub("", html)).strip()


def _best_variant_src(image: dict) -> str | None:
    """Prefer the full-resolution `base` variant; fall back to the widest one."""
    variants = image.get("variants") or {}
    base = variants.get("base")
    if isinstance(base, dict) and base.get("src"):
        return base["src"]
    best, best_w = None, -1
    for v in variants.values():
        if isinstance(v, dict) and v.get("src") and (v.get("width") or 0) > best_w:
            best, best_w = v["src"], v.get("width") or 0
    return best


def _image_caption(image: dict) -> str | None:
    cap = image.get("caption")
    if isinstance(cap, dict):
        return cap.get("text") or None
    if isinstance(cap, str):
        return cap or None
    return None


def parse_article(data: dict, url: str, *, lang: str = "fr") -> ParsedArticle | None:
    """Build a ParsedArticle from a page's __NEXT_DATA__ dict."""
    try:
        wrapper = data["props"]["pageProps"]["data"]
        content = wrapper["content"]
    except (KeyError, TypeError):
        return None

    meta = content.get("meta", {}) or {}
    article = content.get("article", {}) or {}

    authors = meta.get("authors") or []
    author = ", ".join(a.get("name", "").strip() for a in authors if a.get("name")) or None

    headline = article.get("title") or meta.get("teaser", {}).get("default", {}).get("title")
    subhead = article.get("lead") or None
    if isinstance(subhead, str):
        subhead = _strip_html(subhead) or None

    body_html, images = _build_body(article.get("elements") or [])

    # Many articles carry their lead image only in the teaser object (not as a
    # body element). If the body yielded no image, use the teaser image as hero
    # so reproduction still shows the article's main picture.
    if not images:
        teaser_img = (meta.get("teaser", {}).get("default", {}) or {}).get("image")
        src = _best_variant_src(teaser_img) if isinstance(teaser_img, dict) else None
        if src:
            desc = teaser_img.get("description") if isinstance(teaser_img, dict) else None
            images.append(ParsedImage(orig_url=src, role="hero", position=0,
                                      alt_text=desc, caption=desc or None))

    return ParsedArticle(
        url=url,
        source_key=str(content.get("id") or wrapper.get("id") or "") or None,
        headline=_strip_html(headline) if headline else None,
        subhead=subhead,
        author=author,
        section=meta.get("mainCategoryName") or article.get("titleHeader"),
        lang=lang,
        published_at=_parse_dt(meta.get("published")),
        updated_at=_parse_dt(meta.get("updated")),
        body_text=_strip_html(body_html) or None,
        body_html=body_html or None,
        comment_count=None,  # not exposed in the page; count comes from comments
        images=images,
        raw_meta={
            "kickword": meta.get("kickword"),
            "tags": meta.get("tags"),
            "mainCategoryFullUrlPath": meta.get("mainCategoryFullUrlPath"),
            "urlSlug": meta.get("urlSlug"),
            "wordCount": meta.get("wordCount"),
            "commentingEnabled": (meta.get("settings") or {}).get("commentingEnabled"),
        },
    )


def _build_body(elements: list) -> tuple[str, list[ParsedImage]]:
    parts: list[str] = []
    images: list[ParsedImage] = []
    pos = 0
    for el in elements:
        if not isinstance(el, dict):
            continue
        etype = el.get("type")
        if etype in _SKIP_ELEMENTS:
            continue
        if etype == "image":
            img = el.get("image") or {}
            src = _best_variant_src(img)
            if not src:
                continue
            caption = _image_caption(img)
            credit = img.get("credit")
            images.append(ParsedImage(
                orig_url=src,
                role="hero" if pos == 0 else "inline",
                position=pos,
                alt_text=caption,
                caption=" — ".join(x for x in (caption, credit) if x) or None,
            ))
            fig_cap = " — ".join(x for x in (caption, credit) if x)
            parts.append(
                f'<figure><img src="{src}" alt="{caption or ""}">'
                + (f"<figcaption>{fig_cap}</figcaption>" if fig_cap else "")
                + "</figure>"
            )
            pos += 1
        elif etype == "textBlockArray":
            for item in el.get("items") or []:
                if isinstance(item, dict) and item.get("htmlText"):
                    parts.append(f"<p>{item['htmlText']}</p>")
        elif el.get("htmlText"):  # generic fallback (subheads, quotes, …)
            parts.append(f"<p>{el['htmlText']}</p>")
    return "\n".join(parts), images


class TamediaSource(Source):
    comment_system = "native"  # TX Group native community platform

    async def discover(self, fetcher) -> list[str]:
        resp = await fetcher.get(self.base_url + "/")
        if resp.status != 200:
            log.warning("[%s] homepage returned %s", self.slug, resp.status)
            return []
        paths = sorted(set(_STORY_HREF_RE.findall(resp.text())))
        return [self.base_url + p for p in paths]

    async def fetch_article(self, fetcher, url: str) -> ParsedArticle | None:
        resp = await fetcher.get(url)
        if resp.status != 200:
            return None
        data = extract_next_data(resp.text())
        if data is None:
            log.debug("[%s] no __NEXT_DATA__ at %s", self.slug, url)
            return None
        return parse_article(data, url)

    async def fetch_comments(self, fetcher, article: ParsedArticle) -> list[ParsedComment]:
        # Comments load client-side from a `/comment/` (or *.json) endpoint that
        # robots.txt disallows for a generic user-agent. Left disabled pending an
        # explicit crawl-posture decision (see DOCTRINE.md). Once decided, this
        # is where the comment API is fetched and mapped to ParsedComment.
        return []
