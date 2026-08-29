"""PostgreSQL access: secret loading, connection, self-migrating schema, and the
core write helpers.

Conventions borrowed from algotrade:
  * raw SQL via psycopg 3, no ORM;
  * credentials from a secret_postgre.env kept OUTSIDE the repo;
  * schema is self-migrating at startup (CREATE TABLE IF NOT EXISTS +
    ADD COLUMN IF NOT EXISTS); numbered migration scripts are reserved for
    breaking changes only;
  * graceful degradation — connect() returns None instead of raising so the
    daemon can keep running off the JSONL store when Postgres is down.

Data model (see SCHEMA_SPEC.md for the full rationale):
    journal 1--* article 1--* article_snapshot
                     article 1--* comment 1--* comment_snapshot
    image (content-addressed) *--* article_snapshot via article_image
Snapshots capture the *evolution* of an article/comment across polls; a new
snapshot row is written only when content_hash changes.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:  # graceful import: the daemon still boots (JSONL-only) without psycopg
    import psycopg
    from psycopg.types.json import Jsonb
except Exception:  # pragma: no cover - exercised only where psycopg is absent
    psycopg = None  # type: ignore
    Jsonb = None  # type: ignore

log = logging.getLogger(__name__)

# The papers' own day. A Swiss title's "today" is a calendar day in Zurich, not
# in UTC and not on whatever clock the daemon happens to run on, so every
# day-boundary comparison converts to this zone before truncating to a date.
PAPER_TZ = "Europe/Zurich"

# How many calendar days the Today view spans — yesterday and today.
RECENT_DAYS = 2


def paper_today(tz: str = PAPER_TZ) -> date:
    return datetime.now(ZoneInfo(tz)).date()


# One row per article, carrying the publication date of the snapshot that is
# actually displayed. A re-scan that corrects a timestamp therefore moves an
# article between days instead of putting it on both.
_LATEST_SNAPSHOT = """
    SELECT DISTINCT ON (a.id) s.published_at
    FROM article a
    LEFT JOIN article_snapshot s ON s.article_id = a.id
    ORDER BY a.id, s.fetched_at DESC
"""


def paper_window(days: int = 2, tz: str = PAPER_TZ) -> date:
    """The first day of a window of `days` ending on the papers' current day.

    The Today view spans two of them. One is not enough: past midnight in
    Zurich the calendar day turns over while the Swiss titles publish almost
    nothing until morning, so a single day is a blank page for several hours
    every night — and the machine running this daemon need not even be in the
    same day as Zurich.
    """
    return paper_today(tz) - timedelta(days=max(1, days) - 1)


# Search path for the secret file, first match wins. Mirrors algotrade's habit of
# keeping secrets under the user's private dirs, never in the repo.
_SECRET_NAME = "secret_postgre.env"
_SECRET_SEARCH = (
    Path(__file__).resolve().parent.parent,           # repo root (gitignored)
    Path.home() / ".config" / "mediatracker",
    Path.home() / "Documents" / "MATLAB",
)


def load_pg_secret() -> tuple[str | None, str | None]:
    """Return (user, password), honoring env vars first, then secret_postgre.env.

    File format: simple KEY=VALUE lines, POSTGRE_USER / POSTGRE_PASSWORD.
    """
    user = os.environ.get("POSTGRE_USER")
    password = os.environ.get("POSTGRE_PASSWORD")
    if user and password:
        return user, password

    for base in _SECRET_SEARCH:
        path = base / _SECRET_NAME
        if not path.is_file():
            continue
        vals: dict[str, str] = {}
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip().strip('"').strip("'")
        user = user or vals.get("POSTGRE_USER")
        password = password or vals.get("POSTGRE_PASSWORD")
        if user and password:
            log.info("loaded Postgres credentials from %s", path)
            return user, password

    return user, password


def connect(cfg) -> "psycopg.Connection | None":
    """Open a connection, or return None if unavailable (graceful degradation)."""
    if psycopg is None:
        log.warning("psycopg not installed; running without Postgres")
        return None
    user, password = load_pg_secret()
    if not user or not password:
        log.warning("no Postgres credentials found; running without Postgres")
        return None
    try:
        conn = psycopg.connect(
            host=cfg.pg_host,
            port=cfg.pg_port,
            dbname=cfg.pg_dbname,
            user=user,
            password=password,
            autocommit=True,
            connect_timeout=10,
        )
        log.info("connected to Postgres %s@%s:%s/%s", user, cfg.pg_host, cfg.pg_port, cfg.pg_dbname)
        return conn
    except Exception as exc:  # broad: any failure means "run degraded"
        log.warning("could not connect to Postgres (%s); running degraded", exc)
        return None


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal (
    id           TEXT PRIMARY KEY,          -- ids.journal_id(slug)
    slug         TEXT UNIQUE NOT NULL,
    name         TEXT NOT NULL,
    base_url     TEXT NOT NULL,
    comment_system TEXT,                     -- 'native' | 'coral' | 'disqus' | ...
    config       JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS article (
    id            TEXT PRIMARY KEY,          -- ids.article_id(journal_slug, url)
    journal_id    TEXT NOT NULL REFERENCES journal(id),
    canonical_url TEXT NOT NULL,
    source_key    TEXT,                      -- journal-native article id if any
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (journal_id, canonical_url)
);
CREATE INDEX IF NOT EXISTS article_journal_idx ON article (journal_id);

CREATE TABLE IF NOT EXISTS article_snapshot (
    id            BIGSERIAL PRIMARY KEY,
    article_id    TEXT NOT NULL REFERENCES article(id),
    content_hash  TEXT NOT NULL,             -- dedup key vs previous snapshot
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),  -- poll time
    published_at  TIMESTAMPTZ,               -- source's own publication time
    updated_at    TIMESTAMPTZ,              -- source's own "last edited" time
    headline      TEXT,
    subhead       TEXT,
    author        TEXT,
    section       TEXT,
    lang          TEXT,
    body_text     TEXT,
    body_html     TEXT,
    comment_count INTEGER,
    raw_meta      JSONB NOT NULL DEFAULT '{}',
    UNIQUE (article_id, content_hash)
);
CREATE INDEX IF NOT EXISTS article_snapshot_article_idx
    ON article_snapshot (article_id, fetched_at DESC);

CREATE TABLE IF NOT EXISTS comment (
    id           TEXT PRIMARY KEY,          -- ids.comment_id / synthetic_comment_id
    article_id   TEXT NOT NULL REFERENCES article(id),
    source_key   TEXT,                       -- comment id from the source system
    parent_id    TEXT REFERENCES comment(id),
    author_nick  TEXT,                       -- displayed pseudonym
    author_key   TEXT,                       -- stable source user id if exposed
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS comment_article_idx ON comment (article_id);
CREATE INDEX IF NOT EXISTS comment_nick_idx    ON comment (author_nick);

CREATE TABLE IF NOT EXISTS comment_snapshot (
    id           BIGSERIAL PRIMARY KEY,
    comment_id   TEXT NOT NULL REFERENCES comment(id),
    content_hash TEXT NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    posted_at    TIMESTAMPTZ,               -- source's own post time
    body_text    TEXT,
    body_html    TEXT,
    like_count   INTEGER,
    reply_count  INTEGER,
    raw_meta     JSONB NOT NULL DEFAULT '{}',
    UNIQUE (comment_id, content_hash)
);
CREATE INDEX IF NOT EXISTS comment_snapshot_comment_idx
    ON comment_snapshot (comment_id, fetched_at DESC);

-- Content-addressed image blobs; bytes live on disk under blob_dir, this row is
-- the metadata + on-disk location.
CREATE TABLE IF NOT EXISTS image (
    sha256      TEXT PRIMARY KEY,
    byte_size   BIGINT,
    mime        TEXT,
    width       INTEGER,
    height      INTEGER,
    storage_path TEXT NOT NULL,             -- relative to blob_dir
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Which images appeared in which article snapshot (so historical reproduction
-- is faithful even as an article's images change).
CREATE TABLE IF NOT EXISTS article_image (
    snapshot_id BIGINT NOT NULL REFERENCES article_snapshot(id),
    sha256      TEXT NOT NULL REFERENCES image(sha256),
    role        TEXT,                        -- 'hero' | 'inline' | 'thumb'
    position    INTEGER,
    orig_url    TEXT,
    alt_text    TEXT,
    caption     TEXT,
    PRIMARY KEY (snapshot_id, sha256, position)
);

-- One row per scan attempt (scheduled or manual), for the GUI history table and
-- progress reporting.
CREATE TABLE IF NOT EXISTS scan_run (
    id                BIGSERIAL PRIMARY KEY,
    journal_id        TEXT NOT NULL REFERENCES journal(id),
    slug              TEXT NOT NULL,
    trigger           TEXT NOT NULL,          -- 'manual' | 'scheduled'
    status            TEXT NOT NULL,          -- 'queued'|'running'|'done'|'error'
    requested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    articles_seen     INTEGER NOT NULL DEFAULT 0,
    article_snapshots INTEGER NOT NULL DEFAULT 0,
    comments_seen     INTEGER NOT NULL DEFAULT 0,
    comment_snapshots INTEGER NOT NULL DEFAULT 0,
    images_new        INTEGER NOT NULL DEFAULT 0,
    errors            INTEGER NOT NULL DEFAULT 0,
    note              TEXT
);
CREATE INDEX IF NOT EXISTS scan_run_slug_idx ON scan_run (slug, requested_at DESC);

-- A persona is one real person behind several pseudonyms (renames over the
-- years, platform migrations, or different handles per journal). Analysis is
-- meant to run on the persona, not on each nickname in isolation.
CREATE TABLE IF NOT EXISTS persona (
    id         BIGSERIAL PRIMARY KEY,
    label      TEXT NOT NULL UNIQUE,      -- how we refer to this person
    note       TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One nickname maps to at most one persona. journal_slug '*' means the mapping
-- holds on every journal; a real slug scopes it (the same nickname on two
-- journals is not necessarily the same human).
CREATE TABLE IF NOT EXISTS persona_alias (
    journal_slug TEXT NOT NULL DEFAULT '*',
    nick         TEXT NOT NULL,
    persona_id   BIGINT NOT NULL REFERENCES persona(id) ON DELETE CASCADE,
    confidence   TEXT NOT NULL DEFAULT 'confirmed',  -- confirmed | probable | candidate
    evidence     TEXT,                                -- why we believe it
    added_by     TEXT NOT NULL DEFAULT 'manual',      -- manual | stylometry
    added_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (journal_slug, nick)
);
CREATE INDEX IF NOT EXISTS persona_alias_persona_idx ON persona_alias (persona_id);

-- One profile per analysis SUBJECT: a persona when the nicknames have been
-- linked, otherwise a bare nickname. `metrics` is computed deterministically
-- from the stored text; the other JSONB columns hold LLM-inferred attributes,
-- each carrying its own probabilities/confidence and supporting quotes, so an
-- inference is never mistaken for a measurement.
CREATE TABLE IF NOT EXISTS author_profile (
    subject_kind TEXT NOT NULL,          -- 'persona' | 'nick'
    subject_key  TEXT NOT NULL,          -- persona id (text) | nickname
    label        TEXT NOT NULL,
    n_comments   INTEGER,
    n_chars      INTEGER,
    first_seen   TIMESTAMPTZ,
    last_seen    TIMESTAMPTZ,
    metrics      JSONB NOT NULL DEFAULT '{}',   -- deterministic style measures
    language     JSONB NOT NULL DEFAULT '{}',   -- grammar/conjugation/mastery
    gender       JSONB NOT NULL DEFAULT '{}',   -- probabilities + basis
    politics     JSONB NOT NULL DEFAULT '{}',   -- overall + per-period drift
    philosophy   JSONB NOT NULL DEFAULT '{}',
    region       JSONB NOT NULL DEFAULT '{}',
    topics       JSONB NOT NULL DEFAULT '{}',
    notes        TEXT,
    model        TEXT,
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (subject_kind, subject_key)
);

-- Additive column upgrades (safe to run every boot).

-- The comment namespace a title's readers write into. Nicknames are only
-- comparable inside one community: two titles sharing a comment backend share
-- their commenters, while the same nickname on two platforms is two people
-- until something proves otherwise. Defaults to the slug — a title has its own
-- community unless its adapter says otherwise.
ALTER TABLE journal ADD COLUMN IF NOT EXISTS community TEXT;
UPDATE journal SET community = slug WHERE community IS NULL;

-- Profiles and persona clusters are scoped the same way. Everything profiled
-- before this column existed was Le Matin: it was the only journal with data.
ALTER TABLE author_profile ADD COLUMN IF NOT EXISTS community TEXT NOT NULL DEFAULT 'lematin';

-- What a commenter volunteers about the environment they formed in: social
-- origin, schooling, trade, household, generation. Kept because it is usually
-- what explains how someone came to think as they do, and kept categorically:
-- a name would answer a question this study is not asking, so the block
-- describes a person without setting out to locate one.
ALTER TABLE author_profile ADD COLUMN IF NOT EXISTS milieu JSONB NOT NULL DEFAULT '{}';
ALTER TABLE persona ADD COLUMN IF NOT EXISTS community TEXT NOT NULL DEFAULT 'lematin';

-- persona_alias already scoped by journal, but '*' (every journal) is the wrong
-- unit: a nickname is not portable across platforms. Re-key it on community.
ALTER TABLE persona_alias ADD COLUMN IF NOT EXISTS community TEXT NOT NULL DEFAULT 'lematin';

-- ...which means the keys have to widen too, or one nickname could only ever
-- exist in one community. Re-keying is a one-shot structural change, so each
-- step checks the current key before touching it and is a no-op afterwards.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint
               WHERE conname = 'author_profile_pkey'
                 AND array_length(conkey, 1) = 2) THEN
        ALTER TABLE author_profile DROP CONSTRAINT author_profile_pkey;
        ALTER TABLE author_profile
            ADD CONSTRAINT author_profile_pkey
            PRIMARY KEY (community, subject_kind, subject_key);
    END IF;

    IF EXISTS (SELECT 1 FROM pg_constraint
               WHERE conname = 'persona_alias_pkey'
                 AND array_length(conkey, 1) = 2) THEN
        ALTER TABLE persona_alias DROP CONSTRAINT persona_alias_pkey;
        ALTER TABLE persona_alias
            ADD CONSTRAINT persona_alias_pkey PRIMARY KEY (community, nick);
    END IF;

    -- A persona label is only unique within its own community.
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'persona_label_key') THEN
        ALTER TABLE persona DROP CONSTRAINT persona_label_key;
        ALTER TABLE persona ADD CONSTRAINT persona_community_label_key
            UNIQUE (community, label);
    END IF;
END $$;

ALTER TABLE article_snapshot ADD COLUMN IF NOT EXISTS source TEXT;   -- news agency (Reuters/AFP/ATS…)
ALTER TABLE article ADD COLUMN IF NOT EXISTS gone_at TIMESTAMPTZ;    -- when the article stopped being reachable
ALTER TABLE article ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'live';  -- 'live' | 'pdf'
ALTER TABLE article ADD COLUMN IF NOT EXISTS source_file TEXT;       -- archival source (e.g. a printed PDF)

-- The comment count the article page showed the last time we actually read
-- the thread. A rescan whose page reports the same number can skip the comment
-- API entirely, which is the expensive half of the scan. Compared only against
-- the site's own earlier number: our stored row count is not the same quantity.
ALTER TABLE article ADD COLUMN IF NOT EXISTS thread_count INTEGER;
ALTER TABLE article ADD COLUMN IF NOT EXISTS thread_read_at TIMESTAMPTZ;

-- What a reader knows about a subject that the corpus cannot hold: a handle
-- recognised somewhere else, an off-platform rename, a correction to a reading,
-- anything observed rather than computed.
--
-- These are NOT in author_profile even though they describe the same subject.
-- A profiling pass rewrites every column of that row wholesale, so a remark
-- typed by hand would be erased by the next run of the machine. It is also the
-- honest arrangement: everything in author_profile is derived from the stored
-- text and reproducible from it, and a note is neither.
CREATE TABLE IF NOT EXISTS subject_note (
    id           BIGSERIAL PRIMARY KEY,
    community    TEXT NOT NULL,
    subject_kind TEXT NOT NULL,   -- 'persona' | 'nick'
    subject_key  TEXT NOT NULL,   -- persona id (text) | nickname
    body         TEXT NOT NULL,
    source       TEXT,            -- where it was observed: a URL, a thread, a paper
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS subject_note_subject_idx
    ON subject_note (community, subject_kind, subject_key, created_at);

-- The same writer somewhere we do not collect: a Facebook account under a
-- paper's page, a blog, a handle on another network. Structured rather than
-- left inside a note, because the interesting questions are counting ones —
-- which commenters carry an off-platform identity, on which network, and how
-- often a rename there lines up with a rename here.
--
-- `handle` is what the account calls itself and may be anything the person
-- chose; it is recorded as the account's name, not as a claim about who they
-- are, and the profiling contract still applies to everything else.
CREATE TABLE IF NOT EXISTS subject_account (
    id           BIGSERIAL PRIMARY KEY,
    community    TEXT NOT NULL,
    subject_kind TEXT NOT NULL,   -- 'persona' | 'nick'
    subject_key  TEXT NOT NULL,   -- persona id (text) | nickname
    platform     TEXT NOT NULL,   -- facebook | x | instagram | youtube | blog | ...
    handle       TEXT,            -- the name the account goes by there
    url          TEXT,
    confidence   TEXT NOT NULL DEFAULT 'confirmed',  -- confirmed | probable | possible
    evidence     TEXT,            -- why we believe it is the same writer
    added_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS subject_account_subject_idx
    ON subject_account (community, subject_kind, subject_key);
CREATE INDEX IF NOT EXISTS subject_account_platform_idx
    ON subject_account (platform);
"""


def ensure_schema(conn) -> None:
    """Create/upgrade the schema. Idempotent; safe to run at every startup."""
    if conn is None:
        return
    with conn.cursor() as cur:
        cur.execute(_SCHEMA)
    log.info("schema ensured")


# --------------------------------------------------------------------------- #
# Core writers (idempotent; each returns whether it wrote a new snapshot)
# --------------------------------------------------------------------------- #

def _jsonb(value: dict[str, Any] | None):
    return Jsonb(value or {}) if Jsonb is not None else (value or {})


def upsert_journal(conn, *, jid: str, slug: str, name: str, base_url: str,
                   comment_system: str | None = None, config: dict | None = None,
                   community: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO journal (id, slug, name, base_url, comment_system, config, community)
            VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, %s))
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                base_url = EXCLUDED.base_url,
                comment_system = COALESCE(EXCLUDED.comment_system, journal.comment_system),
                community = EXCLUDED.community
            -- NOTE: config is intentionally NOT overwritten here; the GUI owns it
            -- (see update_journal_config). Only the initial INSERT sets it.
            """,
            (jid, slug, name, base_url, comment_system, _jsonb(config),
             community, slug),
        )


def upsert_article(conn, *, aid: str, journal_id: str, canonical_url: str,
                   source_key: str | None = None, origin: str = "live",
                   source_file: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO article (id, journal_id, canonical_url, source_key, origin, source_file)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET last_seen = now(),
                gone_at = NULL,  -- successfully re-seen, so no longer gone
                source_key = COALESCE(EXCLUDED.source_key, article.source_key),
                origin = EXCLUDED.origin,
                source_file = COALESCE(EXCLUDED.source_file, article.source_file)
            """,
            (aid, journal_id, canonical_url, source_key, origin, source_file),
        )


def insert_article_snapshot(conn, *, article_id: str, content_hash: str,
                            fields: dict[str, Any]) -> int | None:
    """Insert a snapshot unless one with this content_hash already exists.
    Returns the new snapshot id, or None if it was a duplicate."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO article_snapshot
                (article_id, content_hash, published_at, updated_at, headline,
                 subhead, author, source, section, lang, body_text, body_html,
                 comment_count, raw_meta)
            VALUES (%(article_id)s, %(content_hash)s, %(published_at)s, %(updated_at)s,
                    %(headline)s, %(subhead)s, %(author)s, %(source)s, %(section)s, %(lang)s,
                    %(body_text)s, %(body_html)s, %(comment_count)s, %(raw_meta)s)
            ON CONFLICT (article_id, content_hash) DO NOTHING
            RETURNING id
            """,
            {
                "article_id": article_id,
                "content_hash": content_hash,
                "published_at": fields.get("published_at"),
                "updated_at": fields.get("updated_at"),
                "headline": fields.get("headline"),
                "subhead": fields.get("subhead"),
                "author": fields.get("author"),
                "source": fields.get("source"),
                "section": fields.get("section"),
                "lang": fields.get("lang"),
                "body_text": fields.get("body_text"),
                "body_html": fields.get("body_html"),
                "comment_count": fields.get("comment_count"),
                "raw_meta": _jsonb(fields.get("raw_meta")),
            },
        )
        row = cur.fetchone()
    return row[0] if row else None


def upsert_comment(conn, *, cid: str, article_id: str, source_key: str | None,
                   parent_id: str | None, author_nick: str | None,
                   author_key: str | None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO comment (id, article_id, source_key, parent_id, author_nick, author_key)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET last_seen = now(),
                author_nick = COALESCE(EXCLUDED.author_nick, comment.author_nick),
                author_key  = COALESCE(EXCLUDED.author_key, comment.author_key)
            """,
            (cid, article_id, source_key, parent_id, author_nick, author_key),
        )


def insert_comment_snapshot(conn, *, comment_id: str, content_hash: str,
                            fields: dict[str, Any]) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO comment_snapshot
                (comment_id, content_hash, posted_at, body_text, body_html,
                 like_count, reply_count, raw_meta)
            VALUES (%(comment_id)s, %(content_hash)s, %(posted_at)s, %(body_text)s,
                    %(body_html)s, %(like_count)s, %(reply_count)s, %(raw_meta)s)
            ON CONFLICT (comment_id, content_hash) DO NOTHING
            RETURNING id
            """,
            {
                "comment_id": comment_id,
                "content_hash": content_hash,
                "posted_at": fields.get("posted_at"),
                "body_text": fields.get("body_text"),
                "body_html": fields.get("body_html"),
                "like_count": fields.get("like_count"),
                "reply_count": fields.get("reply_count"),
                "raw_meta": _jsonb(fields.get("raw_meta")),
            },
        )
        row = cur.fetchone()
    return row[0] if row else None


def upsert_image(conn, *, sha256: str, byte_size: int, mime: str | None,
                 width: int | None, height: int | None, storage_path: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO image (sha256, byte_size, mime, width, height, storage_path)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (sha256) DO NOTHING
            """,
            (sha256, byte_size, mime, width, height, storage_path),
        )


def link_article_image(conn, *, snapshot_id: int, sha256: str, role: str | None,
                       position: int, orig_url: str | None, alt_text: str | None,
                       caption: str | None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO article_image (snapshot_id, sha256, role, position, orig_url, alt_text, caption)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (snapshot_id, sha256, position) DO NOTHING
            """,
            (snapshot_id, sha256, role, position, orig_url, alt_text, caption),
        )


# --------------------------------------------------------------------------- #
# Journals (config / schedule) and scan bookkeeping
# --------------------------------------------------------------------------- #

def list_journals(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, slug, name, base_url, comment_system, config FROM journal ORDER BY slug")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_journal_config(conn, jid: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT config FROM journal WHERE id = %s", (jid,))
        row = cur.fetchone()
    return row[0] if row else None


def update_journal_config(conn, jid: str, config: dict) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE journal SET config = %s WHERE id = %s", (_jsonb(config), jid))


# Origins that are a record of the past rather than a live page. Nothing with
# one of these is ever re-fetched: the URL is either unretrievable or no longer
# served, and there is no later state to catch. Named once so that adding a
# third kind of archive cannot silently put it back in the live work-list.
ARCHIVE_ORIGINS = ("pdf", "wayback")


def active_article_urls(conn, journal_id: str, *, since_days: int) -> list[str]:
    """Canonical URLs of articles still young enough to gain comments — these
    are re-scanned each cycle so comment/vote evolution keeps being captured
    until the thread goes cold.

    The window is on the article's own publication date, never on `last_seen`.
    `last_seen` records when WE last fetched it, and a rescan refreshes it, so
    a window on it renews its own membership: once an article entered the
    work-list it could never leave, and the list only ever grew. It was found
    holding an article published in 2020, refetched on every scan for years.
    Where a title publishes no date, `first_seen` stands in — the day the
    article appeared to us is at least an event outside our control.

    Archived captures are excluded, both kinds. A pdf:// pseudo-URL is not
    retrievable and a printed page is finished. A wayback capture is worse than
    useless here: its URL is a real one the live site no longer serves, and it
    carries no published_at on purpose — dating a 2012 article by the 2016 crawl
    that caught it would be a lie — so `first_seen` stands in and every archived
    article looks like it was published the moment the backfill wrote it. Left
    in, 4,595 recovered articles joined the live work-list overnight and pushed
    one Tribune scan from 27 minutes to six and a half hours.

    An archive that merged into a crawled article keeps origin='live' and so
    keeps being re-scanned, which is the intent."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.canonical_url,
                   COALESCE(p.published_at, a.first_seen) AS dated
            FROM article a
            LEFT JOIN LATERAL (
                SELECT published_at FROM article_snapshot
                WHERE article_id = a.id ORDER BY fetched_at DESC LIMIT 1
            ) p ON true
            WHERE a.journal_id = %s AND a.gone_at IS NULL
              AND a.origin <> ALL(%s)
              AND a.canonical_url NOT LIKE 'pdf://%%'
              AND COALESCE(p.published_at, a.first_seen)
                  > now() - make_interval(days => %s)
            ORDER BY dated DESC
            """,
            (journal_id, list(ARCHIVE_ORIGINS), since_days),
        )
        return [r[0] for r in cur.fetchall()]


def last_thread_count(conn, aid: str) -> int | None:
    """The comment count the source showed when we last read this thread."""
    with conn.cursor() as cur:
        cur.execute("SELECT thread_count FROM article WHERE id = %s", (aid,))
        row = cur.fetchone()
    return row[0] if row else None


def record_thread_count(conn, aid: str, count: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE article SET thread_count = %s, thread_read_at = now() WHERE id = %s",
            (count, aid))


def find_article_by_source_key(conn, journal_id: str, source_key: str) -> tuple[str, str] | None:
    """Return (article_id, canonical_url) for a journal's native article id.
    Used to merge an archived capture with the already-crawled live article."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, canonical_url FROM article WHERE journal_id=%s AND source_key=%s LIMIT 1",
            (journal_id, source_key),
        )
        return cur.fetchone()


def mark_article_gone(conn, aid: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE article SET gone_at = now() WHERE id = %s AND gone_at IS NULL", (aid,)
        )


def create_scan_run(conn, *, journal_id: str, slug: str, trigger: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO scan_run (journal_id, slug, trigger, status)
            VALUES (%s, %s, %s, 'queued') RETURNING id
            """,
            (journal_id, slug, trigger),
        )
        return cur.fetchone()[0]


def start_scan_run(conn, run_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE scan_run SET status='running', started_at=now() WHERE id=%s", (run_id,)
        )


def finish_scan_run(conn, run_id: int, *, status: str, stats: dict, note: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE scan_run SET status=%s, finished_at=now(),
                articles_seen=%s, article_snapshots=%s, comments_seen=%s,
                comment_snapshots=%s, images_new=%s, errors=%s, note=%s
            WHERE id=%s
            """,
            (status, stats.get("articles_seen", 0), stats.get("article_snapshots", 0),
             stats.get("comments_seen", 0), stats.get("comment_snapshots", 0),
             stats.get("images_new", 0), stats.get("errors", 0), note, run_id),
        )


# --------------------------------------------------------------------------- #
# Browsing / read queries (Article Browser: articles, commenters, authors, sources)
# --------------------------------------------------------------------------- #

class BadPattern(ValueError):
    """The user's search regex is not valid POSIX — surfaced to the GUI as such."""


def _rows(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _guard_regex(exc: Exception) -> None:
    """Re-raise a Postgres regex failure as BadPattern; otherwise pass it on."""
    if "invalid regular expression" in str(exc).lower():
        raise BadPattern(str(exc).splitlines()[0]) from exc
    raise exc


def browse_articles(conn, *, q: str | None = None, journals: list[str] | None = None,
                    since: date | None = None, tz: str = PAPER_TZ,
                    limit: int = 100, offset: int = 0) -> list[dict]:
    """Latest snapshot per article, newest first.

    `since` keeps only articles published on or after that calendar date,
    read in the papers' timezone. `journals` keeps only those title slugs —
    an empty list keeps none, which is what unticking every box means.
    """
    sql = """
        SELECT DISTINCT ON (a.id)
               a.id, a.canonical_url, a.origin, a.source_file, j.slug AS journal,
               s.headline, s.subhead, s.author, s.source, s.section,
               s.published_at, s.id AS snapshot_id,
               -- Prefer the number of comments actually stored; fall back to the
               -- count the page announced (the live site does not expose one).
               COALESCE(cc.n, s.comment_count) AS comment_count
        FROM article a
        JOIN journal j ON j.id = a.journal_id
        LEFT JOIN article_snapshot s ON s.article_id = a.id
        LEFT JOIN (SELECT article_id, count(*) AS n FROM comment GROUP BY article_id) cc
               ON cc.article_id = a.id
        -- Search is a case-insensitive POSIX regex (~*). Casts are required:
        -- without them Postgres cannot infer the type of a bare "$1 IS NULL".
        WHERE (%(q)s::text IS NULL OR s.headline ~* %(q)s::text)
          AND (%(journals)s::text[] IS NULL OR j.slug = ANY(%(journals)s::text[]))
        ORDER BY a.id, s.fetched_at DESC
    """
    with conn.cursor() as cur:
        try:
            # The date is matched outside the DISTINCT ON, against the
            # snapshot actually shown. Filtering inside would let an article
            # surface on an older snapshot's date while displaying the newer
            # one's — and would no longer agree with the count in
            # dataset_stats.
            cur.execute(f"SELECT * FROM ({sql}) t "
                        f"WHERE (%(since)s::date IS NULL "
                        f"       OR (t.published_at AT TIME ZONE %(tz)s)::date >= %(since)s::date) "
                        f"ORDER BY published_at DESC NULLS LAST "
                        f"LIMIT %(limit)s OFFSET %(offset)s",
                        {"q": q, "journals": journals, "since": since, "tz": tz,
                         "limit": limit, "offset": offset})
        except Exception as exc:
            _guard_regex(exc)
        return _rows(cur)


def window_journal_counts(conn, since: date | None = None,
                          tz: str = PAPER_TZ) -> list[dict]:
    """Per-title article counts for a window, before any journal filter.

    Deliberately unfiltered: the tick boxes have to say what unticking would
    remove, so they cannot be counted from the filtered list they control.
    Titles with nothing in the window are still listed, at zero.
    """
    with conn.cursor() as cur:
        cur.execute("""
            WITH latest AS (
                SELECT DISTINCT ON (a.id) a.id, a.journal_id, s.published_at
                FROM article a
                LEFT JOIN article_snapshot s ON s.article_id = a.id
                ORDER BY a.id, s.fetched_at DESC
            )
            SELECT j.slug, j.name, count(l.id) AS n
            FROM journal j
            LEFT JOIN latest l ON l.journal_id = j.id
                 AND (%s::date IS NULL
                      OR (l.published_at AT TIME ZONE %s)::date >= %s::date)
            GROUP BY j.slug, j.name
            ORDER BY j.slug
        """, (since, tz, since))
        return _rows(cur)


def get_article(conn, article_id: str, snapshot_id: int | None = None) -> dict | None:
    """One article with its chosen (default latest) snapshot, images and comments."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.*, a.canonical_url, a.origin, a.source_file, j.slug AS journal, j.name AS journal_name
            FROM article_snapshot s
            JOIN article a ON a.id = s.article_id
            JOIN journal j ON j.id = a.journal_id
            WHERE s.article_id = %s AND (%s::bigint IS NULL OR s.id = %s)
            ORDER BY s.fetched_at DESC LIMIT 1
        """, (article_id, snapshot_id, snapshot_id))
        rows = _rows(cur)
        if not rows:
            return None
        art = rows[0]
        cur.execute("""
            SELECT ai.sha256, ai.role, ai.position, ai.alt_text, ai.caption,
                   i.mime, i.width, i.height, i.storage_path
            FROM article_image ai JOIN image i ON i.sha256 = ai.sha256
            WHERE ai.snapshot_id = %s ORDER BY ai.position
        """, (art["id"],))
        art["images"] = _rows(cur)
        cur.execute("""
            SELECT DISTINCT ON (c.id)
                   c.id, c.author_nick, c.parent_id, cs.posted_at, cs.body_text,
                   cs.like_count, cs.reply_count, cs.raw_meta
            FROM comment c JOIN comment_snapshot cs ON cs.comment_id = c.id
            WHERE c.article_id = %s
            ORDER BY c.id, cs.fetched_at DESC
        """, (article_id,))
        art["comments"] = sorted(_rows(cur), key=lambda c: (c["posted_at"] is None, c["posted_at"]))
        return art


def browse_commenters(conn, *, q: str | None = None, limit: int = 200,
                      offset: int = 0) -> list[dict]:
    with conn.cursor() as cur:
        try:
            cur.execute("""
            SELECT c.author_nick AS nick,
                   count(DISTINCT c.id) AS comments,
                   count(DISTINCT c.article_id) AS articles,
                   min(cs.posted_at) AS first_seen,
                   max(cs.posted_at) AS last_seen,
                   count(DISTINCT j.slug) AS journals,
                   sum(cs.like_count) AS total_votes,
                   max(pa.persona_id) AS persona_id,
                   max(p.label) AS persona_label
            FROM comment c
            JOIN comment_snapshot cs ON cs.comment_id = c.id
            JOIN article a ON a.id = c.article_id
            JOIN journal j ON j.id = a.journal_id
            LEFT JOIN persona_alias pa ON pa.nick = c.author_nick AND pa.journal_slug = '*'
            LEFT JOIN persona p ON p.id = pa.persona_id
            WHERE c.author_nick IS NOT NULL
              AND (%(q)s::text IS NULL OR c.author_nick ~* %(q)s::text)
            GROUP BY c.author_nick
            ORDER BY comments DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """, {"q": q, "limit": limit, "offset": offset})
        except Exception as exc:
            _guard_regex(exc)
        return _rows(cur)


def get_commenter(conn, nick: str, limit: int = 500) -> dict:
    comments = comments_for_nicks(conn, [nick], limit=10 ** 9)
    return {
        "nick": nick,
        "comments": comments[:limit],
        "total": len(comments),
        "persona": persona_for_nick(conn, nick),
        "suggestions": suggest_aliases(conn, nick),
    }


# --------------------------------------------------------------------------- #
# Personas: several nicknames -> one person, so analysis runs on the person
# --------------------------------------------------------------------------- #

def list_personas(conn) -> list[dict]:
    """Personas with their aliases and merged activity totals."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.id, p.label, p.note,
                   array_agg(pa.nick ORDER BY pa.nick) AS aliases,
                   count(DISTINCT pa.nick) AS n_aliases
            FROM persona p
            LEFT JOIN persona_alias pa ON pa.persona_id = p.id
            GROUP BY p.id, p.label, p.note
            ORDER BY p.label
        """)
        personas = _rows(cur)
        for p in personas:
            p["aliases"] = [a for a in (p["aliases"] or []) if a]
            p.update(_persona_totals(conn, p["aliases"]))
        return personas


def _persona_totals(conn, nicks: list[str]) -> dict:
    if not nicks:
        return {"comments": 0, "articles": 0, "first_seen": None,
                "last_seen": None, "journals": 0, "total_votes": None}
    with conn.cursor() as cur:
        cur.execute("""
            SELECT count(DISTINCT c.id), count(DISTINCT c.article_id),
                   min(cs.posted_at), max(cs.posted_at),
                   count(DISTINCT j.slug), sum(cs.like_count)
            FROM comment c
            JOIN comment_snapshot cs ON cs.comment_id = c.id
            JOIN article a ON a.id = c.article_id
            JOIN journal j ON j.id = a.journal_id
            WHERE c.author_nick = ANY(%s)
        """, (nicks,))
        n_c, n_a, first, last, n_j, votes = cur.fetchone()
    return {"comments": n_c or 0, "articles": n_a or 0, "first_seen": first,
            "last_seen": last, "journals": n_j or 0, "total_votes": votes}


def persona_for_nick(conn, nick: str, journal_slug: str | None = None) -> dict | None:
    """The persona a nickname belongs to, preferring a journal-scoped mapping."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.id, p.label, pa.confidence, pa.journal_slug
            FROM persona_alias pa JOIN persona p ON p.id = pa.persona_id
            WHERE pa.nick = %s AND pa.journal_slug IN ('*', COALESCE(%s, '*'))
            ORDER BY (pa.journal_slug <> '*') DESC
            LIMIT 1
        """, (nick, journal_slug))
        rows = _rows(cur)
        if not rows:
            return None
        p = rows[0]
        cur.execute("SELECT nick FROM persona_alias WHERE persona_id = %s ORDER BY nick", (p["id"],))
        p["aliases"] = [r[0] for r in cur.fetchall()]
        return p


def get_persona(conn, persona_id: int, limit: int = 3000) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT id, label, note FROM persona WHERE id = %s", (persona_id,))
        rows = _rows(cur)
        if not rows:
            return None
        p = rows[0]
        cur.execute("""
            SELECT nick, journal_slug, confidence, evidence, added_by, added_at
            FROM persona_alias WHERE persona_id = %s ORDER BY nick
        """, (persona_id,))
        p["alias_rows"] = _rows(cur)
    nicks = [a["nick"] for a in p["alias_rows"]]
    p["aliases"] = nicks
    p.update(_persona_totals(conn, nicks))
    p["comments_list"] = comments_for_nicks(conn, nicks, limit=limit)
    return p


def comments_for_nicks(conn, nicks: list[str], limit: int = 3000) -> list[dict]:
    """Merged comment history across several nicknames, oldest first."""
    if not nicks:
        return []
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (c.id)
                   c.id, c.article_id, c.author_nick, cs.posted_at, cs.body_text,
                   cs.like_count, s.headline, j.slug AS journal, a.origin
            FROM comment c
            JOIN comment_snapshot cs ON cs.comment_id = c.id
            JOIN article a ON a.id = c.article_id
            JOIN journal j ON j.id = a.journal_id
            LEFT JOIN LATERAL (
                SELECT headline FROM article_snapshot
                WHERE article_id = a.id ORDER BY fetched_at DESC LIMIT 1
            ) s ON true
            WHERE c.author_nick = ANY(%s)
            ORDER BY c.id, cs.fetched_at DESC
        """, (nicks,))
        rows = _rows(cur)
    rows.sort(key=lambda c: (c["posted_at"] is None, c["posted_at"]))
    return rows[:limit]


def create_persona(conn, *, label: str, note: str | None = None,
                   community: str = "lematin") -> int:
    """Create or reuse a person. Labels are unique within a community, not
    globally: the same handle in two comment backends is two people until
    something says otherwise, which is the same rule the rest of the schema
    follows."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO persona (community, label, note) VALUES (%s, %s, %s)
            ON CONFLICT (community, label)
            DO UPDATE SET note = COALESCE(EXCLUDED.note, persona.note),
                          updated_at = now()
            RETURNING id
        """, (community, label, note))
        return cur.fetchone()[0]


def add_alias(conn, *, persona_id: int, nick: str, journal_slug: str = "*",
              community: str | None = None, confidence: str = "confirmed",
              evidence: str | None = None, added_by: str = "manual") -> None:
    """Attach a nickname to a person. Keyed on (community, nick), which is the
    unit a nickname actually identifies someone in; journal_slug is still
    written for the rows that predate the community column."""
    community = community or persona_community(conn, persona_id) or "lematin"
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO persona_alias (community, journal_slug, nick, persona_id,
                                       confidence, evidence, added_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (community, nick) DO UPDATE SET
                persona_id = EXCLUDED.persona_id,
                confidence = EXCLUDED.confidence,
                evidence   = COALESCE(EXCLUDED.evidence, persona_alias.evidence),
                added_by   = EXCLUDED.added_by,
                added_at   = now()
        """, (community, journal_slug, nick, persona_id, confidence, evidence, added_by))


def persona_community(conn, persona_id) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT community FROM persona WHERE id = %s::bigint", (str(persona_id),))
        row = cur.fetchone()
        return row[0] if row else None


def remove_alias(conn, *, nick: str, journal_slug: str = "*",
                 community: str | None = None) -> None:
    with conn.cursor() as cur:
        if community:
            cur.execute("DELETE FROM persona_alias WHERE nick = %s AND community = %s",
                        (nick, community))
        else:
            cur.execute("DELETE FROM persona_alias WHERE nick = %s", (nick,))


def delete_persona(conn, persona_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM persona WHERE id = %s", (persona_id,))


def suggest_aliases(conn, nick: str, limit: int = 12) -> list[dict]:
    """Cheap, transparent candidates for the same person: other nicknames whose
    normalized spelling (letters/digits only, lowercased) is identical or nested.
    This is a spelling heuristic only — it catches renames like
    'C est pas mal hein' -> 'C-est-pas-mal-hein', not stylometric matches."""
    with conn.cursor() as cur:
        cur.execute("""
            WITH norm AS (
                SELECT DISTINCT author_nick AS nick,
                       regexp_replace(lower(author_nick), '[^a-z0-9]', '', 'g') AS key
                FROM comment WHERE author_nick IS NOT NULL
            ), target AS (SELECT key FROM norm WHERE nick = %s LIMIT 1)
            SELECT n.nick, n.key,
                   (SELECT count(*) FROM comment c WHERE c.author_nick = n.nick) AS comments
            FROM norm n, target t
            WHERE n.nick <> %s
              AND (n.key = t.key OR n.key LIKE '%%' || t.key || '%%' OR t.key LIKE '%%' || n.key || '%%')
              -- both keys must be substantial: a 1-2 char key is a substring of
              -- almost anything and would match unrelated nicknames.
              AND length(t.key) > 3 AND length(n.key) > 3
            ORDER BY comments DESC LIMIT %s
        """, (nick, nick, limit))
        return _rows(cur)


def persona_alias_nicks(conn, persona_id) -> list[str]:
    """Just the nicknames of a persona — no comments, unlike `get_persona`."""
    with conn.cursor() as cur:
        cur.execute("SELECT nick FROM persona_alias WHERE persona_id = %s::bigint "
                    "ORDER BY nick", (persona_id,))
        return [r[0] for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
# Notes: what a reader knows that the corpus does not
# --------------------------------------------------------------------------- #

def note_subjects(kind: str, key, aliases: list[str] | None = None) -> list[tuple[str, str]]:
    """The (kind, key) pairs whose notes belong to this subject.

    A persona reads its own notes AND those written against each of its
    handles. Linking two nicknames into one person must not hide what was
    already recorded about either of them — the whole point of the link is that
    they are the same writer, so their notes are the same writer's notes.

    A nickname reads only its own: a note attached to a persona is about the
    person across every handle, and attributing it to one of them would say
    more than the note does.
    """
    pairs = [(kind, str(key))]
    if kind == "persona":
        pairs += [("nick", n) for n in (aliases or [])]
    return pairs


def subject_community(conn, *, kind: str, key) -> str:
    """Which comment public a subject writes in.

    Needed because a note is stored per community for the same reason a profile
    is: a nickname only identifies someone inside one comment backend. A
    persona carries its community; a bare nickname is placed by where it has
    actually commented, most-used first.
    """
    if kind == "persona":
        return persona_community(conn, key) or "lematin"
    with conn.cursor() as cur:
        cur.execute("""
            SELECT j.community, count(*) AS n
            FROM comment c
            JOIN article a ON a.id = c.article_id
            JOIN journal j ON j.id = a.journal_id
            WHERE c.author_nick = %s
            GROUP BY 1 ORDER BY n DESC LIMIT 1
        """, (str(key),))
        row = cur.fetchone()
        return row[0] if row else "lematin"


def list_notes(conn, *, kind: str, key, community: str | None = None) -> list[dict]:
    """A subject's notes, oldest first — the order they were learnt in.

    Each row carries the handle it was written against, so a persona's page can
    show that a remark predates the link.
    """
    community = community or subject_community(conn, kind=kind, key=key)
    aliases = persona_alias_nicks(conn, key) if kind == "persona" else []
    pairs = note_subjects(kind, key, aliases)
    where = " OR ".join(["(subject_kind = %s AND subject_key = %s)"] * len(pairs))
    args = [community] + [v for pair in pairs for v in pair]
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT id, community, subject_kind, subject_key, body, source,
                   created_at, updated_at
            FROM subject_note
            WHERE community = %s AND ({where})
            ORDER BY created_at, id
        """, args)
        return _rows(cur)


def _note_body(body: str | None) -> str:
    text = (body or "").strip()
    if not text:
        raise ValueError("a note needs something in it")
    return text


def _note_source(source: str | None) -> str | None:
    text = (source or "").strip()
    return text or None


def add_note(conn, *, kind: str, key, body: str, source: str | None = None,
             community: str | None = None) -> int:
    """Record one observation about a subject. Returns the new note's id."""
    text = _note_body(body)                      # refused before it reaches the DB
    community = community or subject_community(conn, kind=kind, key=key)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO subject_note (community, subject_kind, subject_key, body, source)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
        """, (community, kind, str(key), text, _note_source(source)))
        return cur.fetchone()[0]


def note_subject(conn, note_id: int) -> tuple[str, str, str] | None:
    """(kind, key, community) a note hangs off, or None if it is gone."""
    with conn.cursor() as cur:
        cur.execute("SELECT subject_kind, subject_key, community FROM subject_note "
                    "WHERE id = %s", (note_id,))
        row = cur.fetchone()
        return (row[0], row[1], row[2]) if row else None


def update_note(conn, note_id: int, *, body: str, source: str | None = None) -> None:
    text = _note_body(body)
    with conn.cursor() as cur:
        cur.execute("UPDATE subject_note SET body = %s, source = %s, updated_at = now() "
                    "WHERE id = %s", (text, _note_source(source), note_id))


def delete_note(conn, note_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM subject_note WHERE id = %s", (note_id,))


def note_counts(conn, community: str | None = None) -> dict[str, int]:
    """How many notes each nickname carries, for marking a list. Persona notes
    are counted under every handle of that persona, since that is where a
    reader will look for them.

    Resolved in Python rather than by joining on subject_key: the column holds
    a persona id for one kind of row and a nickname for the other, so any SQL
    that casts it to an integer fails on the first note written about a
    nickname — and takes the commenter list down with it.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT subject_kind, subject_key, count(*)
            FROM subject_note
            WHERE (%s::text IS NULL OR community = %s::text)
            GROUP BY 1, 2
        """, (community, community))
        rows = cur.fetchall()
        persona_keys = [k for kind, k, _ in rows if kind == "persona"]
        aliases: dict[str, list[str]] = {}
        if persona_keys:
            cur.execute("SELECT persona_id::text, nick FROM persona_alias "
                        "WHERE persona_id = ANY(%s::bigint[])", (persona_keys,))
            for pid, nick in cur.fetchall():
                aliases.setdefault(pid, []).append(nick)

    counts: dict[str, int] = {}
    for kind, key, n in rows:
        for nick in ([key] if kind == "nick" else aliases.get(key, [])):
            counts[nick] = counts.get(nick, 0) + n
    return counts


# --------------------------------------------------------------------------- #
# The same writer elsewhere
# --------------------------------------------------------------------------- #

# Networks worth naming apart, because they are not the same publics. Anything
# else is stored under its host, which is more useful than a catch-all "other".
PLATFORM_HOSTS = {
    "facebook.com": "facebook", "fb.com": "facebook", "m.facebook.com": "facebook",
    "twitter.com": "x", "x.com": "x",
    "instagram.com": "instagram",
    "youtube.com": "youtube", "youtu.be": "youtube",
    "tiktok.com": "tiktok",
    "linkedin.com": "linkedin",
    "reddit.com": "reddit",
    "bsky.app": "bluesky",
    "mastodon.social": "mastodon",
}


def account_platform(url: str | None, fallback: str | None = None) -> str:
    """Which network a pasted link belongs to.

    Derived from the URL rather than asked for, because a reader pasting a link
    already said which platform it is and being made to say it twice is how the
    two end up disagreeing. An unrecognised host becomes the host itself.
    """
    if fallback:
        return fallback.strip().lower()
    text = (url or "").strip()
    if not text:
        return "other"
    host = re.sub(r"^https?://", "", text, flags=re.I).split("/")[0].split("?")[0]
    host = host.split("@")[-1].lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return PLATFORM_HOSTS.get(host, host or "other")


def list_accounts(conn, *, kind: str, key, community: str | None = None) -> list[dict]:
    """A subject's known accounts elsewhere. Scoped like notes: a persona also
    sees what was recorded against each of its handles."""
    community = community or subject_community(conn, kind=kind, key=key)
    aliases = persona_alias_nicks(conn, key) if kind == "persona" else []
    pairs = note_subjects(kind, key, aliases)
    where = " OR ".join(["(subject_kind = %s AND subject_key = %s)"] * len(pairs))
    args = [community] + [v for pair in pairs for v in pair]
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT id, community, subject_kind, subject_key, platform, handle,
                   url, confidence, evidence, added_at, updated_at
            FROM subject_account
            WHERE community = %s AND ({where})
            ORDER BY platform, added_at, id
        """, args)
        return _rows(cur)


def add_account(conn, *, kind: str, key, url: str | None = None,
                platform: str | None = None, handle: str | None = None,
                confidence: str = "confirmed", evidence: str | None = None,
                community: str | None = None) -> int:
    """Record the same writer on another network. Needs a link or a handle —
    an account nobody can go and look at is not an observation."""
    url = _note_source(url)
    handle = _note_source(handle)
    if not url and not handle:
        raise ValueError("an account needs a link or a handle")
    community = community or subject_community(conn, kind=kind, key=key)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO subject_account (community, subject_kind, subject_key,
                                         platform, handle, url, confidence, evidence)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """, (community, kind, str(key), account_platform(url, platform), handle,
              url, confidence, _note_source(evidence)))
        return cur.fetchone()[0]


def account_subject(conn, account_id: int) -> tuple[str, str, str] | None:
    """(kind, key, community) an account row hangs off, or None if it is gone."""
    with conn.cursor() as cur:
        cur.execute("SELECT subject_kind, subject_key, community FROM subject_account "
                    "WHERE id = %s", (account_id,))
        row = cur.fetchone()
        return (row[0], row[1], row[2]) if row else None


def update_account(conn, account_id: int, *, url: str | None = None,
                   platform: str | None = None, handle: str | None = None,
                   confidence: str = "confirmed", evidence: str | None = None) -> None:
    url = _note_source(url)
    handle = _note_source(handle)
    if not url and not handle:
        raise ValueError("an account needs a link or a handle")
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE subject_account
               SET platform = %s, handle = %s, url = %s, confidence = %s,
                   evidence = %s, updated_at = now()
             WHERE id = %s
        """, (account_platform(url, platform), handle, url, confidence,
              _note_source(evidence), account_id))


def delete_account(conn, account_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM subject_account WHERE id = %s", (account_id,))


def elsewhere_overview(conn, community: str | None = None) -> dict:
    """How much off-platform identity the corpus knows about, by network. The
    counting question this table exists to answer."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT platform, count(*) AS accounts,
                   count(DISTINCT (subject_kind, subject_key)) AS subjects
            FROM subject_account
            WHERE (%s::text IS NULL OR community = %s::text)
            GROUP BY 1 ORDER BY 2 DESC
        """, (community, community))
        return {"platforms": _rows(cur)}


def get_profile(conn, *, nick: str | None = None, persona_id: int | None = None,
                community: str | None = None) -> dict | None:
    """The analysis profile for a subject. A nickname that belongs to a persona
    resolves to the persona's profile, since that is the analysed unit.

    A nickname only identifies someone inside one comment community, so without
    `community` this can only answer when the nickname is unambiguous across
    them. When it is not, the caller has to say which population it means.
    """
    with conn.cursor() as cur:
        if persona_id is None and nick is not None:
            if community is not None:
                cur.execute("SELECT persona_id FROM persona_alias "
                            "WHERE nick = %s AND community = %s LIMIT 1", (nick, community))
            else:
                cur.execute("SELECT persona_id FROM persona_alias WHERE nick = %s LIMIT 1", (nick,))
            row = cur.fetchone()
            if row:
                persona_id = row[0]
        kind = "persona" if persona_id is not None else "nick"
        key = str(persona_id) if persona_id is not None else nick
        if community is not None:
            cur.execute("SELECT * FROM author_profile WHERE community=%s "
                        "AND subject_kind=%s AND subject_key=%s", (community, kind, key))
        else:
            cur.execute("SELECT * FROM author_profile WHERE subject_kind=%s "
                        "AND subject_key=%s ORDER BY n_comments DESC", (kind, key))
        rows = _rows(cur)
        if len(rows) > 1:
            log.info("nickname %r profiled in %d communities; caller did not say which",
                     key, len(rows))
        return rows[0] if rows else None


def profile_overview(conn, *, community: str | None = None) -> dict:
    """Aggregate view of the profiled population — the sociological summary.

    Communities are different publics and are not pooled by default in any way
    that hides which is which: `communities` reports the split, and passing
    `community` narrows every figure to one of them.
    """
    out: dict = {}
    # Every query below is filtered the same way. With no community given the
    # clause is a no-op and the view spans all of them.
    where = "community = %s" if community else "TRUE"
    arg: tuple = (community,) if community else ()
    out["community"] = community
    with conn.cursor() as cur:
        cur.execute("SELECT community, count(*) FROM author_profile GROUP BY 1 ORDER BY 2 DESC")
        out["communities"] = _rows(cur)
        cur.execute(f"SELECT count(*), count(*) FILTER (WHERE subject_kind='persona') "
                    f"FROM author_profile WHERE {where}", arg)
        out["profiles"], out["personas"] = cur.fetchone()
        cur.execute(f"""
            SELECT language->>'mastery' AS mastery, count(*)
            FROM author_profile WHERE language ? 'mastery' AND {where}
            GROUP BY 1 ORDER BY 2 DESC
        """, arg)
        out["mastery"] = _rows(cur)
        cur.execute(f"""
            SELECT politics->>'overall' AS leaning, count(*)
            FROM author_profile WHERE politics ? 'overall' AND {where}
            GROUP BY 1 ORDER BY 2 DESC
        """, arg)
        out["politics"] = _rows(cur)
        cur.execute(f"""
            SELECT CASE
                     WHEN (gender->>'male')::float   >= 0.6 THEN 'male'
                     WHEN (gender->>'female')::float >= 0.6 THEN 'female'
                     ELSE 'unknown' END AS g, count(*)
            FROM author_profile WHERE gender ? 'male' AND {where}
            GROUP BY 1 ORDER BY 2 DESC
        """, arg)
        out["gender"] = _rows(cur)
        cur.execute(f"""
            SELECT region->>'guess' AS region, count(*)
            FROM author_profile WHERE region ? 'guess' AND {where}
            GROUP BY 1 ORDER BY 2 DESC LIMIT 12
        """, arg)
        out["region"] = _rows(cur)
        # Every subject, not a top-N: the population table sorts on any column
        # and the scatter needs the whole cloud. 319 rows is nothing to ship,
        # and it lets the distributions be recomputed with a subject excluded
        # without another round trip.
        cur.execute(f"""
            SELECT community, label, subject_kind, subject_key, n_comments, n_chars,
                   first_seen, last_seen,
                   language->>'mastery' AS mastery,
                   (language->>'error_rate_per_100_words')::float AS err,
                   language->>'accent_usage' AS accents,
                   language->>'register' AS register,
                   (metrics->>'avg_words_per_comment')::float AS avg_words,
                   (metrics->>'vocabulary_richness_ttr')::float AS ttr,
                   politics->>'overall' AS leaning, politics->>'drift' AS drift,
                   (politics->>'confidence')::float AS politics_confidence,
                   region->>'guess' AS region,
                   gender->>'basis' AS gender_basis,
                   (gender->>'male')::float AS male,
                   (gender->>'female')::float AS female
            FROM author_profile WHERE {where} ORDER BY n_comments DESC
        """, arg)
        out["subjects"] = _rows(cur)
        # 'marked' turned out to be vanishingly rare once the pass was told not
        # to manufacture arcs, so 'mild' is where the real movement shows up.
        # Listing only 'marked' hid every subject who actually changed.
        cur.execute(f"""
            SELECT community, label, subject_kind, subject_key,
                   politics->>'drift' AS drift,
                   politics->'periods' AS periods
            FROM author_profile WHERE politics->>'drift' IN ('marked', 'mild') AND {where}
            ORDER BY (politics->>'drift' = 'marked') DESC, n_comments DESC LIMIT 25
        """, arg)
        out["drifters"] = _rows(cur)
    return out


def browse_authors(conn, *, limit: int = 200) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.author, count(DISTINCT s.article_id) AS articles,
                   min(s.published_at) AS first_seen, max(s.published_at) AS last_seen,
                   count(DISTINCT j.slug) AS journals
            FROM article_snapshot s
            JOIN article a ON a.id = s.article_id
            JOIN journal j ON j.id = a.journal_id
            WHERE s.author IS NOT NULL AND s.author <> ''
            GROUP BY s.author ORDER BY articles DESC LIMIT %s
        """, (limit,))
        return _rows(cur)


def browse_sources(conn, *, limit: int = 200) -> list[dict]:
    """Who supplied the news, one row per agency.

    A byline is stored as it was published, and the same agency appears as
    "AFP", "Agence France-Presse" and "afp/Newsnet" across the years. Grouping
    happens here rather than in the stored value, so the spellings actually seen
    stay visible in `variants`.
    """
    from .sources.tamedia import normalize_agency

    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.source, count(DISTINCT s.article_id) AS articles,
                   min(s.published_at) AS first_seen, max(s.published_at) AS last_seen
            FROM article_snapshot s
            WHERE s.source IS NOT NULL AND s.source <> ''
            GROUP BY s.source
        """)
        merged: dict[str, dict] = {}
        for r in _rows(cur):
            name = normalize_agency(r["source"]) or r["source"]
            m = merged.setdefault(name, {
                "source": name, "articles": 0,
                "first_seen": r["first_seen"], "last_seen": r["last_seen"],
                "variants": [],
            })
            m["articles"] += r["articles"]
            if r["source"] != name:
                m["variants"].append(r["source"])
            for k, better in (("first_seen", min), ("last_seen", max)):
                if r[k] and m[k]:
                    m[k] = better(m[k], r[k])
                elif r[k]:
                    m[k] = r[k]
    out = sorted(merged.values(), key=lambda m: -m["articles"])
    for m in out:
        m["variants"].sort()
    return out[:limit]


def dataset_stats(conn) -> dict:
    out: dict = {}
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM article"); out["articles"] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM comment"); out["comments"] = cur.fetchone()[0]
        cur.execute("SELECT count(DISTINCT author_nick) FROM comment WHERE author_nick IS NOT NULL")
        out["commenters"] = cur.fetchone()[0]
        cur.execute("SELECT count(DISTINCT author) FROM article_snapshot WHERE author IS NOT NULL AND author <> ''")
        out["authors"] = cur.fetchone()[0]
        cur.execute("SELECT count(DISTINCT source) FROM article_snapshot WHERE source IS NOT NULL AND source <> ''")
        out["sources"] = cur.fetchone()[0]
        # Counts the same window the Today subtab lists, so the badge and the
        # table can never disagree.
        since = paper_window(RECENT_DAYS)
        cur.execute(f"SELECT count(*) FROM ({_LATEST_SNAPSHOT}) t "
                    f"WHERE (t.published_at AT TIME ZONE %s)::date >= %s", (PAPER_TZ, since))
        out["recent"] = cur.fetchone()[0]
        out["recent_from"] = str(since)
        cur.execute("SELECT count(*) FROM persona"); out["personas"] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM author_profile"); out["profiles"] = cur.fetchone()[0]
        cur.execute("SELECT origin, count(*) FROM article GROUP BY origin")
        out["by_origin"] = dict(cur.fetchall())
        cur.execute("SELECT min(posted_at)::date, max(posted_at)::date FROM comment_snapshot")
        lo, hi = cur.fetchone()
        out["comment_span"] = [str(lo) if lo else None, str(hi) if hi else None]
    return out


# A comment's local desk is the article's own section, which is identical on
# both titles of a shared backend. That makes it a property of the CONTENT and
# not of whichever title's article row the comment happens to hang on — the
# only regional signal in this community that scan order cannot distort.
_DESK_CTE = """
WITH desk AS (
    SELECT a.id AS article_id, a.source_key, j.slug,
           CASE split_part(s.section, '/', 1)
                WHEN 'geneve'       THEN 'geneva'
                WHEN 'vaud-regions' THEN 'vaud'
                WHEN 'valais'       THEN 'valais'
                ELSE 'national' END AS desk
    FROM article a
    JOIN journal j ON j.id = a.journal_id
    JOIN LATERAL (
        SELECT section FROM article_snapshot
        WHERE article_id = a.id ORDER BY fetched_at DESC LIMIT 1
    ) s ON true
    WHERE j.community = 'tx-romandie'
),
-- The two titles do not always agree on whether a syndicated story is local.
-- A Vaud story is 'vaud-regions' for 24 heures and 'suisse' for the Tribune;
-- a Geneva story is the mirror image. Where they disagree the article cannot
-- be evidence that a reader chose local content, because which desk they met
-- it under depends on which title they were reading — which the shared comment
-- thread does not record. Those articles are excluded from the local counts.
contested AS (
    SELECT source_key FROM desk WHERE source_key IS NOT NULL
    GROUP BY source_key
    HAVING count(DISTINCT slug) > 1 AND count(DISTINCT desk) > 1
),
reader AS (
    SELECT cm.author_nick AS nick,
           count(*) FILTER (WHERE d.desk = 'geneva')   AS ge,
           count(*) FILTER (WHERE d.desk = 'vaud')     AS vd,
           count(*) FILTER (WHERE d.desk = 'national') AS na
    FROM comment cm JOIN desk d ON d.article_id = cm.article_id
    WHERE cm.author_nick IS NOT NULL
      AND (d.source_key IS NULL OR d.source_key NOT IN (SELECT source_key FROM contested))
    GROUP BY 1
),
lean AS (
    -- "both" only when the weaker side is at least a third of the stronger;
    -- a single stray comment on the other canton is not a readership.
    SELECT nick, ge, vd, na,
           CASE WHEN ge = 0 AND vd = 0 THEN 'none'
                WHEN ge > 0 AND vd > 0
                     AND least(ge, vd)::float / greatest(ge, vd) >= 0.34 THEN 'both'
                WHEN ge > vd THEN 'geneva' ELSE 'vaud' END AS lean
    FROM reader
)
"""


def desk_overview(conn) -> dict:
    """Local-desk analysis for the shared Geneva/Vaud comment backend.

    The two titles share every syndicated article, so which title a comment sits
    under says nothing about the commenter. Which LOCAL DESK they choose to
    comment on does, and it is behavioural rather than read out of their prose.
    """
    out: dict = {}
    with conn.cursor() as cur:
        cur.execute(_DESK_CTE + """
            , content AS (
                SELECT source_key, min(desk) AS desk, count(DISTINCT slug) AS n_slugs
                FROM desk WHERE source_key IS NOT NULL
                GROUP BY source_key
            )
            SELECT desk,
                   count(*) FILTER (WHERE n_slugs > 1) AS shared,
                   count(*) FILTER (WHERE n_slugs = 1) AS exclusive
            FROM content GROUP BY desk ORDER BY count(*) DESC
        """)
        out["articles"] = _rows(cur)

        cur.execute(_DESK_CTE + "SELECT lean, count(*) AS n FROM lean GROUP BY 1 ORDER BY 2 DESC")
        out["readers"] = _rows(cur)

        cur.execute(_DESK_CTE + """
            SELECT (SELECT count(*) FROM contested)                       AS contested_articles,
                   (SELECT count(*) FROM desk WHERE source_key IS NOT NULL) AS article_rows,
                   (SELECT count(*) FROM comment cm JOIN desk d
                      ON d.article_id = cm.article_id
                     WHERE d.source_key IN (SELECT source_key FROM contested))
                                                                          AS comments_excluded
        """)
        got = _rows(cur)
        out["contested"] = got[0] if got else {}

        # The two readerships compared on everything the profiling pass produced.
        cur.execute(_DESK_CTE + """
            SELECT l.lean,
                   count(*) AS n,
                   count(*) FILTER (WHERE p.politics->>'overall'
                        IN ('left','centre-left','far-left'))        AS left_of_centre,
                   count(*) FILTER (WHERE p.politics->>'overall'
                        IN ('right','centre-right','far-right'))     AS right_of_centre,
                   count(*) FILTER (WHERE p.politics->>'overall'
                        IN ('mixed','unclear'))                      AS unaligned,
                   count(*) FILTER (WHERE p.language->>'mastery'
                        IN ('native-fluent','fluent'))               AS fluent_plus,
                   round(avg((p.language->>'error_rate_per_100_words')::float)::numeric, 2) AS mean_err,
                   round(avg(p.n_comments)::numeric, 1)              AS mean_dossier
            FROM lean l
            JOIN author_profile p
              ON p.community = 'tx-romandie' AND p.subject_kind = 'nick'
             AND p.subject_key = l.nick
            WHERE l.lean IN ('geneva','vaud')
            GROUP BY 1 ORDER BY 1
        """)
        out["compare"] = _rows(cur)

        # Independence audit. A region marker that describes WHICH THREADS the
        # subject commented on is the behavioural signal restated, so agreement
        # on those subjects proves nothing. Only markers quoting the subject's
        # own words make the two measures independent.
        cur.execute(_DESK_CTE + """
            SELECT
              count(*) FILTER (WHERE NOT thready)                       AS text_only,
              count(*) FILTER (WHERE thready AND NOT all_thready)       AS mixed,
              count(*) FILTER (WHERE all_thready)                       AS thread_only,
              count(*) FILTER (WHERE NOT thready AND testable)          AS independent_testable,
              count(*) FILTER (WHERE NOT thready AND testable
                               AND expected = lean)                     AS independent_agree
            FROM (
              SELECT p.subject_key, l.lean,
                     lower(p.region->>'guess') AS expected,
                     l.lean IN ('geneva','vaud')
                       AND p.region->>'guess' IN ('Geneva','Vaud')      AS testable,
                     EXISTS (SELECT 1 FROM jsonb_array_elements_text(p.region->'markers') m
                             WHERE m ~* '(comments? on|engaged? with|writes only on|follows|thread|desk)')
                       AS thready,
                     NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(p.region->'markers') m
                             WHERE m !~* '(comments? on|engaged? with|writes only on|follows|thread|desk)')
                       AS all_thready
              FROM author_profile p
              JOIN lean l ON l.nick = p.subject_key
              WHERE p.community = 'tx-romandie' AND p.subject_kind = 'nick'
                AND p.region ? 'markers'
                AND jsonb_array_length(p.region->'markers') > 0
            ) t
        """)
        got = _rows(cur)
        out["independence"] = got[0] if got else {}
    return out


def findings_overview(conn) -> dict:
    """Every live figure the Findings tab quotes, in one round trip.

    Findings are written prose, but any number inside one is recomputed here
    rather than typed into the text: a finding that hard-codes a count becomes
    quietly false the next time the scanner runs, and a study that cannot trust
    its own write-up is worse than one with no write-up.
    """
    out: dict = {}
    with conn.cursor() as cur:
        cur.execute("""
            SELECT j.community, count(DISTINCT a.id) AS articles,
                   count(cm.id) AS comments,
                   count(DISTINCT cm.author_nick) AS nicks
            FROM journal j
            LEFT JOIN article a ON a.journal_id = j.id
            LEFT JOIN comment cm ON cm.article_id = a.id
            GROUP BY 1 ORDER BY 1
        """)
        out["corpus"] = _rows(cur)

        cur.execute("""
            SELECT j.slug, j.community, count(DISTINCT a.id) AS articles,
                   count(cm.id) AS comments
            FROM journal j
            LEFT JOIN article a ON a.journal_id = j.id
            LEFT JOIN comment cm ON cm.article_id = a.id
            GROUP BY 1,2 ORDER BY 1
        """)
        out["titles"] = _rows(cur)

        # Distributions, per community, per SUBJECT — never per comment. One
        # heavy commenter would otherwise dominate every share on the page.
        for key, expr, where in (
            ("mastery",  "language->>'mastery'",  "language ? 'mastery'"),
            ("politics", "politics->>'overall'",  "politics ? 'overall'"),
            ("region",   "region->>'guess'",      "region ? 'guess'"),
            ("drift",    "politics->>'drift'",    "politics ? 'drift'"),
            ("register", "language->>'register'", "language ? 'register'"),
            ("accents",  "language->>'accent_usage'", "language ? 'accent_usage'"),
        ):
            cur.execute(f"""SELECT community, {expr} AS v, count(*) AS n
                            FROM author_profile WHERE {where}
                            GROUP BY 1,2 ORDER BY 3 DESC""")
            out[key] = _rows(cur)

        cur.execute("""
            SELECT community,
                   CASE WHEN (gender->>'male')::float   >= 0.6 THEN 'male'
                        WHEN (gender->>'female')::float >= 0.6 THEN 'female'
                        ELSE 'unknown' END AS v,
                   count(*) AS n
            FROM author_profile WHERE gender ? 'male' GROUP BY 1,2
        """)
        out["gender"] = _rows(cur)

        cur.execute("""
            SELECT community, count(*) AS n,
                   round(avg((language->>'error_rate_per_100_words')::float)::numeric, 2) AS mean_err,
                   round(avg((metrics->>'avg_words_per_comment')::float)::numeric, 1) AS mean_words,
                   sum(n_comments) AS comments, sum(n_chars) AS chars
            FROM author_profile GROUP BY 1 ORDER BY 1
        """)
        out["profiles"] = _rows(cur)

        # Corpus concentration: the share held by the single heaviest subject.
        cur.execute("""
            SELECT community, label, n_comments,
                   round(100.0 * n_comments / sum(n_comments) OVER (PARTITION BY community), 1) AS pct
            FROM author_profile
            ORDER BY community, n_comments DESC
        """)
        rows = _rows(cur)
        seen, top = set(), []
        for r in rows:
            if r["community"] not in seen:
                seen.add(r["community"]); top.append(r)
        out["heaviest"] = top

        # Nicknames present in more than one community. Kept as separate
        # subjects on purpose; this is the list that makes that testable.
        out["desks"] = desk_overview(conn)

        cur.execute("""
            WITH per_community AS (
                SELECT cm.author_nick AS nick, j.community, count(*) AS n
                FROM comment cm
                JOIN article a ON a.id = cm.article_id
                JOIN journal j ON j.id = a.journal_id
                WHERE cm.author_nick IS NOT NULL
                GROUP BY 1, 2
            )
            SELECT nick,
                   count(*) AS communities,
                   sum(n) AS total,
                   json_agg(json_build_object('community', community, 'n', n)
                            ORDER BY n DESC) AS split
            FROM per_community
            GROUP BY nick
            HAVING count(*) > 1
            ORDER BY sum(n) DESC, nick
        """)
        out["cross_community"] = _rows(cur)
    return out


def list_scan_runs(conn, *, slug: str | None = None, limit: int = 50) -> list[dict]:
    with conn.cursor() as cur:
        if slug:
            cur.execute(
                "SELECT * FROM scan_run WHERE slug=%s ORDER BY requested_at DESC LIMIT %s",
                (slug, limit),
            )
        else:
            cur.execute("SELECT * FROM scan_run ORDER BY requested_at DESC LIMIT %s", (limit,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
