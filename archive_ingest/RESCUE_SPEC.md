# Rescue pass — visual re-extraction

These captures were first processed from their PDF text layer, which LOST
content: either the comment bodies (rendered as images) or the whole page. The
bodies ARE visible on the rendered pages. You re-extract them by READING THE
PAGES AS IMAGES and OVERWRITE the existing record.

Follow `EXTRACTION_SPEC.md` for the JSON shape and all its rules. This file adds
what is specific to the rescue pass.

## Method

For each manifest entry:
1. `Read` the `pdf_path` with the `pages` parameter and view EVERY page
   (max 20 pages per request — split if needed).
2. Re-extract the article and the FULL comment thread from what you see.
3. `Write` the complete record to `records/<stem>.json`, OVERWRITING the old
   one. Do not merge by hand — a fresh, complete extraction is what is wanted.
   Keep `source_file` exactly as the manifest's `file` value.

If the manifest path fails to open, the on-disk filename may contain a
NON-BREAKING space (U+00A0) where the manifest shows a normal space. Use
`ls` / a glob to find the real name, or copy the file to your scratchpad and read
it there — but keep `source_file` as the manifest's `file` string.

## The old (2011–2015) Le Matin forum layout

Most rescue files use it. Reading it correctly:

- Each comment block is: **nickname** (bold), then the timestamp on the next
  line (`15.03.2012, 22:22 Heures`), then a small red `Signaler un abus` link,
  then the comment body in the column to the right.
- Under a comment there are usually **two small boxed numbers**, e.g. `2  0` —
  these are the agree / disagree counters. Record
  `like_count` = the FIRST number, and `reactions` = `{"up": <first>, "down": <second>}`.
  If only one counter is shown, use `like_count` and `reactions: {}`.
- **Indentation means a reply.** A block shifted to the right belongs to the
  nearest less-indented comment above it → set `parent_nick` to that nickname.
  Bodies often also start with `@nickname`.
- `Répondre`, `Signaler un abus`, `Retour au texte`, the right-hand column of
  teasers/ads ("Le monde en images", "SUPPL. PARTENAIRE", …), the rubric bar and
  the footer are page chrome — NOT comments.
- The running header holds the article title and a TRUNCATED url ending in `...`
  → `url` stays null.

## Reminders that matter most

- Transcribe bodies and nicknames VERBATIM — never fix spelling, grammar,
  accents or punctuation. The errors are the research signal.
- Include EVERY comment on every page, in order.
- Timestamps: `15.03.2012, 22:22 Heures` → `2012-03-15 22:22`. If a comment shows
  only a time and no date, use the date of the surrounding comments; if that is
  ambiguous, use null.
- If, after looking at all pages, the content genuinely is not there, keep the
  record with an explanatory `note` — do not invent anything.
