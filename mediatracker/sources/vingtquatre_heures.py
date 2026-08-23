"""24 heures (24heures.ch) adapter.

Another TX Group / Tamedia Next.js title, sharing `TamediaSource`. The module
name avoids a leading digit; the stable slug is "24heures". The shared parser is
assumed to apply (same platform as Le Matin) and should be confirmed against a
live 24heures article before relying on its output.
"""
from __future__ import annotations

from . import register
from .tamedia import TamediaSource


@register
class VingtQuatreHeures(TamediaSource):
    slug = "24heures"
    name = "24 heures"
    base_url = "https://www.24heures.ch"
    comment_tenant_id = None  # TODO: confirm tenantId for api.24heures.ch (comments skipped until set)
