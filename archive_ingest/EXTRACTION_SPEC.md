# PDF/DOCX extraction contract (MediaTracker archive ingest)

You convert one archived Le Matin capture (article page and/or its comment
thread, printed years ago) into ONE JSON record file.

## Output

For each source file in your manifest, write exactly one file:

    /mnt/storage/Projects/MediaTracker/pdf-ingest/records/<stem>.json

`<stem>` is given in the manifest. The file contains a single JSON object:

```json
{
  "source_file": "<the manifest's `file` value, verbatim>",
  "slug": "lematin",
  "parsed": {
    "url": null,
    "headline": "…",
    "subhead": null,
    "author": null,
    "source": null,
    "section": null,
    "lang": "fr",
    "published_at": "2021-11-08",
    "body_text": null,
    "comments": [
      {"author_nick": "…", "posted_at": "2021-11-08 23:26", "body_text": "…",
       "like_count": 55, "reactions": {"DÉTESTABLE": 55}, "parent_nick": null}
    ]
  }
}
```

## Rules — read carefully

1. **Transcribe comment text VERBATIM.** Do NOT fix spelling, grammar, accents,
   spacing or punctuation. Typos and grammatical errors are the research signal
   (they measure language mastery) — "correcting" them destroys the data.
   Same for nicknames: copy them exactly (case, digits, hyphens, spaces).
2. **`published_at`** — if the filename starts with `YYYYMMDD`, use that date
   (format `YYYY-MM-DD`). Otherwise take the date printed on the page. If truly
   unknown, use `null`.
3. **`posted_at`** — the timestamp shown next to each comment, as
   `YYYY-MM-DD HH:MM` (Swiss local time; do not convert). Old layouts print
   `07.04.2012, 23:44 Heures` → `2012-04-07 23:44`. If only a date is shown,
   use `YYYY-MM-DD`. If none, `null`.
4. **`url`** — include ONLY if a COMPLETE url is printed. Print headers often
   truncate with `…`/`...` (e.g. `http://www.lematin.ch/faits-divers/conductrice-ivre-percute-procession-...`)
   — a truncated url is useless, so set `null` in that case. Never invent one.
5. **`like_count` / `reactions`** — modern captures show a reaction bar with a
   number and a dominant label (`DÉTESTABLE`, `LOVE IT`, `C'EST JUSTE!`,
   `SYMPA`, …). Put the number in `like_count` and `{"<LABEL>": <number>}` in
   `reactions`. Older captures may show a simple counter → `like_count` only,
   `reactions: {}`. If nothing is shown, `like_count: null`, `reactions: {}`.
6. **`parent_nick`** — if a comment is visibly a reply (indented, or starts with
   `@nickname`), set the parent's nickname; else `null`.
7. **Article vs comments.** Many files are comments-only ("Commentaires à …"):
   set `body_text: null` and still fill `headline` from the title/filename.
   Article-only captures: fill `body_text` with the article text and
   `comments: []`.
8. **`author` / `source`** — `author` is the journalist byline if shown.
   `source` is the news agency if shown (AFP, ATS, Keystone-ATS, Reuters, AP,
   Bloomberg…). Both `null` if absent.
9. **`section`** — the rubric if shown (Suisse, Monde, Sports, Faits divers,
   People, Économie, …), else `null`.
10. **Skip nothing.** Include every comment on the page, in the order shown.
    Navigation chrome, ads, "PUBLIER UN NOUVEAU COMMENTAIRE", "Signaler un
    abus", "Dénoncer ce commentaire", cookie banners and menus are NOT comments.
11. If a file is unreadable or contains no article and no comments, still write
    the record with `"parsed": {"headline": …, "comments": []}` and add
    `"note": "<why>"` inside `parsed`.

## Validity

The JSON must parse (`json.load`). Use double quotes, escape inner quotes,
no trailing commas, no comments, no markdown fences around it.
