"""24 heures (24heures.ch) adapter.

STATUS: stub. Pending a live inspection of 24heures.ch. Like Le Matin it is a
TX Group / Tamedia title and probably shares the same publishing/comment
platform; once confirmed, the shared logic should move to a common base and both
adapters become thin.

Module name avoids a leading digit (illegal for a Python identifier); the slug
"24heures" is the stable id used everywhere else.
"""
from __future__ import annotations

from . import register
from .base import ParsedArticle, ParsedComment, Source


@register
class VingtQuatreHeures(Source):
    slug = "24heures"
    name = "24 heures"
    base_url = "https://www.24heures.ch"
    comment_system = None  # TODO: detect

    async def discover(self, fetcher) -> list[str]:
        raise NotImplementedError("24heures.discover pending site inspection")

    async def fetch_article(self, fetcher, url: str) -> ParsedArticle | None:
        raise NotImplementedError("24heures.fetch_article pending site inspection")

    async def fetch_comments(self, fetcher, article: ParsedArticle) -> list[ParsedComment]:
        raise NotImplementedError("24heures.fetch_comments pending site inspection")
