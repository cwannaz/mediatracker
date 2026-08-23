# Data model

The schema captures the **evolution** of articles and comment threads across
polls, faithful reproduction of articles (text + images), and enough structure to
compare authors across journals.

The authoritative definition is `mediatracker/db.py::_SCHEMA` (self-migrating at
startup). This document explains the intent.

## Shape

```
journal 1──* article 1──* article_snapshot ─┐
                  │                          ├─* article_image *──1 image
                  └──* comment 1──* comment_snapshot
```

### journal
One row per tracked publication. `id = sha256("journal:" + slug)`. Holds the
comment platform (`native` / `coral` / `disqus` / …) and a free-form `config`
JSONB for per-journal knobs.

### article
Stable identity of an article. `id = sha256("article:" + slug + ":" +
canonical_url)`. `canonical_url` strips tracking params, fragments and trailing
slashes so the same story is one row regardless of how it was linked.
`first_seen` / `last_seen` bound its observed lifetime.

### article_snapshot
One row **per observed change** to an article (headline edits, body updates,
comment-count movement). Deduplicated by `content_hash` over
headline+subhead+body+author, so re-polling an unchanged article writes nothing.
`fetched_at` is poll time; `published_at` / `updated_at` are the source's own
times (point-in-time honesty). This is what lets you replay how a story evolved.

### comment
Stable identity of a comment. Keyed by the source's own comment id when exposed
(`comment_id(...)`), otherwise a synthetic id derived from
author+time+body (`synthetic_comment_id(...)`). `parent_id` preserves thread
structure. `author_nick` is the displayed pseudonym; `author_key` is the source's
stable user id if the platform exposes one.

### comment_snapshot
One row per observed change to a comment (edits, like-count changes), deduped by
`content_hash`. Same poll-time vs post-time split as articles.

### image + article_image
`image` is a **content-addressed** blob: `sha256` primary key, on-disk
`storage_path` under `blob_dir` (`ab/cd/<sha256>.<ext>`), sniffed
mime/width/height. `article_image` links images to the specific
`article_snapshot` they appeared in (with role hero/inline/thumb, position, orig
URL, alt, caption) — so historical reproduction stays accurate even as an
article swaps its images. The web app rewrites `<img src>` to `/blob/<sha256>`.

## Reproducing an article

Join the chosen `article_snapshot` (latest, or any historical row) with its
`article_image` → `image` rows and its `comment` / `comment_snapshot` rows,
rewrite image srcs to the local blob store, and render. No network needed.

## Planned (stylometry phase — not yet in schema)

Author-linkage will add tables such as `author_profile` (aggregated stylometric
features per nick per journal) and `nick_link` (candidate same-person links with
scores/evidence across journals). Kept additive so `ensure_schema` handles it
without a breaking migration.
