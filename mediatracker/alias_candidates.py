"""Find nicknames that are plausibly the same person, by name proximity.

This is NAME similarity only. It proposes candidates for a human to confirm and
never asserts identity by itself; the GUI's Aggregation tab is where a person
reviews a group, drops the members that do not belong, and confirms the rest.

Tiers:
  exact   — identical once accents, case and punctuation are folded away
            ("oscar_the_grouch" == "oscar-the-grouch" == "oscarthegrouch",
             "Séraphin Lampion" == "seraphin_lampion")
  nested  — one folded handle contains the other, both substantial
            ("Fluide-Glacial" inside "fluideglaciale")
  near    — small edit distance (typo / digit change). Reported as loose PAIRS,
            never merged into the strong groups: chaining weak links transitively
            drags unrelated people together (Alex1984 -> Alex1973 -> …).

Evidence carried per group/pair:
  overlap_days    — do the active periods overlap? A rename usually shows none.
  shared_articles — articles both names commented on. Overlap here argues against
                    a simple rename (though people do reply to themselves).

Everything runs in Python over one query, so it is fast enough to serve the GUI.
"""
from __future__ import annotations

import argparse
import sys
import unicodedata

from . import db
from .config import load_config

MIN_KEY_LEN = 5      # shorter handles produce meaningless matches ("bob" ~ "rob")
MAX_LEV = 2


def fold(nick: str) -> str:
    """Accent-, case- and punctuation-insensitive form of a nickname."""
    s = unicodedata.normalize("NFKD", nick or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.lower() if c.isalnum())


def _lev(a: str, b: str, cutoff: int = MAX_LEV) -> int:
    """Levenshtein with an early exit once the best row exceeds `cutoff`."""
    if abs(len(a) - len(b)) > cutoff:
        return cutoff + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cutoff:
            return cutoff + 1
        prev = cur
    return prev[-1]


def load_nicks(conn, min_comments: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.author_nick AS nick,
                   count(DISTINCT c.id) AS comments,
                   min(cs.posted_at) AS first_seen,
                   max(cs.posted_at) AS last_seen,
                   array_agg(DISTINCT c.article_id) AS articles
            FROM comment c
            JOIN comment_snapshot cs ON cs.comment_id = c.id
            WHERE c.author_nick IS NOT NULL
            GROUP BY 1
            HAVING count(DISTINCT c.id) >= %s
        """, (min_comments,))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["key"] = fold(r["nick"])
    return [r for r in rows if len(r["key"]) >= MIN_KEY_LEN]


def _evidence(a: dict, b: dict) -> dict:
    shared = len(set(a["articles"]) & set(b["articles"]))
    lo = max(a["first_seen"], b["first_seen"])
    hi = min(a["last_seen"], b["last_seen"])
    overlap = max(0, int((hi - lo).total_seconds() // 86400)) if lo and hi else 0
    return {"overlap_days": overlap, "shared_articles": shared}


def find_groups(conn, min_comments: int = 2, linked: set[str] | None = None):
    """Return (strong_groups, weak_pairs). `linked` nicknames are marked, not hidden."""
    linked = linked or set()
    nicks = load_nicks(conn, min_comments)
    by_key: dict[str, list[dict]] = {}
    for n in nicks:
        by_key.setdefault(n["key"], []).append(n)

    parent = {k: k for k in by_key}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    keys = sorted(by_key)
    weak_pairs = []
    # Block by first character: a substring/edit-distance match almost always
    # shares it, and it turns an O(n^2) sweep into something instant.
    for i, ka in enumerate(keys):
        for kb in keys[i + 1:]:
            if ka[0] != kb[0] and abs(len(ka) - len(kb)) > MAX_LEV:
                continue
            if (ka in kb or kb in ka):
                ra, rb = find(ka), find(kb)
                if ra != rb:
                    parent[rb] = ra
            elif _lev(ka, kb) <= MAX_LEV:
                weak_pairs.append((ka, kb))

    clusters: dict[str, list[dict]] = {}
    for key, members in by_key.items():
        clusters.setdefault(find(key), []).extend(members)

    strong = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: -m["comments"])
        rel = "exact" if len({m["key"] for m in members}) == 1 else "nested"
        g = {
            "label": members[0]["nick"],
            "relation": rel,
            "total_comments": sum(m["comments"] for m in members),
            "already_linked": sum(1 for m in members if m["nick"] in linked),
            "members": [{k: m[k] for k in ("nick", "comments", "first_seen", "last_seen")}
                        for m in members],
            **_evidence(members[0], members[1]),
        }
        strong.append(g)
    strong.sort(key=lambda g: -g["total_comments"])

    weak = []
    for ka, kb in weak_pairs:
        if find(ka) == find(kb):
            continue
        a, b = by_key[ka][0], by_key[kb][0]
        weak.append({
            "a": {k: a[k] for k in ("nick", "comments", "first_seen", "last_seen")},
            "b": {k: b[k] for k in ("nick", "comments", "first_seen", "last_seen")},
            "relation": "near", **_evidence(a, b),
        })
    weak.sort(key=lambda w: -(w["a"]["comments"] + w["b"]["comments"]))
    return strong, weak


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mediatracker.alias_candidates")
    ap.add_argument("--min-comments", type=int, default=2)
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args(argv)

    conn = db.connect(load_config())
    if conn is None:
        print("no database connection", file=sys.stderr)
        return 1

    strong, weak = find_groups(conn, args.min_comments)
    print(f"STRONG: {len(strong)} groups covering "
          f"{sum(len(g['members']) for g in strong)} nicknames\n")
    for g in strong[:args.limit]:
        print(f"[{g['relation']}] {g['total_comments']} comments / {len(g['members'])} nicknames"
              f"  (overlap {g['overlap_days']}d, {g['shared_articles']} shared articles)")
        for m in g["members"]:
            print(f"    {m['nick']:<30} {m['comments']:>4}  "
                  f"{str(m['first_seen'])[:10]} -> {str(m['last_seen'])[:10]}")
        print()
    print(f"\nWEAK: {len(weak)} near-miss pairs (edit distance <= {MAX_LEV})\n")
    for w in weak[:args.limit]:
        a, b = w["a"], w["b"]
        print(f"    {a['nick']:<24}{a['comments']:>4}  ~  {b['nick']:<24}{b['comments']:>4}"
              f"   overlap={w['overlap_days']}d shared={w['shared_articles']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
