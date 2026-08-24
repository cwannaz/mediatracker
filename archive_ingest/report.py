#!/usr/bin/env python3
"""Summarize what is in the MediaTracker database after the archive ingest.

Reports coverage by origin (live crawl vs pdf archive), the time span now held,
the most prolific pseudonyms, and how many nicknames appear in BOTH the archive
and the live crawl — the starting point for the cross-journal / cross-era
profiling phase.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/home/cwannaz/mediatracker/mediatracker")

from mediatracker import db                     # noqa: E402
from mediatracker.config import load_config     # noqa: E402

Q = {
    "articles by origin":
        "SELECT origin, count(*) FROM article GROUP BY origin ORDER BY 2 DESC",
    "comments by article origin":
        """SELECT a.origin, count(*) FROM comment c
           JOIN article a ON a.id = c.article_id GROUP BY a.origin ORDER BY 2 DESC""",
    "archive time span":
        """SELECT min(published_at)::date, max(published_at)::date
           FROM article_snapshot s JOIN article a ON a.id = s.article_id
           WHERE a.origin = 'pdf' AND published_at IS NOT NULL""",
    "comment time span":
        "SELECT min(posted_at)::date, max(posted_at)::date FROM comment_snapshot",
}


def main() -> int:
    conn = db.connect(load_config())
    if conn is None:
        print("no database connection")
        return 1
    with conn.cursor() as cur:
        for label, sql in Q.items():
            cur.execute(sql)
            print(f"\n== {label}")
            for row in cur.fetchall():
                print("   ", *row)

        cur.execute("SELECT count(DISTINCT author_nick) FROM comment WHERE author_nick IS NOT NULL")
        print("\n== distinct pseudonyms:", cur.fetchone()[0])

        cur.execute("""
            SELECT author_nick, count(*) AS n,
                   min(cs.posted_at)::date AS first, max(cs.posted_at)::date AS last
            FROM comment c JOIN comment_snapshot cs ON cs.comment_id = c.id
            WHERE author_nick IS NOT NULL
            GROUP BY author_nick ORDER BY n DESC LIMIT 15
        """)
        print("\n== most prolific pseudonyms (comment, first seen, last seen)")
        for nick, n, first, last in cur.fetchall():
            print(f"    {nick:<28} {n:>5}   {first} → {last}")

        # Nicknames present in both the archive and the live crawl: these give the
        # longest behavioural histories for profiling.
        cur.execute("""
            SELECT count(*) FROM (
              SELECT c.author_nick FROM comment c JOIN article a ON a.id = c.article_id
              WHERE c.author_nick IS NOT NULL GROUP BY c.author_nick
              HAVING count(DISTINCT a.origin) > 1
            ) t
        """)
        print("\n== pseudonyms seen in BOTH archive and live crawl:", cur.fetchone()[0])

        cur.execute("""
            SELECT c.author_nick, count(*) FROM comment c
            JOIN article a ON a.id = c.article_id
            WHERE c.author_nick IS NOT NULL
            GROUP BY c.author_nick HAVING count(DISTINCT a.origin) > 1
            ORDER BY 2 DESC LIMIT 10
        """)
        for nick, n in cur.fetchall():
            print(f"    {nick:<28} {n:>5}")

        cur.execute("""
            SELECT source, count(*) FROM article_snapshot
            WHERE source IS NOT NULL GROUP BY source ORDER BY 2 DESC LIMIT 10
        """)
        rows = cur.fetchall()
        if rows:
            print("\n== news agencies / sources")
            for src, n in rows:
                print(f"    {src:<28} {n:>5}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
