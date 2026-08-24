# 24 heures is not being collected at all

Checked 2026-08-24.

## What is wrong

`24heures.ch` has **zero** articles and zero comments in the database. The open
item was recorded as "comment `tenantId` still unknown, articles work", but that
is not the situation: the source produces nothing.

The site has moved off the TX Group Next.js platform that
`mediatracker/sources/tamedia.py` targets:

| | lematin.ch | 24heures.ch |
|---|---|---|
| platform | Next.js | **Astro** (`/_astro/…` assets) |
| article payload | `__NEXT_DATA__` JSON | not present |
| article URLs | `/story/<slug>-<id>` | `/<slug>-<id>` (no `/story/` prefix) |
| comments | `api.lematin.ch/comment/v1/comments`, `tenantId=4` | `CommentsFlyout` Astro component, endpoint not yet identified |

So both halves of the adapter miss: `parse_article()` finds no `__NEXT_DATA__`,
and the story-link regex finds no `/story/` hrefs to crawl in the first place.
Le Matin is unaffected and still parses normally — it is still Next.js.

## Also found: the fetcher needs a cookie jar

`https://www.24heures.ch/` answers 302 to itself until a cookie is accepted, so
every request loops and returns a 46-byte body. With a `http.cookiejar.CookieJar`
on the opener the same request returns 200 and ~930 KB. `mediatracker/fetch.py`
builds no opener and keeps no cookies, so 24 heures is unreachable regardless of
parsing.

This is worth fixing on its own: it is one cookie jar, it is what a browser does,
and it affects any site that gates on a consent redirect.

## What a fix needs

1. Cookie jar in `Fetcher` (small, benefits every source).
2. A separate `astro.py` adapter: article discovery by the `-<9+ digits>$` URL
   suffix, and article fields parsed from the server-rendered markup or from the
   island props Astro serialises, rather than from `__NEXT_DATA__`.
3. The comment endpoint behind `CommentsFlyout`, which needs the network panel of
   a real browser to observe — the same way the Le Matin comment API was found.

Left for a decision rather than done overnight: this is a second platform
adapter, not a config value, and it is worth agreeing on before building.
