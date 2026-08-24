#!/usr/bin/env python3
"""Validate extracted archive records before ingesting them.

Checks every record file against the extraction contract, cross-checks coverage
against the inventory, and reports anything suspicious (missing files, bad JSON,
absent headlines, unparseable dates, truncated URLs that slipped through).

Usage:  python3 validate.py [--json combined.json]
"""
from __future__ import annotations

import json
import os
import re
import sys

BASE = "/mnt/storage/Projects/MediaTracker/pdf-ingest"
SRC_DIR = "/mnt/storage/Ariane/MyDocuments/Private/Forums/Le Matin"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")


def main() -> int:
    inv = json.load(open(f"{BASE}/inventory.json"))
    expected = {r["stem"]: r["file"] for r in inv}

    records, problems, warnings = {}, [], []
    for stem, fname in expected.items():
        path = f"{BASE}/records/{stem}.json"
        if not os.path.exists(path):
            problems.append(f"MISSING record for {fname}")
            continue
        try:
            rec = json.load(open(path, encoding="utf-8"))
        except Exception as exc:
            problems.append(f"BAD JSON {stem}.json: {exc}")
            continue

        p = rec.get("parsed") or {}
        if rec.get("source_file") != fname:
            warnings.append(f"source_file mismatch in {stem}.json "
                            f"({rec.get('source_file')!r} != {fname!r})")
            rec["source_file"] = fname          # repair: trust the manifest
        if not os.path.exists(os.path.join(SRC_DIR, rec["source_file"])):
            problems.append(f"source_file not on disk: {rec['source_file']}")
        if not p.get("headline"):
            warnings.append(f"no headline: {fname}")
        for field in ("published_at",):
            v = p.get(field)
            if v and not DATE_RE.match(str(v)):
                warnings.append(f"odd {field} {v!r} in {fname}")
        url = p.get("url")
        if url and ("..." in url or "…" in url):
            warnings.append(f"truncated url dropped in {fname}")
            p["url"] = None
        for c in p.get("comments") or []:
            ts = c.get("posted_at")
            if ts and not DATE_RE.match(str(ts)):
                warnings.append(f"odd comment ts {ts!r} in {fname}")
                c["posted_at"] = None
            if not c.get("body_text"):
                warnings.append(f"empty comment body in {fname}")
        records[stem] = rec

    notes = [(r["source_file"], r["parsed"]["note"]) for r in records.values()
             if r["parsed"].get("note")]

    n_comments = sum(len(r["parsed"].get("comments") or []) for r in records.values())
    n_with_body = sum(1 for r in records.values() if r["parsed"].get("body_text"))
    n_with_url = sum(1 for r in records.values() if r["parsed"].get("url"))
    nicks = {c.get("author_nick") for r in records.values()
             for c in (r["parsed"].get("comments") or []) if c.get("author_nick")}

    print(f"records:        {len(records)} / {len(expected)} expected")
    print(f"comments:       {n_comments}")
    print(f"distinct nicks: {len(nicks)}")
    print(f"with body_text: {n_with_body}   with url: {n_with_url}")
    print(f"problems:       {len(problems)}")
    print(f"warnings:       {len(warnings)}")
    for x in problems[:25]:
        print("  PROBLEM:", x)
    for x in warnings[:15]:
        print("  warn:", x)
    if len(warnings) > 15:
        print(f"  … and {len(warnings) - 15} more warnings")
    print(f"records with extractor notes: {len(notes)}")
    for f, n in notes[:20]:
        print(f"  note [{f[:55]}]: {str(n)[:110]}")

    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        json.dump(list(records.values()), open(out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("combined ->", out)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
