# 24 heures and the Tribune de Genève: the second TX Group platform

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

## The two titles are one commenting community

The Tribune de Genève runs the same Astro front-end — and the same backend.
Fetching one article's comments from both hosts returns identical UUIDs and
identical nicknames: `/apprentissage-…-633967257619` is one article with one
thread, served by both sites. On a given day 218 of the ~270 articles on their
fronts are shared; 51 are Geneva-only and 35 Vaud-only, and it is that local
desk (`/geneve` vs `/vaud-regions`) which draws the readerships apart.

So identity needs a unit above the title: the **community**, meaning the
comment backend a nickname is registered in. `TxAstroSource.community` is
`"tx-romandie"` for both titles; Le Matin, a different platform, is its own.
Subjects, profiles, personas and alias clusters are all keyed by it:

* the same nickname on Le Matin and on 24 heures is **two subjects**, never
  pooled — separate platforms, separate registrations, nothing linking the
  accounts. Seven nicknames currently appear in both and are counted twice on
  purpose;
* a commenter writing on both Astro titles is **one subject**, not two halves.
  72 of the 117 tx-romandie subjects do exactly that.

Comment ids follow: `ids.shared_comment_id(community, uuid)` where a backend's
ids are unique across the whole community, so the id does not depend on which
title's article row we reached the comment through. Without it every shared
thread would be stored once per title. Verified on the first TDG scan: 2,138
comments encountered, 799 new snapshots — the rest already stored via 24 heures
— and 2,443 comment rows for 2,443 distinct UUIDs, i.e. no duplication at all.
Le Matin keeps its per-article scheme untouched, and the two are proven
disjoint by test.

Both article rows are kept, because both titles really did publish the piece.
`comment.article_id` names whichever title we saw the thread through first, and
the upsert leaves it alone afterwards so it never flaps between scans.

## Known rough edges

* Discovery crawls the homepage plus eight desk fronts and yields ~253 URLs.
  A handful are service pages (`impressum-…`, `marketplace-…`, `cdl-…`) which
  the site models as articles, category `a-propos` / `marketplace` /
  `services-24heures`. They carry no comments. They are left in rather than
  denylisted: there is no structural signal distinguishing them, and a category
  denylist would silently drop a real desk if the site renamed one.
* Which of the two titles a shared comment is filed under is an artefact of
  scan order, not a fact about the commenter. What a subject's `journals` list
  does say honestly is which titles they write on at all, and for someone who
  only ever comments on Geneva-local or Vaud-local stories that is a real
  regional signal — a better one than the title split would have been.
* `browse_authors` and the commenter/persona views are still keyed on a bare
  nickname, so for the seven nicknames present in both communities they resolve
  to whichever profile has more comments (and log that they did). Those views
  need a community alongside the nickname before that matters much.
