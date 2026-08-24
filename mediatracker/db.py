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
from pathlib import Path
from typing import Any

try:  # graceful import: the daemon still boots (JSONL-only) without psycopg
    import psycopg
    from psycopg.types.json import Jsonb
except Exception:  # pragma: no cover - exercised only where psycopg is absent
    psycopg = None  # type: ignore
    Jsonb = None  # type: ignore

log = logging.getLogger(__name__)

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

-- Additive column upgrades (safe to run every boot).
ALTER TABLE article_snapshot ADD COLUMN IF NOT EXISTS source TEXT;   -- news agency (Reuters/AFP/ATS…)
ALTER TABLE article ADD COLUMN IF NOT EXISTS gone_at TIMESTAMPTZ;    -- when the article stopped being reachable
ALTER TABLE article ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'live';  -- 'live' | 'pdf'
ALTER TABLE article ADD COLUMN IF NOT EXISTS source_file TEXT;       -- archival source (e.g. a printed PDF)
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
                   comment_system: str | None = None, config: dict | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO journal (id, slug, name, base_url, comment_system, config)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                base_url = EXCLUDED.base_url,
                comment_system = COALESCE(EXCLUDED.comment_system, journal.comment_system)
            -- NOTE: config is intentionally NOT overwritten here; the GUI owns it
            -- (see update_journal_config). Only the initial INSERT sets it.
            """,
            (jid, slug, name, base_url, comment_system, _jsonb(config)),
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


def active_article_urls(conn, journal_id: str, *, since_days: int) -> list[str]:
    """Canonical URLs of articles seen recently and not marked gone — these are
    re-scanned each cycle so comment/vote evolution keeps being captured until
    the article disappears."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT canonical_url FROM article
            WHERE journal_id = %s AND gone_at IS NULL
              AND last_seen > now() - make_interval(days => %s)
            ORDER BY last_seen DESC
            """,
            (journal_id, since_days),
        )
        return [r[0] for r in cur.fetchall()]


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

def _rows(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def browse_articles(conn, *, q: str | None = None, journal: str | None = None,
                    limit: int = 100, offset: int = 0) -> list[dict]:
    """Latest snapshot per article, newest first."""
    sql = """
        SELECT DISTINCT ON (a.id)
               a.id, a.canonical_url, a.origin, a.source_file, j.slug AS journal,
               s.headline, s.subhead, s.author, s.source, s.section,
               s.published_at, s.comment_count, s.id AS snapshot_id
        FROM article a
        JOIN journal j ON j.id = a.journal_id
        LEFT JOIN article_snapshot s ON s.article_id = a.id
        WHERE (%(q)s IS NULL OR s.headline ILIKE '%%' || %(q)s || '%%')
          AND (%(journal)s IS NULL OR j.slug = %(journal)s)
        ORDER BY a.id, s.fetched_at DESC
    """
    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM ({sql}) t ORDER BY published_at DESC NULLS LAST "
                    f"LIMIT %(limit)s OFFSET %(offset)s",
                    {"q": q, "journal": journal, "limit": limit, "offset": offset})
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
        cur.execute("""
            SELECT c.author_nick AS nick,
                   count(DISTINCT c.id) AS comments,
                   count(DISTINCT c.article_id) AS articles,
                   min(cs.posted_at) AS first_seen,
                   max(cs.posted_at) AS last_seen,
                   count(DISTINCT j.slug) AS journals,
                   sum(cs.like_count) AS total_votes
            FROM comment c
            JOIN comment_snapshot cs ON cs.comment_id = c.id
            JOIN article a ON a.id = c.article_id
            JOIN journal j ON j.id = a.journal_id
            WHERE c.author_nick IS NOT NULL
              AND (%(q)s IS NULL OR c.author_nick ILIKE '%%' || %(q)s || '%%')
            GROUP BY c.author_nick
            ORDER BY comments DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """, {"q": q, "limit": limit, "offset": offset})
        return _rows(cur)


def get_commenter(conn, nick: str, limit: int = 500) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (c.id)
                   c.id, c.article_id, cs.posted_at, cs.body_text, cs.like_count,
                   s.headline, j.slug AS journal, a.origin
            FROM comment c
            JOIN comment_snapshot cs ON cs.comment_id = c.id
            JOIN article a ON a.id = c.article_id
            JOIN journal j ON j.id = a.journal_id
            LEFT JOIN LATERAL (
                SELECT headline FROM article_snapshot
                WHERE article_id = a.id ORDER BY fetched_at DESC LIMIT 1
            ) s ON true
            WHERE c.author_nick = %s
            ORDER BY c.id, cs.fetched_at DESC
        """, (nick,))
        comments = sorted(_rows(cur), key=lambda c: (c["posted_at"] is None, c["posted_at"]))
        return {"nick": nick, "comments": comments[:limit], "total": len(comments)}


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
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.source, count(DISTINCT s.article_id) AS articles,
                   min(s.published_at) AS first_seen, max(s.published_at) AS last_seen
            FROM article_snapshot s
            WHERE s.source IS NOT NULL AND s.source <> ''
            GROUP BY s.source ORDER BY articles DESC LIMIT %s
        """, (limit,))
        return _rows(cur)


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
        cur.execute("SELECT origin, count(*) FROM article GROUP BY origin")
        out["by_origin"] = dict(cur.fetchall())
        cur.execute("SELECT min(posted_at)::date, max(posted_at)::date FROM comment_snapshot")
        lo, hi = cur.fetchone()
        out["comment_span"] = [str(lo) if lo else None, str(hi) if hi else None]
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
