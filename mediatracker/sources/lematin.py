"""Le Matin (lematin.ch) adapter.

STATUS: stub. Structure/comment-system detection pending a live inspection of
lematin.ch (article markup, whether the comment thread is server-rendered or a
client-side widget, and the comment API if there is one). Le Matin is a TX Group
/ Tamedia title, so it likely shares a platform with 24heures.ch — the two
adapters may end up sharing a common base.

Fill in discover(), fetch_article() and fetch_comments() once the platform is
known; the surrounding pipeline needs no changes.
"""
from __future__ import annotations

from . import register
from .base import ParsedArticle, ParsedComment, Source


@register
class LeMatin(Source):
    slug = "lematin"
    name = "Le Matin"
    base_url = "https://www.lematin.ch"
    comment_system = None  # TODO: detect ('native' | 'coral' | ...)

    async def discover(self, fetcher) -> list[str]:
        raise NotImplementedError("lematin.discover pending site inspection")

    async def fetch_article(self, fetcher, url: str) -> ParsedArticle | None:
        raise NotImplementedError("lematin.fetch_article pending site inspection")

    async def fetch_comments(self, fetcher, article: ParsedArticle) -> list[ParsedComment]:
        raise NotImplementedError("lematin.fetch_comments pending site inspection")
