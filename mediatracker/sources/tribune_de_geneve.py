"""Tribune de Genève (tdg.ch) adapter.

The same Astro front-end as 24 heures, and — importantly — the same content and
comment backend: 218 of the ~270 articles on the two sites' fronts on a given
day are one article under one id, served by both, with one shared comment
thread. What differs is the local desk: Geneva stories appear only here, Vaud
stories only on 24 heures, and it is that local coverage which draws the two
readerships apart.

So this is a separate *title* over a shared *community*. `TxAstroSource` sets
`community = "tx-romandie"` for both, which is what keeps a syndicated thread
from being counted twice while still recording that both papers published it.
"""
from __future__ import annotations

from . import register
from .tx_astro import TxAstroSource


@register
class TribuneDeGeneve(TxAstroSource):
    slug = "tdg"
    name = "Tribune de Genève"
    base_url = "https://www.tdg.ch"
    # /geneve is this title's local desk where 24 heures has /vaud-regions.
    sections = ("/geneve", "/suisse", "/monde", "/economie", "/sports",
                "/culture", "/savoirs", "/vivre")
