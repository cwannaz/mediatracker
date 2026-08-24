"""24 heures (24heures.ch) adapter.

24 heures has left the TX Group Next.js platform that `tamedia.py` targets and
is now an Astro site, so it shares `TxAstroSource` rather than `TamediaSource`.
The module name avoids a leading digit; the stable slug is "24heures".
"""
from __future__ import annotations

from . import register
from .tx_astro import TxAstroSource


@register
class VingtQuatreHeures(TxAstroSource):
    slug = "24heures"
    name = "24 heures"
    base_url = "https://www.24heures.ch"
    # The homepage carries the day's selection only. These are the desk fronts
    # the breadcrumbs use, so a story that never reaches the front page is
    # still seen.
    sections = ("/vaud-regions", "/suisse", "/monde", "/economie", "/sports",
                "/culture", "/savoirs", "/vivre")
