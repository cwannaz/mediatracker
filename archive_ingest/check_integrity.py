#!/usr/bin/env python3
"""Guard against cross-contamination in the rescue pass.

Rescue agents copied PDFs into a shared scratchpad; at least one file was
overwritten there by a concurrent agent. If an agent then read the wrong file,
its record would hold another capture's content under the right filename.

Heuristic: a capture's filename carries its headline ("<date> - Commentaires à
<title> - Le Matin.pdf"). Compare the significant words of the filename with the
record's headline. A record whose headline shares no meaningful word with its
filename is suspicious and gets re-checked by hand.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata

BASE = "/mnt/storage/Projects/MediaTracker/pdf-ingest"

STOP = {
    "commentaires", "le", "la", "les", "matin", "lematin", "ch", "pdf", "docx",
    "a", "à", "de", "des", "du", "un", "une", "et", "en", "au", "aux", "pour",
    "sur", "dans", "par", "avec", "que", "qui", "ne", "pas", "plus", "son",
    "ses", "est", "sont", "the", "of", "monde", "suisse", "-", "d", "l", "il",
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower()


def words(s: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", norm(s)) if len(w) > 3 and w not in STOP}


def main() -> int:
    inv = json.load(open(f"{BASE}/inventory.json"))
    suspicious, checked = [], 0
    for meta in inv:
        try:
            rec = json.load(open(f"{BASE}/records/{meta['stem']}.json", encoding="utf-8"))
        except Exception:
            continue
        p = rec.get("parsed") or {}
        headline = p.get("headline") or ""
        if not headline:
            continue
        fw, hw = words(meta["file"]), words(headline)
        if not fw or not hw:
            continue
        checked += 1
        if not (fw & hw):
            # Fall back to the body/comment text before crying wolf.
            blob = norm((p.get("body_text") or "")[:2000])
            if not any(w in blob for w in fw):
                suspicious.append((meta["file"], headline))

    print(f"checked {checked} records; {len(suspicious)} suspicious")
    for f, h in suspicious:
        print(f"  FILE : {f}")
        print(f"  HEAD : {h}\n")
    return 1 if suspicious else 0


if __name__ == "__main__":
    sys.exit(main())
