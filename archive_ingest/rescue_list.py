#!/usr/bin/env python3
"""Find records that need a second, VISUAL pass.

The text lane (pdftotext) misclassifies two cases:
  * captures whose extractable text is only navigation chrome (the article and
    comments are images) -> record has no comments and no body;
  * old print layouts where comment bodies were dropped at print time but
    nicknames/timestamps survived -> comments exist with body_text = null.

Both are recoverable by reading the PDF pages as images. This writes rescue
manifests in the same shape the extraction agents already consume.
"""
from __future__ import annotations

import json
import os

BASE = "/mnt/storage/Projects/MediaTracker/pdf-ingest"
SRC = "/mnt/storage/Ariane/MyDocuments/Private/Forums/Le Matin"


def main() -> None:
    inv = {r["stem"]: r for r in json.load(open(f"{BASE}/inventory.json"))}
    need = []
    for stem, meta in inv.items():
        path = f"{BASE}/records/{stem}.json"
        if not os.path.exists(path):
            continue
        try:
            rec = json.load(open(path, encoding="utf-8"))
        except Exception:
            need.append((stem, meta, "unparseable record"))
            continue
        p = rec.get("parsed") or {}
        comments = p.get("comments") or []
        empty_bodies = [c for c in comments if not c.get("body_text")]

        if meta["ext"] != "pdf":
            continue                                   # docx: nothing to re-read
        if not comments and not p.get("body_text"):
            need.append((stem, meta, "no content extracted"))
        elif comments and len(empty_bodies) == len(comments):
            need.append((stem, meta, f"{len(comments)} comments with no body"))
        elif empty_bodies and len(empty_bodies) > 0.5 * len(comments):
            need.append((stem, meta, f"{len(empty_bodies)}/{len(comments)} bodies missing"))

    print(f"{len(need)} records need a visual rescue pass")
    for stem, meta, why in need:
        print(f"  {meta['file'][:62]:<64} {why}")

    # Balanced rescue manifests (~5 files each)
    per = 5
    batches = [need[i:i + per] for i in range(0, len(need), per)]
    os.makedirs(f"{BASE}/manifests", exist_ok=True)
    for i, b in enumerate(batches, 1):
        json.dump([{"file": m["file"], "stem": s,
                    "pdf_path": os.path.join(SRC, m["file"]),
                    "pages": m.get("pages"), "reason": why}
                   for s, m, why in b],
                  open(f"{BASE}/manifests/rescue_{i:02d}.json", "w"),
                  ensure_ascii=False, indent=1)
    print(f"wrote {len(batches)} rescue manifests")


if __name__ == "__main__":
    main()
