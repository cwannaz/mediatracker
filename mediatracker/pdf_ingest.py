"""One-shot ingest of archived article PDFs (printed over many years, in changing
layouts) via an LLM in the loop.

Because the PDF formats vary wildly over time — and some have no text layer at all
(printed as images) — there is deliberately NO generic PDF parser here. An LLM
reads each PDF visually and emits a normalized record; this module only takes that
record and writes it to the same tables as the live crawler, tagged origin='pdf'.

Record shape (one per PDF), as JSON:
  {
    "source_file": "<basename or path of the PDF>",
    "slug": "lematin",
    "parsed": {
      "url": "https://www.lematin.ch/…"       # optional; when present the archive
                                               # row merges with the live article
      "headline": "...", "subhead": "...",
      "author": "...", "source": "AFP",        # source = news agency if shown
      "section": "...", "lang": "fr",
      "published_at": "2012-04-08" | ISO | null,
      "body_text": "full article text",
      "comments": [
        {"author_nick": "...", "body_text": "...",
         "posted_at": "2012-04-07 23:44" | ISO | null,
         "like_count": 1, "parent_nick": null,
         "reactions": {"smart": 6, ...}         # optional, if the layout shows them
        }, ...
      ]
    }
  }

Usage:
  python3 -m mediatracker.pdf_ingest records.json            # ingest
  python3 -m mediatracker.pdf_ingest records.json --dry-run  # validate only
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db, ids
from .config import load_config

log = logging.getLogger(__name__)

# Le Matin timestamps in the PDFs are Swiss local time with no zone printed.
_LOCAL_TZ = ZoneInfo("Europe/Zurich")


def parse_dt(value) -> datetime | None:
    """Lenient timestamp parse for LLM-provided dates across many PDF layouts.
    Naive results are localized to Europe/Zurich (the papers' timezone) so
    time-of-day analysis is correct."""
    if not value:
        return None
    dt = value if isinstance(value, datetime) else _try_formats(str(value).strip())
    if dt is None:
        log.warning("unparseable date %r; keeping null", value)
        return None
    return dt.replace(tzinfo=_LOCAL_TZ) if dt.tzinfo is None else dt


def _try_formats(s: str) -> datetime | None:
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    for f in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
              "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%d.%m.%Y, %H:%M",
              "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


# TX Group urls carry the article's native numeric id, both in article urls
# (/story/<slug>-<id>) and in comment-page urls (/comment/<id>). That id is the
# reliable join key to an already-crawled live article.
_NATIVE_ID_RE = re.compile(r"/(?:comment|story)/(?:.*?-)?(\d{6,})(?:\D|$)")


def native_article_id(url: str | None) -> str | None:
    if not url:
        return None
    m = _NATIVE_ID_RE.search(url)
    return m.group(1) if m else None


def _article_identity(conn, slug: str, parsed: dict,
                      source_file: str) -> tuple[str, str, str | None, bool]:
    """Return (article_id, canonical_url, source_key, merged_with_live).

    Merge order:
      1. the article's native numeric id already present in the DB (live crawl) —
         reuse that row so archive and live history join;
      2. a complete article url printed on the capture;
      3. a stable pdf:// key derived from the filename (URL lost / page gone).
    """
    url = (parsed.get("url") or "").strip() or None
    native = native_article_id(url)

    if native and conn is not None:
        hit = db.find_article_by_source_key(conn, ids.journal_id(slug), native)
        if hit:
            log.info("merging %s into live article %s", os.path.basename(source_file), native)
            return hit[0], hit[1], native, True

    if url and "/story/" in url:
        return ids.article_id(slug, url), ids.canonical_url(url), native, False

    key = f"pdf://{slug}/{os.path.basename(source_file)}"
    return ids._sha(f"article:{slug}:{key}"), key, native, False


def ingest_record(conn, record: dict, stats: dict) -> None:
    slug = record.get("slug") or "lematin"
    source_file = record["source_file"]
    parsed = record["parsed"]
    base = os.path.basename(source_file)

    jid = ids.journal_id(slug)
    aid, canon, native, merged = _article_identity(conn, slug, parsed, source_file)
    if merged:
        stats["merged_with_live"] = stats.get("merged_with_live", 0) + 1

    chash = ids.content_hash(parsed.get("headline") or "", parsed.get("subhead") or "",
                             parsed.get("body_text") or "", parsed.get("author") or "")
    raw_meta = {"ingested_by": "pdf-llm", "source_file": base,
                "archive_url": parsed.get("url"), "note": parsed.get("note")}

    # An archived capture must never downgrade a live article's origin — but
    # "merged" only means we found an article with the same native id, and that
    # article may itself be an earlier PDF capture. Only a real crawled URL
    # makes this live; two archives of the same page stay an archive.
    origin = "live" if merged and not canon.startswith("pdf://") else "pdf"
    db.upsert_article(conn, aid=aid, journal_id=jid, canonical_url=canon,
                      source_key=native, origin=origin, source_file=base)
    snap_id = db.insert_article_snapshot(conn, article_id=aid, content_hash=chash, fields={
        "published_at": parse_dt(parsed.get("published_at")),
        "updated_at": None,
        "headline": parsed.get("headline"),
        "subhead": parsed.get("subhead"),
        "author": parsed.get("author"),
        "source": parsed.get("source"),
        "section": parsed.get("section"),
        "lang": parsed.get("lang") or "fr",
        "body_text": parsed.get("body_text"),
        "body_html": None,
        "comment_count": len(parsed.get("comments") or []),
        "raw_meta": raw_meta,
    })
    if snap_id is not None:
        stats["article_snapshots"] += 1
    stats["articles"] += 1

    for c in parsed.get("comments") or []:
        _ingest_comment(conn, slug, aid, base, c, stats)


def _ingest_comment(conn, slug, aid, base, c, stats) -> None:
    nick = c.get("author_nick")
    body = c.get("body_text") or ""
    posted = parse_dt(c.get("posted_at"))
    cid = ids.synthetic_comment_id(aid, nick or "", str(posted or c.get("posted_at") or ""), body)
    parent = None
    if c.get("parent_nick"):
        # Threading in old PDFs is by nickname; keep the raw ref, no hard link.
        parent = None
    reactions = c.get("reactions") or {}
    reactions_sig = ";".join(f"{k}={reactions[k]}" for k in sorted(reactions))
    chash = ids.content_hash(body, str(c.get("like_count") or ""), reactions_sig)

    db.upsert_comment(conn, cid=cid, article_id=aid, source_key=None, parent_id=parent,
                      author_nick=nick, author_key=None)
    ok = db.insert_comment_snapshot(conn, comment_id=cid, content_hash=chash, fields={
        "posted_at": posted,
        "body_text": body,
        "body_html": None,
        "like_count": c.get("like_count"),
        "reply_count": None,
        "raw_meta": {"ingested_by": "pdf-llm", "source_file": base,
                     "reactions": reactions or None, "parent_nick": c.get("parent_nick")},
    })
    if ok:
        stats["comment_snapshots"] += 1
    stats["comments"] += 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mediatracker.pdf_ingest")
    ap.add_argument("records", help="JSON file: a record object or a list of them")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    data = json.load(open(args.records, encoding="utf-8"))
    records = data if isinstance(data, list) else [data]

    stats = {"articles": 0, "article_snapshots": 0, "comments": 0, "comment_snapshots": 0}
    if args.dry_run:
        for r in records:
            p = r["parsed"]
            print(f"[dry] {os.path.basename(r['source_file'])}: "
                  f"{(p.get('headline') or '')[:60]!r} · {len(p.get('comments') or [])} comments "
                  f"· date={p.get('published_at')}")
        return 0

    conn = db.connect(load_config())
    if conn is None:
        print("ERROR: no Postgres connection", file=sys.stderr)
        return 1
    db.ensure_schema(conn)
    for r in records:
        try:
            ingest_record(conn, r, stats)
        except Exception as exc:
            log.error("failed on %s: %s", r.get("source_file"), exc)
    print("ingested:", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
