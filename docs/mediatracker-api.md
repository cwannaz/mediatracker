# Reading MediaTracker from another project

This describes everything an outside project needs to search MediaTracker's
corpus. It is self-contained: you do not need access to the source, and nothing
here assumes you have seen the application.

---

## 1. What you are talking to

MediaTracker is a study of three Swiss French-language newspapers — **Le Matin**,
**24 heures** and the **Tribune de Genève** — and of the people who comment on
them. It holds, as of this writing:

| | |
|---|---:|
| articles | ~320,000 |
| pictures | ~217,000 |
| reader comments | ~3,300,000 |
| span | 2007 to now |

Three things about the material, because they explain most of what you will see:

- **A comment is a first-class document.** Most of the corpus by count is
  readers arguing, not journalism. If you search for a person you will often
  find them discussed by readers rather than written about by a paper, and both
  come back in the same result list, distinguished by `kind`.
- **The archive is uneven, and it says so.** The deep past was recovered from
  web archives and PDFs, so pre-2017 articles frequently have a headline and no
  body text. A search over body text will under-report that period. This is a
  property of what survives, not of the index.
- **Entity extraction is still running.** People, organisations, places, events
  and topics are being read out of the articles by a language model over a
  period of days. `entity_coverage` on every relevant response tells you what
  fraction has been read. **Do not present an entity count as final** while that
  number is below 100.

---

## 2. Connecting

The base URL is the host running MediaTracker plus `/v1`:

```
http://127.0.0.1:55032/v1
```

**There is no authentication, because there is nothing to authenticate.** The
service is read-only — no endpoint creates, changes or deletes anything — and it
binds to a private interface. If you find yourself wanting a token, what you
actually want is a different service; this one cannot be made to do damage.

It answers `Access-Control-Allow-Origin: *`, so a browser front-end may call it
directly rather than proxying every request through its own server for no gain.

Everything is JSON. Send nothing; `GET` is the only method (plus `OPTIONS` for
the preflight).

### How it refuses you

Errors are JSON with `ok: false` and an `error` string, and they name the legal
values rather than making you guess:

```json
{"ok": false, "error": "unknown kind 'bogus'",
 "allowed": ["article", "image", "comment"]}
```

A path that does not exist returns `404` with the same shape. A database that is
down returns `503`. Nothing returns an HTML error page.

---

## 3. Conventions

- **`ok`** is on every response. Check it before anything else.
- **`total` is capped.** Counting every match of a common word means scanning the
  match, which is the one query that must not run here. Counts stop at 20,000
  and **`truncated: true`** says so. Narrow the search if you need an exact
  figure.
- **`note` says how the query was answered** — which literal a regex was
  prefiltered on, whether it fell back to a scan, whether it was cut short by
  the time limit. When a search is incomplete, this is where it says so. Show it
  to a human if you show them the count.
- **Dates** are `YYYY-MM-DD` strings, or `null`. Some archived material has no
  reliable date.
- **`limit`** defaults to 25 and caps at 200. Page with `offset`.
- Ids are opaque strings. Do not parse them.

---

## 4. Finding your way around

### `GET /v1/`

Describes the service, its current size, and every endpoint with its
parameters. If this document and that response ever disagree, the response is
right.

### `GET /v1/health`

Liveness, corpus counts, and extraction progress:

```json
{"ok": true, "service": "mediatracker", "version": "1.0",
 "corpus": {"documents": {"article": 320628, "comment": 3304349,
                          "image": 216983},
            "journals": ["24heures", "lematin", "tdg"],
            "earliest": "2007-04-11", "latest": "2026-09-06"},
 "entity_coverage": {"articles_read": 46443, "articles_total": 180593,
                     "pct": 25.72, "entities": 86029, "mentions": 358878}}
```

---

## 5. Searching

### `GET /v1/search`

The main endpoint. Everything else exists to make its results more useful.

| parameter | meaning |
|---|---|
| `q` | the search terms; empty means "browse the newest" |
| `mode` | `text` (default) or `regex` |
| `kind` | `article`, `image`, `comment` — comma-separated or repeated |
| `journal` | `lematin`, `24heures`, `tdg` — comma-separated |
| `year_from`, `year_to` | inclusive years |
| `entity_id` | only documents mentioning this entity (see §6) |
| `limit`, `offset` | paging; limit caps at 200 |

**Text mode** stems words and folds accents, so `geneve` finds *Genève* and
`remplacement` finds *remplacements*. Two pieces of syntax matter:

- `"grand remplacement"` — double quotes make one **block**. The words must be
  adjacent. Without the quotes they are merely both present, which is a
  different claim: 4,284 documents against 229.
- `~chien` — a tilde **excludes**. `~"petit chat"` excludes the phrase, because
  quoting binds tighter than negation.

**Regex mode** is POSIX, case-insensitive, matched against the raw text. It is
made fast by pulling literal runs out of your expression and letting the index
find those first, so `(?:grand )?remplacement` probes for `remplacement`. An
expression with no guaranteed literal — a bare alternation, or `^\d+$` — cannot
be prefiltered and is scanned under a time limit; `note` will say so.

A result:

```json
{"kind": "article",
 "id": "d220bff7f6f7…",
 "journal": "24heures",
 "date": "2025-11-18",
 "title": "«Chemtrails»: pourquoi cette théorie du complot revient en force",
 "snippet": "…théorie conspirationniste des chemtrails refait surface…",
 "highlighted": "…des <<chemtrails>> refait surface…",
 "score": 0.098,
 "source_url": "https://www.24heures.ch/…",
 "document_url": "/v1/document/article/d220bff7f6f7…",
 "article": {"section": "savoirs/sciences", "byline": "Adriana Stimoli",
             "comment_count": 10, "origin": "sitemap"}}
```

`snippet` is plain text. `highlighted` is the same string with matches wrapped
in `<<` and `>>` — render those as you like, or ignore the field.

The last key varies by `kind`: `article`, `comment` (`author`, `is_reply`,
`under_headline`) or `image` (see §7).

---

## 6. Entities

People, organisations, places, events and topics, read out of the articles.

### `GET /v1/entities?q=&kind=&limit=`

```json
{"ok": true,
 "entities": [{"id": 1147, "kind": "place", "name": "Genève", "mentions": 4626}],
 "entity_coverage": {"pct": 25.72, …}}
```

`kind` is one of `person`, `organization`, `place`, `event`, `topic`.

### `GET /v1/entities/{id}`

The entity plus the documents mentioning it, in the same result shape as a
search, and with `first_seen` / `last_seen` giving the span over which the
corpus talks about it.

**Two limits worth knowing before you build on this.** Identity is by surface
form, folded for case, accents and a leading article — so *Genève*, *geneve* and
*GENÈVE* are one place, but **"Donald Trump" and "Trump" are two entities**.
Resolving that is coreference, which this does not attempt, because guessing
would merge people who share a surname. And a topic is the model's reading of
what an article is about, so it need not appear in the text; the other four
kinds are read from the text and do appear.

To combine an entity with terms, pass both: `?entity_id=1147&q=logement`.

---

## 7. Documents and pictures

### `GET /v1/document/{kind}/{id}`

One document in full — including the complete body text, which search results
never carry — plus the entities found in it:

```json
{"ok": true,
 "document": {"kind": "article", "id": "…", "journal": "24heures",
              "language": "fr", "date": "2025-11-18",
              "title": "…", "text": "…3052 characters…",
              "meta": {"section": "…", "url": "…"},
              "entities": [{"id": 91, "kind": "person", "name": "Tucker Carlson"}]}}
```

### Pictures

An image result carries three URLs, already absolute:

```json
"image": {"blob_url": "http://127.0.0.1:55031/blob/48c9c9f4…",
          "thumbnail_url": "http://127.0.0.1:55031/thumb/t/48c9c9f4…",
          "preview_url": "http://127.0.0.1:55031/thumb/m/48c9c9f4…",
          "width": 2001, "height": 1334, "bytes": 239746,
          "credit": "AFP", "ran_under": "the headline it was published under"}
```

Use `thumbnail_url` (320px) for grids and `preview_url` (1200px) for viewing.
`blob_url` is the original the paper served, which averages 1.7k pixels wide —
fetching those for a grid is roughly thirteen times the bytes for no visible
gain. Thumbnails are generated on first request, so the first fetch of an
unseen picture is slower than the second.

The id of an image is the SHA-256 of its bytes, so identical files are one
document however many articles used them.

---

## 8. A worked example

```python
import urllib.parse, urllib.request, json

BASE = "http://127.0.0.1:55032/v1"

def mt(path, **params):
    url = f"{BASE}/{path}?" + urllib.parse.urlencode(
        {k: v for k, v in params.items() if v is not None})
    with urllib.request.urlopen(url, timeout=30) as r:
        out = json.load(r)
    if not out.get("ok"):
        raise RuntimeError(out.get("error", "request failed"))
    return out

# Is a name known, and as what?
who = mt("entities", q="Pécresse", limit=5)["entities"]

# Everything the corpus holds about the first match.
if who:
    hits = mt("search", entity_id=who[0]["id"], limit=20)
else:
    # Fall back to text: entity extraction may not have reached this
    # material yet, and coverage is on every entity response.
    hits = mt("search", q='"Valérie Pécresse"', limit=20)

for h in hits["results"]:
    print(h["kind"], h["date"], h["journal"], h["title"] or h["snippet"][:70])

if hits["truncated"]:
    print(f"showing {len(hits['results'])} of more than {hits['total']}")
if hits.get("note"):
    print("how this was answered:", hits["note"])
```

The fallback in that example is the pattern to copy. While extraction is in
progress, an entity lookup can legitimately find nothing for someone the corpus
discusses at length; text search reaches the whole corpus today.

---

## 9. What it will not do

- **It will not write.** There is no endpoint that changes anything.
- **It will not run your SQL.** Only the documented parameters are accepted.
- **It will not return an exact count for a broad query.** See `truncated`.
- **It will not resolve people for you.** See the note in §6.
- **It will not tell you it is complete when it is not.** If a query timed out,
  was prefiltered, or ran against a partly-extracted corpus, that is in `note`
  and `entity_coverage`. Pass it on rather than presenting a partial answer as a
  whole one.

---

## 10. Ports

| | |
|---|---|
| `55030` | daemon control (WebSocket; internal, not this API) |
| `55031` | image blobs and thumbnails |
| **`55032`** | **this API** |
| `55080` | the web app |
