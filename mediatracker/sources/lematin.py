"""Le Matin (lematin.ch) adapter.

Le Matin is a TX Group / Tamedia Next.js site; all parsing lives in the shared
`TamediaSource` base. Articles (`/story/…`) are parsed from `__NEXT_DATA__` and
are robots-allowed. Comments are pending a crawl-posture decision (see
tamedia.py / DOCTRINE.md).
"""
from __future__ import annotations

from . import register
from .tamedia import TamediaSource


@register
class LeMatin(TamediaSource):
    slug = "lematin"
    name = "Le Matin"
    base_url = "https://www.lematin.ch"
