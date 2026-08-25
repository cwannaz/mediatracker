"""Common shapes and the Source adapter contract.

An adapter's job is only to *parse* a journal into these normalized structures.
It never touches Postgres, the blob store, or ids — the ingest pipeline
(pipeline.py) does that uniformly for every source, which is what lets stylometry
compare authors across journals on identical fields.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger(__name__)


@dataclass
class ParsedImage:
    orig_url: str
    role: str = "inline"        # 'hero' | 'inline' | 'thumb'
    position: int = 0
    alt_text: str | None = None
    caption: str | None = None


@dataclass
class ParsedArticle:
    url: str                     # canonical or as-seen; ids.canonical_url normalizes
    source_key: str | None = None
    headline: str | None = None
    subhead: str | None = None
    author: str | None = None       # byline (human authors and/or agency)
    source: str | None = None       # originating news agency (Reuters/AFP/ATS…)
    section: str | None = None
    lang: str | None = None
    published_at: datetime | None = None
    updated_at: datetime | None = None
    body_text: str | None = None
    body_html: str | None = None
    comment_count: int | None = None
    images: list[ParsedImage] = field(default_factory=list)
    raw_meta: dict = field(default_factory=dict)


@dataclass
class ParsedComment:
    source_key: str | None       # comment id from the source, if any
    author_nick: str | None
    body_text: str | None
    author_key: str | None = None
    parent_source_key: str | None = None
    posted_at: datetime | None = None
    body_html: str | None = None
    like_count: int | None = None
    reply_count: int | None = None
    raw_meta: dict = field(default_factory=dict)


class Source:
    """Base class for a journal adapter. Subclasses set the class attributes and
    implement the three async methods. Register with @sources.register."""

    slug: str = ""                 # stable short id, e.g. "lematin"
    name: str = ""                 # human name, e.g. "Le Matin"
    base_url: str = ""             # e.g. "https://www.lematin.ch"
    comment_system: str | None = None  # 'native' | 'coral' | 'disqus' | ...

    # The comment namespace this title's readers write into. Nicknames are only
    # comparable inside one of these: "Marie03" on two different platforms is
    # two people until something proves otherwise, while two titles sharing one
    # comment backend really do share their commenters. Defaults to the slug —
    # a title has its own community unless it says otherwise.
    community: str | None = None

    # True when the source's own comment id identifies a comment across the
    # whole community rather than only within one article. Titles that share a
    # backend then store one row for one comment instead of one per title.
    comment_ids_global: bool = False

    def __init__(self, *, base_url: str | None = None) -> None:
        # Per-journal base_url override (edited in the GUI) shadows the class default.
        if base_url:
            self.base_url = base_url.rstrip("/")

    @property
    def community_key(self) -> str:
        return self.community or self.slug

    @property
    def comments_supported(self) -> bool:
        """Whether this adapter can actually collect comments today.

        The GUI shows a warning when it cannot, so this has to describe the
        adapter rather than any one platform's configuration: it used to be
        read off a Next.js tenant id, which meant every source on a different
        platform was reported as broken however well it worked.
        """
        return False

    async def discover(self, fetcher) -> list[str]:
        """Return candidate article URLs to (re-)check this cycle — typically
        from the homepage, section fronts, RSS feeds or a sitemap."""
        raise NotImplementedError

    async def fetch_article(self, fetcher, url: str) -> ParsedArticle | None:
        """Fetch and parse a single article. Return None to skip (e.g. paywalled
        or not an article)."""
        raise NotImplementedError

    async def fetch_comments(self, fetcher, article: ParsedArticle) -> list[ParsedComment]:
        """Fetch and parse the comment thread for an article. Return [] if none."""
        return []
