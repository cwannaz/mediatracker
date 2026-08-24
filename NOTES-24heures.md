# 24 heures: the second TX Group platform

Diagnosed 2026-08-24, adapter built the same day. Superseded the earlier note
that recorded only "comment `tenantId` still unknown" — the real situation was
that the source produced nothing at all.

## What had happened

`24heures.ch` left the Next.js platform that `mediatracker/sources/tamedia.py`
targets and is now an **Astro** site. Both halves of the old adapter missed:
`parse_article()` found no `__NEXT_DATA__`, and the discovery regex looked for
`/story/` hrefs that no longer exist. The journal scanned on schedule and
returned zero articles every time, so nothing in the logs said "broken".

| | lematin.ch | 24heures.ch |
|---|---|---|
| platform | Next.js | Astro |
| article payload | `__NEXT_DATA__` | `<script id="page-data">` + `NewsArticle` ld+json |
| article URLs | `/story/<slug>-<id>` | `/<slug>-<id>` |
| body | JSON element tree | `article-element` nodes in `<article data-article-body>` |
| comments | JSON, `api.lematin.ch`, `tenantId=4` | HTML fragment, `POST /api/content/…` |
| comment ids | numeric | UUID |
| reply linkage | `parentCommentId` | `@nickname` marker only |
| paging | `offset` + `totalCount` | timestamp cursor |

`mediatracker/sources/tx_astro.py` is the new adapter; `vingtquatre_heures.py`
subclasses it instead of `TamediaSource`. By the site's own JS
(`e === '24heures' || e === 'tdg'`) the Tribune de Genève runs the same
front-end, so that title should need only a subclass.

## The comment endpoints

No browser was needed in the end — the flyout's behaviour is readable straight
out of the Astro bundle (`/_astro/CommentsFlyout.*.js` →
`fetchHelpers.*.js`). Two POST endpoints, both returning **HTML**, not JSON:

    POST /api/content/load-comment-container
      {articleId, articleType, commentCount, currentUrl, sortOrder}

    POST /api/content/comment-list          # subsequent pages
      {articleId, sortOrder, userInfo, paginationCursor, lastCommentIndex}

`comment-list` rejects a request with 400 "Missing required properties" unless
`articleId`, `sortOrder`, `userInfo` and `paginationCursor` are all present.
`userInfo` describes the *reader*, and must be an object — `null` is refused,
`{}` is accepted and is the anonymous case. `articleType`, `commentCount`,
`currentUrl` and `lastCommentIndex` are optional there. The cursor is the
timestamp of the last comment on the page, read off the load-more button's
`data-pagination-cursor`; a page without one is the last page.

Page size is ~27. Verified against a 95-comment thread: four pages, 95 distinct
ids, matching the count the page itself declares.

`articleType` is not always `"article"` — a picture gallery is `"slideshow"`
and takes comments like anything else, so the value is read from the flyout
markup rather than assumed.

## Two things the platform loses, which are recorded rather than papered over

**Replies carry no parent id.** They are flattened into the one list with a
`<span class="parent-nickname">@Nick</span>` marker. `link_replies()` resolves
that to a comment id when exactly one earlier comment in the thread has that
nickname, and otherwise leaves the link unset and records
`parent_ambiguous` — a thread where one person posts repeatedly cannot be
disentangled from the nickname alone, and guessing "the nearest one" would
invent a conversation structure. On the test thread: 7 replies, 3 linked,
4 honestly ambiguous. The raw `reply_to_nickname` is kept either way.

**Premium articles serve no body.** A paywalled page renders its figures and
captions but not a single paragraph, so a body scraped from markup comes back
as ~150 characters of photo credit and looks like a very short article rather
than an absent one. `parse_body()` therefore returns how many text blocks were
actually served, and `body_text` is set to None when that is zero, with
`paywalled` / `bodyWithheld` in `raw_meta`. The comments on those articles are
public and complete — which is what this study is about — so the articles are
still tracked; they just cannot contribute their text.

## Also fixed on the way

**The fetcher had no cookie jar.** `https://www.24heures.ch/` answered 302 to
itself until a cookie was accepted, so every request looped and returned a
46-byte body: the site was unreachable regardless of parsing. Done in `09cdf7e`.

**`Fetcher` had no POST.** Added `post_json()`, which goes through the same
per-host politeness delay and robots gate as `get()`. It only ever requests a
document; nothing here writes to the site. The comment endpoints pass
`force_allow=True` for the same reason Le Matin's do — robots.txt disallows
`/api/` for generic agents and the user has explicitly opted to collect
comments (see DOCTRINE.md).

## Known rough edges

* Discovery crawls the homepage plus eight desk fronts and yields ~253 URLs.
  A handful are service pages (`impressum-…`, `marketplace-…`, `cdl-…`) which
  the site models as articles, category `a-propos` / `marketplace` /
  `services-24heures`. They carry no comments. They are left in rather than
  denylisted: there is no structural signal distinguishing them, and a category
  denylist would silently drop a real desk if the site renamed one.
* Comment volume is unknown territory — 24 heures may have a different
  commenting culture from Le Matin, and the two corpora should not be pooled
  without checking that first.
