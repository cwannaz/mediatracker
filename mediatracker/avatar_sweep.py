"""Re-reading threads to recover the avatar history the corpus never kept.

An uploaded avatar is the one near-identifier this comment platform exposes: it
belongs to the account, not to the nickname, so it survives a rename. The API
serves the account's CURRENT picture beside each comment's nickname frozen as
it was written -- so re-reading an old thread returns the old nickname wearing
the new picture, and a rename becomes readable years after the fact.

None of it is in anything we stored, so recovering it means asking the site
again. Two measurements decided the shape of that:

  * **How far back it is possible at all.** The comment API answers only for
    modern content ids. Tested one thread per era: 2008, 2010, 2012, 2014 and
    2016 all return HTTP 500, while 2021, 2023 and 2026 return full threads
    with an avatar on every comment. The floor is 9 November 2020 -- the oldest
    comment on a thread whose id the API still knows. Le Matin has 149,602
    nicknames and 90,331 of them are reachable; the other 59,271 wrote only on
    threads the site no longer serves, and no amount of crawling recovers them.

  * **How much of it is necessary.** What is wanted is nicknames, not threads.
    A nickname's picture is the same on every thread it appears on, so the
    queue is ordered by how many nicknames each thread would be the FIRST to
    show us. Greedy, measured on the real corpus: 50% of nicknames in 356
    threads, 90% in 3,633, and all 90,331 in 10,190 -- against 47,463 threads
    if they were simply walked in date order. Twenty-eight hours instead of
    eighty, for the same answer.

The thread's comments are written as well as its avatars. The comment pages are
fetched either way, so throwing their contents away would be the waste; a
thread last read in 2022 usually has votes that have moved.
"""
from __future__ import annotations

import collections
import heapq
import logging
import time

from . import db, fetch, ids, sources
from .pipeline import Pipeline

log = logging.getLogger(__name__)

# The API answers for these and 500s for everything older; see the module
# docstring. Nine digits is the shortest modern id observed (2026 ids are
# 9-digit, 2021-2023 ids are 12).
MODERN_ID = r"^[0-9]{9,}$"


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #

def plan(conn, *, journal: str = "lematin", rebuild: bool = False) -> dict:
    """Order every reachable thread by how many nicknames it would be first to show.

    Set cover, greedily, with a lazy heap: a thread's stored yield goes stale as
    other threads cover its authors, so it is rechecked when popped and pushed
    back if it has fallen. Exact greedy, one pass over the pairs, no re-scan.
    """
    with conn.cursor() as cur:
        if rebuild:
            cur.execute("DELETE FROM avatar_sweep WHERE journal = %s", (journal,))
        cur.execute("SELECT count(*) FROM avatar_sweep WHERE journal = %s",
                    (journal,))
        if cur.fetchone()[0]:
            return {"journal": journal, "planned": 0,
                    "note": "a plan already exists; pass rebuild to replace it"}

        cur.execute(f"""
            SELECT DISTINCT a.id, c.author_nick
            FROM article a JOIN journal j ON j.id = a.journal_id
            JOIN comment c ON c.article_id = a.id
            WHERE j.slug = %s AND a.gone_at IS NULL
              AND a.source_key ~ '{MODERN_ID}'
              AND c.author_nick IS NOT NULL
        """, (journal,))
        pairs = cur.fetchall()

    threads: dict[str, set] = collections.defaultdict(set)
    for aid, nick in pairs:
        threads[aid].add(nick)
    if not threads:
        return {"journal": journal, "planned": 0, "note": "nothing reachable"}

    heap = [(-len(s), aid) for aid, s in threads.items()]
    heapq.heapify(heap)
    covered: set = set()
    order: list[tuple[str, int]] = []
    while heap:
        neg, aid = heapq.heappop(heap)
        gain = len(threads[aid] - covered)
        if gain == 0:
            continue              # every author here already seen elsewhere
        if gain < -neg:
            heapq.heappush(heap, (-gain, aid))   # stale estimate, re-queue
            continue
        covered |= threads[aid]
        order.append((aid, gain))

    with conn.cursor() as cur:
        for rank, (aid, gain) in enumerate(order, 1):
            cur.execute("""
                INSERT INTO avatar_sweep (article_id, journal, rank, new_nicks)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (article_id) DO NOTHING
            """, (aid, journal, rank, gain))
    return {"journal": journal, "planned": len(order),
            "threads_available": len(threads), "nicknames": len(covered)}


# --------------------------------------------------------------------------- #
# Working through it
# --------------------------------------------------------------------------- #

async def run(conn, *, blobs, store, fetcher, journal: str = "lematin",
              limit: int | None = None, max_hours: float | None = None,
              progress_every: int = 25) -> dict:
    """Re-read queued threads until the queue, the budget or a hold ends it."""
    fetch.check_not_paused(f"the avatar sweep of {journal}")
    src = sources.get(journal)
    if src is None:
        raise ValueError(f"unknown journal {journal!r}")
    source = src()

    def _queue():
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.article_id, a.source_key, a.canonical_url, s.rank,
                       s.new_nicks
                FROM avatar_sweep s JOIN article a ON a.id = s.article_id
                WHERE s.journal = %s AND s.status IS NULL
                ORDER BY s.rank
                LIMIT %s
            """, (journal, limit if limit is not None else 10 ** 9))
            return db._rows(cur)

    # This unit starts at boot beside the daemon, whose ensure_schema takes the
    # same tables in the opposite order; the reader is always the one Postgres
    # kills. Same fix as the body backfill's.
    todo = db.retrying_on_lock(_queue, what="the avatar sweep queue")

    out = {"journal": journal, "queued": len(todo), "threads": 0,
           "comments": 0, "snapshots": 0, "avatars_new": 0, "failed": 0,
           "stopped": None}
    if not todo:
        return out

    pipeline = Pipeline(conn=conn, blobs=blobs, store=store, fetcher=fetcher)
    deadline = time.time() + max_hours * 3600 if max_hours else None
    log.info("[%s] avatar sweep: %d threads queued", journal, len(todo))

    for row in todo:
        if deadline and time.time() > deadline:
            out["stopped"] = "budget"
            log.info("[%s] budget reached; %d done, the rest stay queued",
                     journal, out["threads"])
            break
        try:
            st = await pipeline.refresh_thread(
                source, row["article_id"], row["source_key"],
                row["canonical_url"])
        except fetch.Paused:
            out["stopped"] = "hold"
            log.warning("[%s] a hold was placed mid-sweep; stopping", journal)
            break
        except Exception as exc:
            # Marked, not retried: a thread that fails is one thread's worth of
            # nicknames, and a sweep that stops on each one never finishes.
            out["failed"] += 1
            _finish(conn, row["article_id"], f"failed:{type(exc).__name__}", None)
            log.debug("thread %s failed: %s", row["article_id"], exc)
            continue

        out["threads"] += 1
        out["comments"] += st.comments_seen
        out["snapshots"] += st.comment_snapshots
        out["avatars_new"] += st.avatars_new
        _finish(conn, row["article_id"], "done", st.comments_seen)
        if out["threads"] % progress_every == 0:
            log.info("[%s] %d/%d threads, %d new avatars, %d comment snapshots",
                     journal, out["threads"], len(todo), out["avatars_new"],
                     out["snapshots"])
    return out


def _finish(conn, article_id: str, status: str, seen: int | None) -> None:
    with conn.cursor() as cur:
        cur.execute("""UPDATE avatar_sweep
                       SET status = %s, nicks_seen = %s, done_at = now()
                       WHERE article_id = %s""", (status, seen, article_id))


def progress(conn, *, journal: str = "lematin") -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT count(*) AS planned,
                   count(*) FILTER (WHERE status = 'done') AS done,
                   -- The percent is doubled because this statement carries
                   -- a parameter, and psycopg reads a single one as the start
                   -- of a placeholder -- in a comment just as much as in SQL,
                   -- which is how the first attempt at this line failed.
                   count(*) FILTER (WHERE status LIKE 'failed%%') AS failed,
                   count(*) FILTER (WHERE status IS NULL) AS todo,
                   sum(new_nicks) FILTER (WHERE status IS NOT NULL) AS nicks_reached,
                   sum(new_nicks) AS nicks_planned
            FROM avatar_sweep WHERE journal = %s
        """, (journal,))
        return db._rows(cur)[0]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    import argparse
    import asyncio
    import json

    from .config import load_config
    from .fetch import Fetcher
    from .images import BlobStore
    from .store import JsonlStore

    p = argparse.ArgumentParser(prog="mediatracker.avatar_sweep")
    p.add_argument("--journal", default="lematin")
    p.add_argument("--plan", action="store_true", help="build the queue and stop")
    p.add_argument("--rebuild", action="store_true", help="replace an existing plan")
    p.add_argument("--status", action="store_true", help="how far it has got")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--max-hours", type=float, default=None)
    a = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    cfg = load_config()
    conn = db.connect(cfg)
    if conn is None:
        print("Postgres unavailable")
        return 1

    if a.status:
        print(json.dumps(progress(conn, journal=a.journal), indent=1, default=str))
        return 0
    if a.plan or a.rebuild:
        print(json.dumps(plan(conn, journal=a.journal, rebuild=a.rebuild), indent=1))
        return 0

    # Exit 0 on a deliberate hold: this also runs as a unit, and a hold must not
    # leave a failed service behind. The printed line says nothing was fetched.
    reason = fetch.hold_reason(f"the avatar sweep of {a.journal}")
    if reason:
        print(reason)
        return 0

    if not progress(conn, journal=a.journal)["planned"]:
        print(json.dumps(plan(conn, journal=a.journal), indent=1))

    out = asyncio.run(run(conn, blobs=BlobStore(cfg.blob_path),
                          store=JsonlStore(cfg.jsonl_path),
                          fetcher=Fetcher(cfg), journal=a.journal,
                          limit=a.limit, max_hours=a.max_hours))
    print(json.dumps(out, indent=1))
    print(json.dumps(progress(conn, journal=a.journal), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
