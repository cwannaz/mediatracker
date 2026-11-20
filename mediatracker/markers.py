"""Out-group names, and what calling people something says about the caller.

Character 4-grams in `lexicon` catch how someone writes. This catches
something different and coarser: which word a writer reaches for to name the
people they disagree with. Cedric's observation, which started this: someone
who answers every opponent with "les fachos" is placing themselves, and so is
someone who answers with "les gauchos" -- while "les gauchistes", the same
referent with the derogation turned down, is a different speaker again.

Two uses, and they have opposite requirements:

  * **stance** -- which way the invective is aimed. Needs COMMON markers, so
    that most writers produce a reading at all.
  * **identity** -- which rare word this particular writer habitually uses.
    Needs RARE markers, because a word 3 people in the corpus use narrows a
    field of 183,000 nicknames to 3.

Both are measured here rather than asserted. The first thing this module
learned on the live corpus is that the obvious version of the stance idea is
wrong: writers who use `fachos` are 3.06x MORE likely than chance to also use
`gauchos`, not less. Presence of an epithet measures how combative someone is,
not which side they are on. What does carry signal is the *asymmetry* -- the
ratio of right-aimed to left-aimed uses -- and even that was 5:1 in the
predicted direction on a small labelled subset, not a clean separation.

So nothing here returns a verdict. `aim()` returns a number and the counts it
came from, and a caller that prints it without the counts is misusing it.

`mistakes.py` paid for the rule this module follows: a narrow regex
manufactures false rarity, and a marker's base rate must be MEASURED on this
corpus before anyone calls it a fingerprint. `measure()` writes those rates to
`marker_base_rate` and `rarity()` refuses to answer from anything else.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# The lexicon
# --------------------------------------------------------------------------- #
# `aim` is who the word is thrown AT, never who throws it -- that is the thing
# to be inferred, and building it into the data would be assuming the answer.
#
# `heat` separates the two registers Cedric pointed at: a `slur` is a word
# chosen to insult ("gauchos", "fachos"), a `label` names the same people
# without the sneer ("la gauche", "l'extrême droite"). A writer who only ever
# uses labels is not placed by this lexicon at all, which is correct.


@dataclass(frozen=True)
class Marker:
    name: str
    aim: str          # 'left' | 'right' | 'other' -- the target, not the speaker
    heat: str         # 'slur' | 'label'
    forms: tuple[str, ...]   # regex alternatives, matched whole-word, no case
    note: str = ""


MARKERS: tuple[Marker, ...] = (
    # ---- aimed at the right ------------------------------------------------
    Marker("fachos", "right", "slur", ("fachos?", "fachistes?"),
           "the one that started this; 1,057 writers"),
    Marker("faf", "right", "slur", ("fafs?",),
           "52 writers corpus-wide: rare enough to narrow a field"),
    Marker("fascistes", "right", "slur", ("fascistes?", "fasciste"),
           "milder and far commoner than 'fachos'"),
    Marker("nazis", "right", "slur", ("nazis?", "nazillons?", "néo-?nazis?")),
    Marker("bruns", "right", "slur", ("bruns?", "chemises brunes")),
    Marker("reacs", "right", "slur", ("réacs?", "reacs?")),
    Marker("beaufs", "right", "slur", ("beaufs?",)),
    Marker("extreme-droite", "right", "label",
           ("extr[êe]me[- ]droite", "extr[êe]me droite")),
    Marker("la-droite", "right", "label", ("la droite",)),

    # ---- aimed at the left -------------------------------------------------
    Marker("gauchos", "left", "slur", ("gauchos?",),
           "1,598 writers; the right's counterpart to 'fachos'"),
    Marker("gauchistes", "left", "slur", ("gauchistes?",),
           "3,372 writers -- commonest of all, and the least derogatory"),
    Marker("gauchiasse", "left", "slur", ("gauchiasses?",),
           "3 writers corpus-wide: close to a signature on its own"),
    Marker("bobos", "left", "slur", ("bobos?", "bobo[- ]gauchistes?")),
    Marker("socialos", "left", "slur", ("socialos?",)),
    Marker("khmers-verts", "left", "slur", ("khmers? verts?",)),
    Marker("ecolos", "left", "slur", ("écolos?", "ecolos?")),
    Marker("islamo-gauchistes", "left", "slur",
           ("islamo[- ]gauchistes?", "islamogauchistes?")),
    Marker("woke", "left", "slur", ("wokes?", "wokistes?", "wokisme")),
    Marker("bien-pensants", "left", "slur",
           ("bien[- ]pensants?", "bienpensance", "bien[- ]pensance")),
    Marker("droits-de-lhommistes", "left", "slur",
           ("droits?[- ]de[- ]l'?hommistes?",)),
    Marker("assistes", "left", "slur", ("assistés?",)),
    Marker("la-gauche", "left", "label", ("la gauche",)),

    # ---- aimed at neither: register markers, kept for contrast -------------
    Marker("droitards", "other", "slur", ("droitards?",),
           "152 writers; aimed at the right but from no settled quarter"),
    Marker("moutons", "other", "slur", ("moutons?",),
           "whoever the writer thinks is credulous; deliberately unaimed"),
    Marker("complotistes", "other", "slur", ("complotistes?", "conspis?")),
)

BY_NAME = {m.name: m for m in MARKERS}


def _alt(m: Marker) -> str:
    return "|".join(m.forms)


def pattern(m: Marker) -> re.Pattern:
    """Whole-word, case-insensitive, accent as written."""
    return re.compile(rf"\b(?:{_alt(m)})\b", re.I)


def sql_pattern(m: Marker) -> str:
    r"""The same thing for Postgres, whose word boundary is \m ... \M."""
    return rf"\m(?:{_alt(m)})\M"


_COMPILED = {m.name: pattern(m) for m in MARKERS}


# --------------------------------------------------------------------------- #
# Reading one writer
# --------------------------------------------------------------------------- #

def scan(text: str | None) -> dict[str, int]:
    """Which markers occur in one piece of text, and how often."""
    if not text:
        return {}
    out = {}
    for name, rx in _COMPILED.items():
        n = len(rx.findall(text))
        if n:
            out[name] = n
    return out


def aim(counts: dict[str, int]) -> dict:
    """How lopsidedly a writer's invective points, with the counts behind it.

    The index runs -1 (every slur aimed at the right) to +1 (every slur aimed
    at the left). Only slurs count: someone who writes "la gauche" has named a
    side, not insulted one, and reading that as hostility is the error this
    separation exists to prevent.

    The counts travel with the index because the index alone is a lie at small
    n -- one use of one word gives +-1.0, which looks like certainty and is a
    coin toss. Callers must look at `slurs`.
    """
    left = sum(n for k, n in counts.items()
               if BY_NAME[k].aim == "left" and BY_NAME[k].heat == "slur")
    right = sum(n for k, n in counts.items()
                if BY_NAME[k].aim == "right" and BY_NAME[k].heat == "slur")
    total = left + right
    return {
        "aimed_left": left,
        "aimed_right": right,
        "slurs": total,
        # Positive means aimed at the left, so typically a writer on the right.
        # "Typically" is the whole caveat: see this module's docstring.
        "index": None if total == 0 else (left - right) / total,
    }


# --------------------------------------------------------------------------- #
# Base rates: measured, stored, and required
# --------------------------------------------------------------------------- #

def measure(conn) -> list[dict]:
    """Count, in one pass, how many writers and comments use each marker.

    One scan with an aggregate per marker rather than one query per marker:
    the regexes are the cost and the corpus is 4.3 million comments, so paying
    for the scan twenty-five times would be the whole difference between a
    minute and half an hour.

    Writes `marker_base_rate` per title and overall. This is a deliberate act,
    not something a scan does quietly: a rate is only meaningful with a date
    on it, and recomputing it silently would let a 'rare' marker drift common
    without anyone noticing.
    """
    from . import db

    filters = ",\n".join(
        f"       count(*) FILTER (WHERE cs.body_text ~* {_q(sql_pattern(m))}) "
        f"AS c_{i},\n"
        f"       count(DISTINCT c.author_nick) FILTER (WHERE cs.body_text ~* "
        f"{_q(sql_pattern(m))}) AS w_{i}"
        for i, m in enumerate(MARKERS))
    sql = f"""
        SELECT j.slug AS scope,
               count(DISTINCT c.author_nick) AS writers_tot,
{filters}
        FROM comment_snapshot cs
        JOIN comment c ON c.id = cs.comment_id
        JOIN article a ON a.id = c.article_id
        JOIN journal j ON j.id = a.journal_id
        WHERE cs.body_text IS NOT NULL AND c.author_nick IS NOT NULL
        GROUP BY j.slug
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = db._rows(cur)

    out, totals = [], {}
    for row in rows:
        for i, m in enumerate(MARKERS):
            rec = {"marker": m.name, "scope": row["scope"],
                   "comments": row[f"c_{i}"], "writers": row[f"w_{i}"],
                   "writers_tot": row["writers_tot"]}
            out.append(rec)
            t = totals.setdefault(m.name, {"comments": 0, "writers": 0})
            t["comments"] += rec["comments"]
            # Summing distinct writers across titles overcounts anyone who
            # writes on two of them. Said plainly rather than hidden: the
            # 'all' row is an upper bound on writers, exact on comments.
            t["writers"] += rec["writers"]
    tot_all = sum(r["writers_tot"] for r in rows)
    for name, t in totals.items():
        out.append({"marker": name, "scope": "all", "comments": t["comments"],
                    "writers": t["writers"], "writers_tot": tot_all})

    with conn.cursor() as cur:
        for r in out:
            cur.execute("""
                INSERT INTO marker_base_rate
                    (marker, scope, writers, comments, writers_tot)
                VALUES (%(marker)s, %(scope)s, %(writers)s, %(comments)s,
                        %(writers_tot)s)
                ON CONFLICT (marker, scope) DO UPDATE SET
                    writers = EXCLUDED.writers, comments = EXCLUDED.comments,
                    writers_tot = EXCLUDED.writers_tot, measured_at = now()
            """, r)
    return out


def _q(s: str) -> str:
    """A literal for a statement built by hand; the patterns are ours, not input."""
    return "'" + s.replace("'", "''") + "'"


class NotMeasured(RuntimeError):
    """Asked how rare a marker is before anyone measured it on this corpus."""


def rarity(conn, name: str, *, scope: str = "all") -> dict:
    """How many writers use this marker -- from the table, never from a guess.

    Raises rather than estimating. A fingerprint claim rests entirely on this
    number, and a plausible-looking default would be the exact mistake
    `mistakes.py` records.
    """
    with conn.cursor() as cur:
        cur.execute("""SELECT writers, comments, writers_tot, measured_at
                       FROM marker_base_rate WHERE marker=%s AND scope=%s""",
                    (name, scope))
        row = cur.fetchone()
    if row is None:
        raise NotMeasured(
            f"{name!r} has no measured base rate for scope {scope!r}; "
            "run `python3 -m mediatracker.markers measure` first")
    writers, comments, tot, at = row
    return {"marker": name, "scope": scope, "writers": writers,
            "comments": comments, "writers_tot": tot, "measured_at": at,
            "share": (writers / tot) if tot else None}


def writer_markers(conn, nick: str) -> dict[str, int]:
    """Which markers one nickname has used, and in how many comments."""
    from . import db

    filters = ",\n".join(
        f"       count(*) FILTER (WHERE cs.body_text ~* {_q(sql_pattern(m))}) AS c_{i}"
        for i, m in enumerate(MARKERS))
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT
{filters}
            FROM comment_snapshot cs JOIN comment c ON c.id = cs.comment_id
            WHERE c.author_nick = %s AND cs.body_text IS NOT NULL
        """, (nick,))
        row = db._rows(cur)[0]
    return {m.name: row[f"c_{i}"] for i, m in enumerate(MARKERS)
            if row[f"c_{i}"]}


def fingerprint(conn, nick: str, *, scope: str = "all",
                max_writers: int = 500) -> list[dict]:
    """This writer's markers, rarest first, with the base rate behind each.

    `max_writers` is what makes a row worth reading: a marker 3,372 people use
    places nobody. The cut is on the measured count, so raising it is a
    deliberate widening and not an accident.
    """
    out = []
    for name, n in writer_markers(conn, nick).items():
        try:
            r = rarity(conn, name, scope=scope)
        except NotMeasured:
            continue
        if r["writers"] and r["writers"] <= max_writers:
            out.append({**r, "comments_by_writer": n, "aim": BY_NAME[name].aim,
                        "heat": BY_NAME[name].heat})
    return sorted(out, key=lambda r: r["writers"])


def who_uses(conn, name: str, *, journal: str | None = None,
             limit: int = 200) -> list[dict]:
    """Every writer who has used one marker, with when they used it.

    The succession use: for a marker only a handful of people in the corpus
    reach for, this is a candidate list short enough to read by eye -- and the
    dates say which of them could be the same person as which.
    """
    from . import db

    m = BY_NAME[name]
    sql = f"""
        SELECT c.author_nick AS nick, j.slug AS journal, count(*) AS comments,
               min(cs.posted_at) AS first_used, max(cs.posted_at) AS last_used
        FROM comment_snapshot cs
        JOIN comment c ON c.id = cs.comment_id
        JOIN article a ON a.id = c.article_id
        JOIN journal j ON j.id = a.journal_id
        WHERE cs.body_text ~* {_q(sql_pattern(m))}
          AND c.author_nick IS NOT NULL
          {"AND j.slug = %(journal)s" if journal else ""}
        GROUP BY 1, 2 ORDER BY 3 DESC LIMIT %(limit)s
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"journal": journal, "limit": limit})
        return db._rows(cur)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    import argparse

    from . import db
    from .config import load_config

    p = argparse.ArgumentParser(prog="mediatracker.markers")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("measure", help="count every marker on the corpus and store it")
    r = sub.add_parser("rates", help="the stored base rates, rarest first")
    r.add_argument("--scope", default="all")
    f = sub.add_parser("fingerprint", help="one writer's rare markers")
    f.add_argument("nick")
    f.add_argument("--max-writers", type=int, default=500)
    w = sub.add_parser("who", help="everyone who uses one marker")
    w.add_argument("marker")
    w.add_argument("--journal", default=None)
    a = sub.add_parser("aim", help="which way one writer's invective points")
    a.add_argument("nick")

    args = p.parse_args(argv)
    conn = db.connect(load_config())
    if conn is None:
        print("Postgres unavailable")
        return 1

    if args.cmd == "measure":
        rows = measure(conn)
        print(f"measured {len(MARKERS)} markers over "
              f"{len({r['scope'] for r in rows}) - 1} titles")
        for r_ in sorted((x for x in rows if x["scope"] == "all"),
                         key=lambda x: x["writers"]):
            print(f"  {r_['marker']:22s} {r_['writers']:>7,} writers  "
                  f"{r_['comments']:>8,} comments")
    elif args.cmd == "rates":
        with conn.cursor() as cur:
            cur.execute("""SELECT marker, writers, comments, writers_tot, measured_at
                           FROM marker_base_rate WHERE scope=%s
                           ORDER BY writers""", (args.scope,))
            rows = cur.fetchall()
        if not rows:
            print(f"nothing measured for scope {args.scope!r}")
            return 1
        print(f"scope {args.scope}, measured {rows[0][4]:%Y-%m-%d %H:%M}")
        for marker, wr, cm, tot, _ in rows:
            m = BY_NAME.get(marker)
            print(f"  {marker:22s} {wr:>7,} writers ({100*wr/tot:5.2f}%)  "
                  f"{cm:>8,} comments  aim={m.aim if m else '?':5s} "
                  f"{m.heat if m else ''}")
    elif args.cmd == "fingerprint":
        rows = fingerprint(conn, args.nick, max_writers=args.max_writers)
        if not rows:
            print(f"{args.nick!r}: no marker rarer than {args.max_writers} writers")
            return 0
        for r_ in rows:
            print(f"  {r_['marker']:22s} used in {r_['comments_by_writer']:>4} "
                  f"of their comments; {r_['writers']:>6,} writers corpus-wide "
                  f"({100*r_['share']:.3f}%)  aim={r_['aim']}")
    elif args.cmd == "who":
        for r_ in who_uses(conn, args.marker, journal=args.journal):
            print(f"  {r_['nick'][:30]:30s} {r_['journal']:9s} "
                  f"{r_['comments']:>5}  "
                  f"{r_['first_used']:%Y-%m-%d} → {r_['last_used']:%Y-%m-%d}")
    else:
        got = aim(writer_markers(conn, args.nick))
        print(f"  {args.nick}: {got['slurs']} slurs — "
              f"{got['aimed_right']} aimed right, {got['aimed_left']} aimed left")
        print(f"  index: {got['index']}"
              + ("  (too few to read)" if got["slurs"] < 3 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
