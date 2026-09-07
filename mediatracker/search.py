"""One index over everything the tracker holds: articles, images, comments.

Until now every view in this project began from a person -- a nickname, a
persona, a stance -- because the question was who comments and how. That is
one branch of a larger thing. A record of 320,000 articles, 216,000 pictures
and 3.3 million comments is an account of what three newspapers published and
what their readers made of it, and most questions you can put to that account
start from a subject rather than a person: a place, an organisation, an event,
a word.

So this indexes the whole corpus into ONE table instead of bolting a search box
onto each existing view. One table means one ranking across kinds, one place to
add a language, and one place to add a publication -- which matters, because
English-language sources are coming and the index has to take them without a
rewrite.

Four things are deliberate:

* **The table duplicates the text**, about a gigabyte of it. Indexing the
  source tables in place would tie search to their shape and make every future
  source a special case. Duplication buys a rebuild that never touches ingest,
  and a clean delete if this design turns out wrong.
* **Language is a column, not a deployment.** The tsvector is generated with
  the configuration the row's own `lang` names, so a French article and an
  English one sit in the same index and each stems correctly.
* **Accents are folded** -- `unaccent` is mapped into both configurations, so
  "geneve" finds "Genève". On a French corpus typed at by a human that is not
  a nicety.
* **Regex is prefiltered by its own literals.** A bare regex over a gigabyte
  is a sequential scan, and the daemon runs queries on a single-threaded event
  loop where one slow query freezes the whole application. Most regexes contain
  a literal run -- `chemtrail`, `Genève` -- which the GIN index can find in
  milliseconds; the true expression is then applied to those rows only.
"""
from __future__ import annotations

import json
import logging
import re
import time

log = logging.getLogger(__name__)

KINDS = ("article", "image", "comment")

# Rows per refresh batch. Snapshot ids are serial, so a batch is an id window;
# the window is over SOURCE rows, not indexed docs, because a batch of article
# snapshots collapses to fewer articles when several describe the same one.
BATCH = 20_000

# A search must never outlive the patience of the loop it blocks. Anything
# slower than this is a query that should have been narrowed.
TIMEOUT_MS = 8_000

# Postgres text-search configurations, extended with unaccent. Built by
# ensure_schema; `lang` picks between them per row.
CONFIGS = {"en": "english_ua", "fr": "french_ua"}
DEFAULT_CONFIG = "french_ua"


def _config_case(col: str) -> str:
    """SQL choosing a text-search configuration from the row's own language.

    Written out rather than parameterised because a generated column must be
    immutable, and a regconfig literal is what makes to_tsvector so.
    """
    return (f"CASE WHEN {col} = 'en' THEN 'english_ua'::regconfig "
            f"ELSE 'french_ua'::regconfig END")


def ensure_schema(conn) -> None:
    """Create the index tables, configurations and indexes if absent."""
    cfg = _config_case("lang")
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        # Accent-folding copies of the stock configurations. CREATE ... COPY
        # has no IF NOT EXISTS, so the duplicate is caught rather than tested.
        for name, base in (("french_ua", "french"), ("english_ua", "english")):
            cur.execute("SELECT 1 FROM pg_ts_config WHERE cfgname = %s", (name,))
            if cur.fetchone():
                continue
            cur.execute(f"CREATE TEXT SEARCH CONFIGURATION {name} (COPY = {base})")
            cur.execute(
                f"ALTER TEXT SEARCH CONFIGURATION {name} "
                f"ALTER MAPPING FOR hword, hword_part, word "
                f"WITH unaccent, {base}_stem")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS search_doc (
                kind         text NOT NULL,
                ref          text NOT NULL,
                journal      text,
                lang         text,
                published_at timestamptz,
                title        text,
                body         text,
                extra        jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                indexed_at   timestamptz NOT NULL DEFAULT now(),
                tsv tsvector GENERATED ALWAYS AS (
                    setweight(to_tsvector({cfg}, coalesce(title, '')), 'A') ||
                    setweight(to_tsvector({cfg}, coalesce(body,  '')), 'B')
                ) STORED,
                PRIMARY KEY (kind, ref)
            )""")
        cur.execute("CREATE INDEX IF NOT EXISTS search_doc_tsv "
                    "ON search_doc USING gin (tsv)")
        cur.execute("CREATE INDEX IF NOT EXISTS search_doc_when "
                    "ON search_doc (kind, published_at DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS search_doc_journal "
                    "ON search_doc (journal, kind)")
        # Titles are short and get substring and regex traffic that the
        # tsvector cannot serve (partial words, punctuation).
        cur.execute("CREATE INDEX IF NOT EXISTS search_doc_title_trgm "
                    "ON search_doc USING gin (title gin_trgm_ops)")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS search_state (
                kind         text PRIMARY KEY,
                watermark    bigint NOT NULL DEFAULT 0,
                refreshed_at timestamptz
            )""")
    conn.commit()


# --------------------------------------------------------------------------- #
# building the index
# --------------------------------------------------------------------------- #

# Each statement folds one batch of source snapshots into documents. DISTINCT ON
# is not decoration: a batch routinely holds several snapshots of the same
# article, and an upsert that touches one row twice in a statement is an error.
_REFRESH = {
    "article": """
        INSERT INTO search_doc (kind, ref, journal, lang, published_at, title, body, extra)
        SELECT DISTINCT ON (s.article_id)
               'article', s.article_id, j.slug, coalesce(s.lang, 'fr'), s.published_at,
               nullif(concat_ws(' — ', nullif(s.headline, ''), nullif(s.subhead, '')), ''),
               s.body_text,
               jsonb_strip_nulls(jsonb_build_object(
                   'section', s.section, 'author', s.author,
                   'url', a.canonical_url, 'origin', a.origin,
                   'comments', s.comment_count))
        FROM article_snapshot s
        JOIN article a ON a.id = s.article_id
        JOIN journal j ON j.id = a.journal_id
        WHERE s.id > %(lo)s AND s.id <= %(hi)s
        ORDER BY s.article_id, s.id DESC
    """,
    # One document per picture. An image reused across articles keeps the
    # newest context rather than accumulating every caption it ever carried:
    # the caption is what a searcher reads, and a concatenation of six of them
    # reads like nothing at all.
    "image": """
        INSERT INTO search_doc (kind, ref, journal, lang, published_at, title, body, extra)
        SELECT DISTINCT ON (ai.sha256)
               'image', ai.sha256, j.slug, coalesce(s.lang, 'fr'), s.published_at,
               -- The LONGER of the two, not the caption. A caption is very
               -- often just the agency ("AFP" appears 2,230 times, "DR" 470),
               -- and where both are descriptive the caption is frequently a
               -- truncation of the alt text. Length is a blunt rule that gets
               -- both cases right.
               nullif(CASE WHEN length(coalesce(ai.alt_text, '')) >
                                length(coalesce(ai.caption, ''))
                           THEN ai.alt_text ELSE ai.caption END, ''),
               nullif(concat_ws(' ', nullif(ai.caption, ''), nullif(ai.alt_text, ''),
                                nullif(s.headline, '')), ''),
               jsonb_strip_nulls(jsonb_build_object(
                   'article', s.article_id, 'role', ai.role,
                   'mime', i.mime, 'width', i.width, 'height', i.height,
                   'bytes', i.byte_size, 'headline', s.headline,
                   'url', a.canonical_url,
                   -- A short caption is a credit line, and worth keeping as
                   -- one: it says who took the picture.
                   'credit', CASE WHEN length(coalesce(ai.caption, '')) BETWEEN 1 AND 29
                                  THEN ai.caption END))
        FROM article_image ai
        JOIN article_snapshot s ON s.id = ai.snapshot_id
        JOIN article a ON a.id = s.article_id
        JOIN journal j ON j.id = a.journal_id
        JOIN image i ON i.sha256 = ai.sha256
        WHERE s.id > %(lo)s AND s.id <= %(hi)s
        ORDER BY ai.sha256, s.id DESC
    """,
    "comment": """
        INSERT INTO search_doc (kind, ref, journal, lang, published_at, title, body, extra)
        SELECT DISTINCT ON (cs.comment_id)
               'comment', cs.comment_id, j.slug, 'fr', cs.posted_at,
               NULL, cs.body_text,
               jsonb_strip_nulls(jsonb_build_object(
                   'nick', c.author_nick, 'article', c.article_id,
                   'headline', a2.headline, 'is_reply', c.parent_id IS NOT NULL))
        FROM comment_snapshot cs
        JOIN comment c ON c.id = cs.comment_id
        JOIN article a ON a.id = c.article_id
        JOIN journal j ON j.id = a.journal_id
        LEFT JOIN LATERAL (
            SELECT headline FROM article_snapshot
            WHERE article_id = a.id ORDER BY id DESC LIMIT 1) a2 ON true
        WHERE cs.id > %(lo)s AND cs.id <= %(hi)s
        ORDER BY cs.comment_id, cs.id DESC
    """,
}

_CONFLICT = """
    ON CONFLICT (kind, ref) DO UPDATE SET
        journal = EXCLUDED.journal, lang = EXCLUDED.lang,
        published_at = EXCLUDED.published_at, title = EXCLUDED.title,
        body = EXCLUDED.body, extra = EXCLUDED.extra, indexed_at = now()
"""

_SOURCE_MAX = {
    "article": "SELECT coalesce(max(id), 0) FROM article_snapshot",
    "image":   "SELECT coalesce(max(id), 0) FROM article_snapshot",
    "comment": "SELECT coalesce(max(id), 0) FROM comment_snapshot",
}


def watermarks(conn) -> dict:
    """Where each kind's indexing has reached, against where it could reach."""
    out = {}
    with conn.cursor() as cur:
        for kind in KINDS:
            cur.execute("SELECT watermark, refreshed_at FROM search_state WHERE kind = %s",
                        (kind,))
            row = cur.fetchone()
            cur.execute(_SOURCE_MAX[kind])
            top = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM search_doc WHERE kind = %s", (kind,))
            out[kind] = {"watermark": row[0] if row else 0,
                         "source_max": top,
                         "docs": cur.fetchone()[0],
                         "refreshed_at": row[1].isoformat() if row and row[1] else None}
    return out


def refresh(conn, *, kinds=KINDS, batch: int = BATCH,
            max_batches: int | None = None, progress=None) -> dict:
    """Bring the index up to date, in id-ordered batches, committing each.

    Resumable by construction: the watermark only advances after the batch it
    describes is committed, so an interrupted run repeats at most one batch.
    """
    ensure_schema(conn)
    done = {}
    for kind in kinds:
        sql = _REFRESH[kind] + _CONFLICT
        with conn.cursor() as cur:
            cur.execute("SELECT watermark FROM search_state WHERE kind = %s", (kind,))
            row = cur.fetchone()
            lo = row[0] if row else 0
            cur.execute(_SOURCE_MAX[kind])
            top = cur.fetchone()[0]
        n = batches = 0
        started = time.time()
        while lo < top:
            if max_batches is not None and batches >= max_batches:
                break
            hi = min(lo + batch, top)
            with conn.cursor() as cur:
                cur.execute(sql, {"lo": lo, "hi": hi})
                n += cur.rowcount
                cur.execute("""
                    INSERT INTO search_state (kind, watermark, refreshed_at)
                    VALUES (%s, %s, now())
                    ON CONFLICT (kind) DO UPDATE
                      SET watermark = EXCLUDED.watermark, refreshed_at = now()
                """, (kind, hi))
            conn.commit()
            lo = hi
            batches += 1
            if progress:
                progress(kind, lo, top, n)
        done[kind] = {"docs": n, "batches": batches, "watermark": lo,
                      "seconds": round(time.time() - started, 1)}
        log.info("indexed %s: %d docs in %d batches (%.1fs)",
                 kind, n, batches, time.time() - started)
    return done


# --------------------------------------------------------------------------- #
# regex literals
# --------------------------------------------------------------------------- #

_RUN = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]{3,}")
_OPTIONAL = "?*"


def literals_from_regex(pattern: str) -> list[str]:
    """Literal runs a matching string MUST contain, for use as a prefilter.

    Conservative on purpose. A prefilter that drops a row the regex would have
    matched turns a search into a lie, and the failure is silent -- the reader
    sees a short result list, not a warning. So every construct that makes a
    run merely possible rather than certain erases it: optional quantifiers,
    optional groups, and alternation, which guarantees neither branch.
    """
    s = _blank_optional_groups(_strip_classes(pattern))
    if "|" in s:                      # a top-level alternation survived
        return []
    out = []
    for m in _RUN.finditer(s):
        run, end = m.group(0), m.end()
        # A quantifier binds to the single character before it, so that
        # character is not guaranteed: `abc?` guarantees only "ab".
        if end < len(s):
            nxt = s[end]
            if nxt in _OPTIONAL:
                run = run[:-1]
            elif nxt == "{" and re.match(r"\{0[,}]", s[end:]):
                run = run[:-1]
        if len(run) >= 3:
            out.append(run)
    return out


def _blank_optional_groups(s: str) -> str:
    """Erase groups that need not appear, innermost first, keeping offsets.

    `(?:grand )?remplacement` guarantees "remplacement" and NOT "grand"; an
    index probe for "grand" would miss every plain "remplacement" in the
    corpus. A group holding an alternation guarantees nothing either, so its
    contents go the same way while whatever follows it survives.
    """
    while True:
        m = re.search(r"\(([^()]*)\)", s)
        if not m:
            return s
        inner, after = m.group(1), s[m.end():]
        optional = (after[:1] in _OPTIONAL) or bool(re.match(r"\{0[,}]", after))
        if optional or "|" in inner:
            repl = " " * (m.end() - m.start())
        else:
            repl = " " + inner + " "          # keep contents, drop the parens
        s = s[:m.start()] + repl + s[m.end():]


def _strip_classes(pattern: str) -> str:
    """Blank escapes, character classes and group flags, keeping offsets.

    Offsets are kept so the quantifier following a run is still the next
    character, and so two runs separated by a blanked class cannot fuse into
    a literal that appears nowhere in the corpus.
    """
    out, i, n = [], 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "\\" and i + 1 < n:
            out.append("  ")
            i += 2
            continue
        if c == "[":
            j = i + 1
            if j < n and pattern[j] == "^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 2 if pattern[j] == "\\" else 1
            j = min(j, n - 1)
            out.append(" " * (j - i + 1))
            i = j + 1
            continue
        if pattern.startswith("(?", i):
            # Inline flags and the non-capturing marker: blank them so "?:"
            # is not read as a quantifier on the run before the paren.
            k = i + 2
            while k < n and pattern[k] not in ":)":
                k += 1
            out.append("(" + " " * (k - i))
            i = k + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


# --------------------------------------------------------------------------- #
# querying
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# what the reader typed
# --------------------------------------------------------------------------- #

_TOKEN = re.compile(r'(~?)"([^"]*)"|(~?)(\S+)')


def parse_terms(q: str) -> dict:
    """Split a text query into phrases, words, and the negations of each.

    Two pieces of syntax, both asked for and both meaning something the bare
    word list cannot say:

    * `"grand remplacement"` is ONE block. Without it the two words are ANDed
      and an article using them a paragraph apart matches, which is a different
      claim about the corpus than the phrase occurring.
    * `~chien` excludes. Postgres spells negation `-`, but a leading minus is
      also how people write a hyphenated word, so `~` is the safer marker and
      is what was asked for.

    Quoting binds tighter than negation, so `~"grand remplacement"` excludes the
    phrase rather than excluding "grand" and searching for "remplacement".
    """
    phrases, terms, not_phrases, not_terms = [], [], [], []
    for neg_q, quoted, neg_w, word in _TOKEN.findall(q or ""):
        if quoted or neg_q:
            text = quoted.strip()
            if not text:
                continue
            (not_phrases if neg_q == "~" else phrases).append(text)
        else:
            text = word.strip()
            if not text or text == "~":
                continue
            (not_terms if neg_w == "~" else terms).append(text)
    return {"phrases": phrases, "terms": terms,
            "not_phrases": not_phrases, "not_terms": not_terms}


def _query_sql(parsed: dict, cfg: str, params: dict, *, positive_only: bool = False):
    """Build one configuration's tsquery from parsed input, or None if empty.

    `&&` against an empty tsquery yields the other side unchanged, so a term
    that stems to nothing -- a stopword -- drops out without special-casing and
    without turning the whole query into a match-nothing.
    """
    parts = []
    for i, text in enumerate(parsed["phrases"]):
        params[f"p{i}"] = text
        parts.append(f"phraseto_tsquery({cfg}, %(p{i})s)")
    for i, text in enumerate(parsed["terms"]):
        params[f"t{i}"] = text
        parts.append(f"plainto_tsquery({cfg}, %(t{i})s)")
    if not positive_only:
        for i, text in enumerate(parsed["not_phrases"]):
            params[f"np{i}"] = text
            parts.append(f"!!phraseto_tsquery({cfg}, %(np{i})s)")
        for i, text in enumerate(parsed["not_terms"]):
            params[f"nt{i}"] = text
            parts.append(f"!!plainto_tsquery({cfg}, %(nt{i})s)")
    if not parts:
        return None
    return "(" + " && ".join(parts) + ")"


# Every text query is built twice, once per configuration, and the two are
# ORed. The index holds French and English documents side by side and each
# stems in its own configuration, so a single configuration would silently
# under-match the other language. GIN serves both probes from the same index,
# and the second costs little next to being wrong about half a corpus once the
# English sources arrive.

_HEADLINE_OPTS = "StartSel=<<,StopSel=>>,MaxWords=38,MinWords=12,MaxFragments=2,FragmentDelimiter= … "

# How many matching rows the facet counts are allowed to look at. A true count
# over a large match is a full scan of the match, which is exactly the query
# that must not run on the event loop; a capped count is reported as capped.
FACET_CAP = 20_000


def _filters(kinds, journals, year_from, year_to, params, entity_id=None) -> str:
    where = []
    if entity_id:
        # A semi-join rather than a JOIN: a document mentioning the entity
        # twice must not come back twice, and the planner handles IN against
        # the mention primary key well.
        where.append("(kind, ref) IN (SELECT doc_kind, doc_ref FROM entity_mention "
                     "WHERE entity_id = %(eid)s)")
        params["eid"] = int(entity_id)
    if kinds:
        where.append("kind = ANY(%(kinds)s)")
        params["kinds"] = list(kinds)
    if journals:
        where.append("journal = ANY(%(journals)s)")
        params["journals"] = list(journals)
    if year_from:
        where.append("published_at >= make_timestamptz(%(yf)s, 1, 1, 0, 0, 0)")
        params["yf"] = int(year_from)
    if year_to:
        where.append("published_at < make_timestamptz(%(yt)s, 1, 1, 0, 0, 0)")
        params["yt"] = int(year_to) + 1
    return (" AND " + " AND ".join(where)) if where else ""


def query(conn, *, q: str, mode: str = "text", kinds=(), journals=(),
          year_from=None, year_to=None, entity_id=None, limit: int = 50,
          offset: int = 0, timeout_ms: int = TIMEOUT_MS) -> dict:
    """Search the index. Returns rows, facet counts, and how it was answered.

    `mode` is "text" (stemmed, accent-folded, ranked) or "regex" (POSIX, case
    insensitive). The report of HOW a regex was answered is part of the result
    rather than a log line, because a prefiltered regex and a timed-out scan
    are different claims about completeness and the reader has to see which
    one they got.
    """
    q = (q or "").strip()
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    started = time.time()
    params: dict = {"q": q, "limit": limit, "offset": offset}
    note = None

    if not q:
        # No terms is not an empty result, it is a browse: the newest of
        # whatever the filters describe. This is what makes the picture
        # browser the same code path as the search box rather than a second
        # one that drifts away from it.
        match = "true"
        rank = "0::float4"
        snippet = "left(coalesce(d.body, d.title, ''), 240)"
        lateral = ""
        note = "browsing; no search terms"
    elif mode == "regex":
        params["rx"] = q
        lits = literals_from_regex(q)
        if lits:
            # Prefix lexemes, not stems: a regex literal is a substring of the
            # word it appears in, and the stemmer would rewrite it out of the
            # index it is meant to probe.
            params["pre"] = " & ".join(f"{re.sub(chr(39), '', l)}:*" for l in lits)
            match = ("tsv @@ to_tsquery('simple', %(pre)s) "
                     "AND coalesce(body, title, '') ~* %(rx)s")
            note = f"prefiltered on {', '.join(lits)}"
        else:
            match = "coalesce(body, title, '') ~* %(rx)s"
            note = ("no literal to prefilter on; scanned until the time limit"
                    if "|" not in q else
                    "alternation guarantees no literal; scanned until the time limit")
        rank = "0::float4"
        snippet = ("substring(coalesce(d.body, d.title, '') "
                   "FROM greatest(1, m.pos - 120) FOR 320)")
        lateral = ("LEFT JOIN LATERAL (SELECT regexp_instr("
                   "coalesce(d.body, d.title, ''), %(rx)s, 1, 1, 0, 'i') AS pos) m ON true")
    else:
        parsed = parse_terms(q)
        qfr = _query_sql(parsed, "'french_ua'::regconfig", params)
        qen = _query_sql(parsed, "'english_ua'::regconfig", params)
        if qfr is None:
            # Everything the reader typed stemmed away -- stopwords only.
            match, rank, snippet, lateral = "true", "0::float4", \
                "left(coalesce(d.body, d.title, ''), 240)", ""
            note = "nothing searchable in those words"
        else:
            match = f"(tsv @@ {qfr} OR tsv @@ {qen})"
            rank = f"greatest(ts_rank_cd(tsv, {qfr}), ts_rank_cd(tsv, {qen}))"
            # Highlight on the POSITIVE part only: a negated term is by
            # definition absent, and asking ts_headline to mark what is not
            # there produces a snippet chosen for the wrong reason.
            hfr = _query_sql(parsed, "'french_ua'::regconfig", params, positive_only=True)
            hen = _query_sql(parsed, "'english_ua'::regconfig", params, positive_only=True)
            cfg = _config_case("d.lang")
            if hfr is None:
                snippet = "left(coalesce(d.body, d.title, ''), 240)"
            else:
                snippet = (f"ts_headline({cfg}, coalesce(d.body, d.title, ''), "
                           f"CASE WHEN d.lang = 'en' THEN {hen} ELSE {hfr} END, "
                           f"'{_HEADLINE_OPTS}')")
            lateral = ""
            if not (parsed["phrases"] or parsed["terms"]):
                # Pure exclusion: no positive lexeme for GIN to start from, so
                # this is a scan. Say so rather than look mysteriously slow.
                note = "only exclusions given; scanned rather than indexed"

    where = match + _filters(kinds, journals, year_from, year_to, params, entity_id)
    truncated = False
    rows: list[dict] = []
    facets: dict = {}
    total = 0

    with conn.cursor() as cur:
        cur.execute("SELECT set_config('statement_timeout', %s, true)",
                    (str(int(timeout_ms)),))
        try:
            cur.execute(f"""
                SELECT d.kind, d.ref, d.journal, d.published_at, d.title,
                       {snippet} AS snippet, d.extra, d.rank
                FROM (
                    SELECT *, {rank} AS rank FROM search_doc
                    WHERE {where}
                    ORDER BY {'published_at DESC NULLS LAST' if not q else rank + ' DESC, published_at DESC NULLS LAST'}
                    LIMIT %(limit)s OFFSET %(offset)s
                ) d
                {lateral}
            """, params)
            rows = [{"kind": k, "ref": r, "journal": j,
                     "when": (t.date().isoformat() if t else None),
                     "title": ti, "snippet": sn, "extra": ex or {},
                     "rank": float(rk or 0)}
                    for k, r, j, t, ti, sn, ex, rk in cur.fetchall()]
        except Exception as exc:
            if "statement timeout" not in str(exc).lower():
                raise
            conn.rollback()
            return {"rows": [], "facets": {}, "total": 0, "mode": mode,
                    "truncated": True, "took_ms": int(1000 * (time.time() - started)),
                    "note": (note + "; " if note else "") +
                            f"timed out after {timeout_ms} ms — narrow it with a "
                            f"kind, a journal or a year"}

        # Facets, capped. Counted over the same predicate so the numbers
        # describe this search and not the corpus.
        try:
            cur.execute(f"""
                SELECT kind, count(*) FROM (
                    SELECT kind FROM search_doc WHERE {where} LIMIT {FACET_CAP}
                ) s GROUP BY kind
            """, params)
            facets = {k: n for k, n in cur.fetchall()}
            total = sum(facets.values())
            truncated = total >= FACET_CAP
        except Exception as exc:
            if "statement timeout" not in str(exc).lower():
                raise
            conn.rollback()
            facets, total, truncated = {}, len(rows), True
            note = (note + "; " if note else "") + "counts unavailable within the time limit"

    return {"rows": rows, "facets": facets, "total": total, "mode": mode,
            "truncated": truncated, "note": note,
            "took_ms": int(1000 * (time.time() - started))}


def facets_for(conn, *, q: str = "", mode: str = "text", kinds=(), journals=(),
               year_from=None, year_to=None, entity_id=None,
               timeout_ms: int = TIMEOUT_MS) -> dict:
    """Journal and year breakdown for a search, for the filter rail."""
    params: dict = {"q": q}
    if mode == "regex":
        params["rx"] = q
        match = "coalesce(body, title, '') ~* %(rx)s"
    elif q.strip():
        parsed = parse_terms(q)
        qfr = _query_sql(parsed, "'french_ua'::regconfig", params)
        qen = _query_sql(parsed, "'english_ua'::regconfig", params)
        match = "true" if qfr is None else f"(tsv @@ {qfr} OR tsv @@ {qen})"
    else:
        match = "true"
    where = match + _filters(kinds, journals, year_from, year_to, params, entity_id)
    out = {"journals": {}, "years": {}}
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('statement_timeout', %s, true)",
                    (str(int(timeout_ms)),))
        try:
            cur.execute(f"""
                SELECT journal, extract(year FROM published_at)::int AS y, count(*)
                FROM (SELECT journal, published_at FROM search_doc
                      WHERE {where} LIMIT {FACET_CAP}) s
                GROUP BY 1, 2
            """, params)
            for j, y, n in cur.fetchall():
                if j:
                    out["journals"][j] = out["journals"].get(j, 0) + n
                if y:
                    out["years"][str(y)] = out["years"].get(str(y), 0) + n
        except Exception as exc:
            if "statement timeout" not in str(exc).lower():
                raise
            conn.rollback()
    return out


def main(argv=None) -> int:
    import argparse
    from . import db
    from .config import load_config

    p = argparse.ArgumentParser(prog="mediatracker.search")
    sub = p.add_subparsers(dest="action", required=True)
    r = sub.add_parser("refresh", help="bring the index up to date")
    r.add_argument("--kind", action="append", choices=list(KINDS), default=None)
    r.add_argument("--batch", type=int, default=BATCH)
    r.add_argument("--max-batches", type=int, default=None)
    s = sub.add_parser("query", help="search from the shell")
    s.add_argument("q")
    s.add_argument("--mode", default="text", choices=("text", "regex"))
    s.add_argument("--kind", action="append", default=None)
    s.add_argument("--limit", type=int, default=10)
    sub.add_parser("status", help="how far the index has got")
    a = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    conn = db.connect(load_config())
    if conn is None:
        print("no database", flush=True)
        return 1

    if a.action == "refresh":
        def show(kind, lo, top, n):
            print(f"  {kind:8s} {lo:>10,} / {top:<10,} {n:>9,} docs", flush=True)
        out = refresh(conn, kinds=tuple(a.kind) if a.kind else KINDS,
                      batch=a.batch, max_batches=a.max_batches, progress=show)
        print(json.dumps(out, indent=1))
    elif a.action == "status":
        print(json.dumps(watermarks(conn), indent=1))
    else:
        out = query(conn, q=a.q, mode=a.mode,
                    kinds=tuple(a.kind) if a.kind else (), limit=a.limit)
        print(f"{out['total']}{'+' if out['truncated'] else ''} matches "
              f"in {out['took_ms']} ms  {out.get('note') or ''}")
        for r in out["rows"]:
            print(f"\n[{r['kind']}] {r['journal']} {r['when'] or ''}  {r['title'] or ''}")
            print(f"   {(r['snippet'] or '')[:300]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
