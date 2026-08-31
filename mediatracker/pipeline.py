"""Journal-agnostic ingest pipeline.

Takes a Source adapter's parsed output and persists it uniformly: assigns stable
ids, downloads/deduplicates images, and writes article/comment snapshots only
when their content actually changed. Postgres is the primary sink; when it is
down every record is mirrored to the JSONL store so nothing is lost.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from . import db, ids
from .fetch import Fetcher, FetchError
from .images import BlobStore
from .sources.base import ParsedArticle, ParsedComment, Source

log = logging.getLogger(__name__)


@dataclass
class IngestStats:
    articles_seen: int = 0
    article_snapshots: int = 0
    comments_seen: int = 0
    threads_skipped: int = 0
    comment_snapshots: int = 0
    images_new: int = 0
    errors: int = 0


class Pipeline:
    def __init__(self, *, conn, blobs: BlobStore, store, fetcher: Fetcher,
                 origin: str = "live") -> None:
        self.conn = conn            # psycopg connection or None (degraded)
        self.blobs = blobs
        self.store = store          # JsonlStore
        self.fetcher = fetcher
        # How the rows this pipeline writes should be labelled. The daily scan
        # is "live": we asked on a schedule, so absence means the thread was
        # not there. A run walking the publisher's own back-catalogue is
        # "sitemap": the same live site, but read years after the fact, so an
        # empty thread may be an empty thread or a pruned one, and these
        # articles must not join the rescan work-list.
        self.origin = origin

    async def ingest_journal(self, source: Source, *, since_days: int = 10,
                             on_progress=None) -> IngestStats:
        """Scan a journal: discover new articles from the homepage AND re-scan
        recently-seen articles (so comment/vote evolution keeps being captured
        until an article disappears). `on_progress(done, total)` is called after
        each article so the GUI can show a progress bar."""
        stats = IngestStats()
        self._register_journal(source)
        jid = ids.journal_id(source.slug)

        try:
            discovered = await source.discover(self.fetcher)
        except NotImplementedError:
            log.warning("[%s] adapter not implemented yet; skipping", source.slug)
            return stats
        except Exception as exc:
            log.error("[%s] discover failed: %s", source.slug, exc)
            stats.errors += 1
            return stats

        active: list[str] = []
        if self.conn is not None:
            try:
                active = db.active_article_urls(self.conn, jid, since_days=since_days)
            except Exception as exc:
                log.warning("[%s] active-article lookup failed: %s", source.slug, exc)

        # Merge, canonical-deduped: freshly discovered first, then rescans.
        seen: set[str] = set()
        worklist: list[tuple[str, bool]] = []
        for url in discovered:
            c = ids.canonical_url(url)
            if c not in seen:
                seen.add(c)
                worklist.append((url, False))  # discovery
        for url in active:
            c = ids.canonical_url(url)
            if c not in seen:
                seen.add(c)
                worklist.append((url, True))   # rescan

        total = len(worklist)
        log.info("[%s] scan work-list: %d (%d new-discovery, %d rescan)",
                 source.slug, total, len(discovered), total - len(discovered))
        if on_progress:
            on_progress(0, total)
        for i, (url, is_rescan) in enumerate(worklist):
            try:
                await self._ingest_article(source, url, stats, is_rescan=is_rescan)
            except FetchError as exc:
                log.warning("[%s] fetch error for %s: %s", source.slug, url, exc)
                stats.errors += 1
            except Exception as exc:
                log.exception("[%s] unexpected error for %s: %s", source.slug, url, exc)
                stats.errors += 1
            if on_progress:
                on_progress(i + 1, total)
        return stats

    # ------------------------------------------------------------------ #

    def _register_journal(self, source: Source) -> None:
        jid = ids.journal_id(source.slug)
        if self.conn is not None:
            db.upsert_journal(
                self.conn, jid=jid, slug=source.slug, name=source.name,
                base_url=source.base_url, comment_system=source.comment_system,
                community=source.community_key,
            )
        else:
            self.store.append("journal", {
                "id": jid, "slug": source.slug, "name": source.name,
                "base_url": source.base_url, "comment_system": source.comment_system,
                "community": source.community_key,
            })

    async def _ingest_article(self, source: Source, url: str, stats: IngestStats,
                              *, is_rescan: bool = False) -> None:
        article = await source.fetch_article(self.fetcher, url)
        if article is None:
            # A rescanned article that no longer parses is treated as gone.
            if is_rescan and self.conn is not None:
                db.mark_article_gone(self.conn, ids.article_id(source.slug, url))
            return
        stats.articles_seen += 1

        aid = ids.article_id(source.slug, article.url)
        jid = ids.journal_id(source.slug)
        canon = ids.canonical_url(article.url)
        chash = ids.content_hash(
            article.headline or "", article.subhead or "",
            article.body_text or article.body_html or "",
            article.author or "",
        )

        snap_id = self._write_article(jid, aid, canon, article, chash, stats)
        if snap_id is not None:
            await self._write_images(snap_id, article, stats)

        if is_rescan and self._thread_unchanged(aid, article):
            stats.threads_skipped += 1
            return

        comments = await self._safe_comments(source, article)
        for c in comments:
            self._write_comment(source, aid, c, stats)
        # Recorded only after the thread was actually read, so a failed fetch
        # leaves the marker at its old value and the next scan tries again.
        if article.comment_count is not None and self.conn is not None:
            db.record_thread_count(self.conn, aid, article.comment_count)

    def _thread_unchanged(self, aid: str, article: ParsedArticle) -> bool:
        """True when the article page shows the same comment count as the last
        time we actually read the thread.

        Fetching comments is the expensive half of a rescan — a separate API
        call plus a page of pagination for every fifty comments — and on a
        rescan it usually returns exactly what we already hold. Titles that
        print the count on the article page let us find that out for the price
        of the page we fetched anyway.

        The comparison is against the count recorded at our last fetch, not
        against how many comment rows we hold. Those two disagree on nearly
        half the articles in this corpus — a moderated comment leaves our row
        behind, and the page does not count replies the way we store them — so
        our own row count is not a usable proxy. Comparing the site's number to
        the site's own previous number is sound whatever it happens to count.

        Anything unknown means fetching: a title that prints no count, an
        article we have never read the thread of, or a count that moved in
        either direction.
        """
        if article.comment_count is None or self.conn is None:
            return False
        return article.comment_count == db.last_thread_count(self.conn, aid)

    def _write_article(self, jid, aid, canon, article: ParsedArticle, chash, stats) -> int | None:
        fields = {
            "published_at": article.published_at, "updated_at": article.updated_at,
            "headline": article.headline, "subhead": article.subhead,
            "author": article.author, "source": article.source,
            "section": article.section, "lang": article.lang,
            "body_text": article.body_text, "body_html": article.body_html,
            "comment_count": article.comment_count, "raw_meta": article.raw_meta,
        }
        if self.conn is not None:
            db.upsert_article(self.conn, aid=aid, journal_id=jid,
                              canonical_url=canon, source_key=article.source_key,
                              origin=self.origin)
            snap_id = db.insert_article_snapshot(
                self.conn, article_id=aid, content_hash=chash, fields=fields)
            if snap_id is not None:
                stats.article_snapshots += 1
            return snap_id
        # degraded: append and treat as "new" so images are still captured
        self.store.append("article_snapshot",
                          {"article_id": aid, "content_hash": chash, "canonical_url": canon, **fields})
        stats.article_snapshots += 1
        return None

    async def _write_images(self, snap_id: int, article: ParsedArticle, stats) -> None:
        if self.conn is None:
            return  # image linkage needs the snapshot id; handled on JSONL replay
        for img in article.images:
            try:
                resp = await self.fetcher.get(img.orig_url)
            except FetchError as exc:
                log.debug("image fetch failed %s: %s", img.orig_url, exc)
                continue
            if resp.status != 200 or not resp.body:
                continue
            stored = self.blobs.store(resp.body, content_type=resp.content_type)
            if stored.is_new:
                stats.images_new += 1
            db.upsert_image(self.conn, sha256=stored.sha256, byte_size=stored.byte_size,
                            mime=stored.mime, width=stored.width, height=stored.height,
                            storage_path=stored.storage_path)
            db.link_article_image(self.conn, snapshot_id=snap_id, sha256=stored.sha256,
                                  role=img.role, position=img.position, orig_url=img.orig_url,
                                  alt_text=img.alt_text, caption=img.caption)

    async def _safe_comments(self, source: Source, article: ParsedArticle) -> list[ParsedComment]:
        try:
            return await source.fetch_comments(self.fetcher, article)
        except NotImplementedError:
            return []
        except Exception as exc:
            log.warning("[%s] comment fetch failed: %s", source.slug, exc)
            return []

    def _write_comment(self, source: Source, aid: str, c: ParsedComment, stats) -> None:
        stats.comments_seen += 1
        # Where two titles share one comment backend the comment is one thing
        # seen twice, so its id must not depend on which title's article row we
        # reached it through — otherwise every shared commenter's volume
        # doubles. `comment.article_id` then names the title we saw it on
        # first, and the upsert leaves it alone afterwards.
        community = source.community_key
        shared = source.comment_ids_global

        def ident(key: str) -> str:
            return (ids.shared_comment_id(community, key) if shared
                    else ids.comment_id(source.slug, aid, key))

        if c.source_key:
            cid = ident(c.source_key)
        else:
            cid = ids.synthetic_comment_id(
                aid, c.author_nick or "", str(c.posted_at or ""), c.body_text or "")
        parent = ident(c.parent_source_key) if c.parent_source_key else None
        # Include the reaction distribution so evolving votes (which keep changing
        # even after commenting is disabled) always produce a fresh snapshot — the
        # latest snapshot is then the final vote distribution.
        reactions = (c.raw_meta or {}).get("reactions") or {}
        reactions_sig = ";".join(f"{k}={reactions[k]}" for k in sorted(reactions))
        chash = ids.content_hash(c.body_text or c.body_html or "",
                                 str(c.like_count or ""), str(c.reply_count or ""),
                                 reactions_sig)
        fields = {
            "posted_at": c.posted_at, "body_text": c.body_text, "body_html": c.body_html,
            "like_count": c.like_count, "reply_count": c.reply_count, "raw_meta": c.raw_meta,
        }
        if self.conn is not None:
            db.upsert_comment(self.conn, cid=cid, article_id=aid, source_key=c.source_key,
                              parent_id=parent, author_nick=c.author_nick, author_key=c.author_key)
            if db.insert_comment_snapshot(self.conn, comment_id=cid, content_hash=chash, fields=fields):
                stats.comment_snapshots += 1
        else:
            self.store.append("comment_snapshot",
                              {"comment_id": cid, "article_id": aid, "content_hash": chash,
                               "author_nick": c.author_nick, **fields})
            stats.comment_snapshots += 1
