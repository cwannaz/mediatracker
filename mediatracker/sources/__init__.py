"""Per-journal source adapters.

Each journal is one module exposing a Source subclass. A registry maps slug ->
Source so the daemon can iterate configured journals. Adapters return the common
Parsed* shapes from base.py; everything downstream (ids, snapshots, images) is
journal-agnostic.
"""
from __future__ import annotations

from .base import ParsedArticle, ParsedComment, ParsedImage, Source

_REGISTRY: dict[str, type[Source]] = {}


def register(cls: type[Source]) -> type[Source]:
    """Class decorator: add a Source subclass to the registry under its slug."""
    _REGISTRY[cls.slug] = cls
    return cls


def get(slug: str) -> type[Source] | None:
    return _REGISTRY.get(slug)


def all_slugs() -> list[str]:
    return sorted(_REGISTRY)


# Import adapters so they self-register. Add new journals here.
from . import lematin  # noqa: E402,F401
from . import vingtquatre_heures  # noqa: E402,F401

__all__ = [
    "ParsedArticle", "ParsedComment", "ParsedImage", "Source",
    "register", "get", "all_slugs",
]
