# MediaTracker doctrine

Conventions this project follows. They mirror the algotrade component set so the
two feel like siblings, adapted for a crawler/archiver.

## Principles

1. **Stdlib first.** Fetching and parsing use `urllib`, `xml.etree`, `re`,
   `json`. A third-party dependency must earn its place. Current runtime deps:
   `psycopg` (Postgres), `websockets` (control surface), `playwright`
   (last-resort JS rendering only).
2. **Headless browsing is a last resort.** Anything obtainable with a plain GET
   goes through `fetch.py`. `render.py` (Playwright) is used only by adapters for
   journals whose article body or comments exist only after client-side JS.
3. **Point-in-time honesty.** Store the source's own timestamps (publication /
   post time) separately from `fetched_at` (poll time). Ids never depend on
   mutable data — only on identity.
4. **Idempotent by construction.** Journals/articles/comments have stable
   hash-based ids; a new snapshot row is written only when `content_hash`
   changes. Re-polling is safe and cheap.
5. **Graceful degradation.** If Postgres is down the daemon keeps running and
   mirrors every record to the JSONL store for later replay.
6. **Secrets out of the repo.** Only non-secret values live in `config.toml`.
   Postgres credentials come from `secret_postgre.env` kept outside the tree
   (gitignored), read by `db.py`.
7. **Be a polite crawler.** Per-host minimum delay, descriptive User-Agent,
   robots.txt respected by default. This is private research, not a scraper farm.

## Comment collection & robots.txt

Articles (`/story/…`) are robots-allowed and fetched normally. The comment API
(`api.<domain>/comment/v1/comments`) is on a host whose robots.txt is
`Disallow: /`. The user explicitly opted to collect comments (2026-08-23), so
comment fetches — and only those — pass `force_allow=True` to bypass the robots
check; the per-host politeness delay still applies. Everything else stays fully
robots-compliant. If this posture changes, unset it in `tamedia.fetch_comments`.

### Two readings of a writer, never blended (measured 2026-08-27)

`proximity` scores a writer as thirteen aggregate rates. `lexicon` scores the
character 4-grams they repeat. Held out by time over 744 Le Matin profiles, a
probe of 1300 characters finds its own author at the top of the list:

| reading | top-1 | top-5 | median rank |
|---|---|---|---|
| aggregate rates | 8% | 46% | 8 |
| rare words, idf-weighted | 40% | 60% | 4 |
| **character 4-grams** | **49%** | **71%** | **2** |
| turns of phrase (2-3 words) | 17% | 37% | 12 |

Both are reported, neither is blended into the other: no weighting this corpus
can justify exists, and an unjustified one would read as precision. Two
artefacts are removed inside `lexicon` rather than left to callers — @mentions
and URLs (the strongest match ever produced rested entirely on fragments of a
third party's handle both had replied to) and document size (uncapped, the
longest profile headed the ranking for five unrelated probes out of five).

Neither reading can say the true author is present at all: best score 0.175
with them in the population, 0.154 with them removed. Every ranking therefore
reports `standout` — how far its top sits above what the best of a field that
size is worth by chance, which is sqrt(2 ln n), near 3.6 for seven hundred.
Quote the excess, never the raw score.

### No headless browser (measured 2026-08-27)

Le Matin renders its comment list client-side, so `www.lematin.ch/comment/<id>`
returns a page with a comment count and no comments — not one nickname from a
150-comment thread appears in the 126 KB the server sends. Driving a real
browser was benchmarked against the plain-HTTP route on the same articles:

| | requests | transferred | wall | comments |
|---|---|---|---|---|
| HTTP (what we do) | 3 | 242 KiB | 3.2 s | 149 of 150 |
| Headless Chrome | 96 | 7,073 KiB | 7–10 s | 10 of 150 |

The browser makes the *same* API call we do — captured verbatim as
`api.lematin.ch/comment/v1/comments?tenantId=4&contentId=…&limit=10` — with a
page size of 10 against our 100, then needs a click per further ten behind a
consent overlay. It is thirty times the traffic to obtain a fifteenth of the
data per request, and offers no field the API lacks, because the DOM it builds
is built from that JSON. The dependency is available on this machine and was
tested; it is not used.

## Layout

- One lowercase package (`mediatracker/`) inside the repo root, with
  `server.py` / `__main__.py` / `config.py` / `protocol.py` / `db.py` /
  `store.py`, plus one module per external concern.
- `sources/` holds one adapter per journal; `sources/__init__.py` is the
  registry. Adapters only parse into the `Parsed*` shapes — the pipeline does all
  persistence, so every journal is handled uniformly.

## Config layering

defaults (`config.py`) → `config.toml` → `MEDIATRACKER_*` env → CLI flags.

## Schema evolution

Self-migrating at startup (`db.ensure_schema`, `CREATE TABLE IF NOT EXISTS` +
`ADD COLUMN IF NOT EXISTS`). Additive changes need no migration. Reserve numbered
migration scripts under a future `migrations/` dir for breaking changes only.

## Logging

Stdlib `logging`; `log = logging.getLogger(__name__)` per module;
`basicConfig` once in `main()`. WARNING/ERROR are the alerting channel and are
counted by `health.py`.

## Privacy posture

Scope is **private, local-only research** (confirmed with Cedric, 2026-08-23).
Comment data — including pseudonyms and stylometric author-linkage — is retained
locally for analysis and is never republished. Keep it that way: any feature that
would export or expose individuals needs an explicit decision first.

**Real names are uninteresting, not forbidden** (Cedric, 2026-08-27). The
database already holds journalists' names, and a commenter's name that turns up
in a note or a handle is not a problem. The line is one of purpose: *this is not
a system for tracking real people down.* So nothing guards the free-text fields,
and nothing should — but no feature should set out to resolve a pseudonym to a
person either, and the profiling pass still records what places a writer rather
than who they are, because that is what the sociology needs.

### What a hand writes lives outside what a pass rewrites (2026-08-27)

Notes and off-platform accounts are their own tables (`subject_note`,
`subject_account`), not columns on `author_profile`. Two reasons, and both
matter:

* A profiling run rewrites every column of an `author_profile` row. A remark
  typed by a reader would be erased by the next run of the machine.
* Everything in `author_profile` is derived from the stored comments and
  reproducible from them. A note is not, and mixing the two would put an
  observation and a measurement in the same shape — the same mistake the
  `metrics` / inferred split exists to prevent.

Both are keyed `(community, subject_kind, subject_key)` like a profile. A
persona reads its own records **and** those written against each of its
handles, never the reverse: linking two nicknames must not bury what was
already recorded about either, while a note about the person says more than any
one handle can carry.

Off-platform accounts are structured rather than prose because the questions
worth asking about them are counting ones — how many of a public carry an
identity elsewhere, on which network, and whether a rename there lines up with
a rename here. The platform is read off the pasted link rather than asked for,
so the two cannot disagree.

## Testing

`pytest`, tests in `tests/` as `test_*.py`. Run with `python3 -m pytest -q` from
the repo root. Unit tests must not require Postgres or network.
