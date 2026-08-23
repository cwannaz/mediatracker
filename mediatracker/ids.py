"""Stable, content-derived identifiers.

Every journal, article and comment gets a deterministic id so that re-seeing the
same thing on a later poll is idempotent (no duplicate rows, just a new
snapshot). Ids are hex SHA-256 digests over normalized inputs.

Design note (point-in-time honesty): ids never depend on poll time or on mutable
fields like comment counts — only on identity (canonical URL, source id, slug).
Mutable content lives in *_snapshot rows keyed by content_hash instead.
"""
from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query params that are pure tracking noise and must not affect article identity.
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_EXACT = {
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "mc_cid", "mc_eid",
    "igshid", "ref", "ref_src", "cmpid", "ncid", "spm", "yclid", "_hsenc",
    "_hsmi", "vgo_ee", "wt_zmc",
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_url(url: str) -> str:
    """Normalize a URL for identity: lowercase scheme+host, drop fragment and
    tracking params, sort remaining query, strip a trailing slash on the path."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking(k)
    ]
    kept.sort()
    query = urlencode(kept)
    return urlunsplit((scheme, netloc, path, query, ""))


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k in _TRACKING_EXACT or any(k.startswith(p) for p in _TRACKING_PREFIXES)


def journal_id(slug: str) -> str:
    return _sha(f"journal:{slug.strip().lower()}")


def article_id(journal_slug: str, url: str) -> str:
    return _sha(f"article:{journal_slug.strip().lower()}:{canonical_url(url)}")


def comment_id(journal_slug: str, art_id: str, source_comment_id: str) -> str:
    """Preferred: keyed by the comment id the source system assigns."""
    return _sha(f"comment:{journal_slug.strip().lower()}:{art_id}:{source_comment_id}")


def synthetic_comment_id(art_id: str, author: str, posted_at: str, body: str) -> str:
    """Fallback when a source exposes no per-comment id: derive one from the
    tuple that uniquely identifies the comment on the page."""
    return _sha(f"scomment:{art_id}:{author}:{posted_at}:{body}")


def content_hash(*parts: str) -> str:
    """Hash of the mutable content of a snapshot; two snapshots with the same
    content_hash are identical and the second is not stored."""
    return _sha("\x1f".join(p or "" for p in parts))
