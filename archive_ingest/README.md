# Archive ingest (one-shot)

Tooling for the one-shot import of ~350 Le Matin captures printed as PDF/DOCX
between 2011 and 2026. The captures' layouts changed repeatedly and many have no
text layer, so there is deliberately **no generic parser**: an LLM reads each
capture (from `pdftotext` output where a text layer exists, otherwise from the
rendered pages as images) and emits one JSON record per file, which
`mediatracker/pdf_ingest.py` writes to the normal tables with `origin='pdf'`.

Working data lives outside the repo, on the ZFS volume:
`/mnt/storage/Projects/MediaTracker/pdf-ingest/` (`records/`, `text/`,
`manifests/`, `inventory.json`).

| file | role |
|------|------|
| `EXTRACTION_SPEC.md` | the contract each extraction agent follows |
| `RESCUE_SPEC.md`     | second pass: re-read pages visually when the text layer lost the comment bodies (incl. the 2011–2015 forum layout) |
| `rescue_list.py`     | finds records needing that second pass, writes rescue manifests |
| `validate.py`        | validates every record, repairs NBSP filename mismatches, emits `combined.json` |
| `check_integrity.py` | guards against cross-contamination (agents shared a scratchpad) by matching filename words against the extracted headline |
| `report.py`          | post-ingest dataset summary |

Run order: extract → `rescue_list.py` → rescue → `validate.py --json combined.json`
→ `python3 -m mediatracker.pdf_ingest combined.json` → `report.py`.

Result of the 2026-08-23 run: 352/352 files, 7,067 comments, 100% with body text
(1,244 recovered by the rescue pass), 2,347 distinct pseudonyms, 2011–2026.
