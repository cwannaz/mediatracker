"""How a commenter chose to appear.

Signing a comment is a social act before it is an identifier. Somebody who
writes under what reads as their own name has accepted that a neighbour might
recognise them; somebody who writes as a 1970s cartoon bear has not; somebody
who writes as `dcrc60` has arranged to be unrecognisable even to themselves a
year later. Those are three different relationships to a public, and the
difference is legible from the string alone.

**This measures the SHAPE of a handle, never its truth.** `personal-name` means
the string is built like a personal name. It does not mean the name is real, and
it certainly does not mean it is the writer's — this corpus is full of handles
built exactly like a name that are nothing of the kind:

    Alex Tincteur      an extinguisher
    Paul Ochon         a bolster
    Jean Eymar         I have had enough
    Anna Coluthe       a figure of speech
    Nom Prénom         the labels of the form field itself
    Yitzhak Rabin      a dead prime minister

The distinction that cannot be made mechanically is exactly the one between
those and `Vincent Zellweger`. A string cannot say whether a name is borrowed,
invented or owned, so this module does not try: it reports the shape, and
`nicknames.read` unmasks the ones a reader has recognised by hand. What is left
over is genuinely unknown, and saying so is the point — the study has no
interest in resolving it, only in how many people chose that shape at all.
"""
from __future__ import annotations

import re
from collections import Counter

# Three ways of standing in front of a public.
FORMS = ("personal-name", "alias", "opaque")

FORM_MEANING = {
    "personal-name": "built like a personal name — whether or not it is one",
    "alias": "a readable word, phrase or persona",
    "opaque": "letters and digits that name nothing",
}

# Accented letters are ordinary letters here; the corpus is French.
_LETTER = "A-Za-zÀ-ÖØ-öø-ÿ"
_WORD = re.compile(rf"^[{_LETTER}'’]+(?:-[{_LETTER}'’]+)*$")
_VOWEL = re.compile(r"[aeiouyàâäéèêëîïôöùûüAEIOUYÀÂÄÉÈÊËÎÏÔÖÙÛÜ]")
_LETTERS_ONLY = re.compile(rf"[^{_LETTER}]")
# "P-A Gallay", "J-F", "JC" — an initialled forename is still a forename.
_INITIALS = re.compile(r"^[A-ZÀ-Ö](?:[-.]?[A-ZÀ-Ö])*$")
_SPLIT = re.compile(r"[\s_.]+")

# Below this, nothing in the string can be read aloud as a word or a name.
# Three is deliberate: it keeps `luc45` and `Ali14` — a forename with a number
# bolted on, which is a way of appearing, not a refusal to appear — out of the
# bucket meant for `dcrc60` and `JDE`.
MIN_READABLE_LETTERS = 3


def _tokens(nick: str | None) -> list[str]:
    return [t for t in _SPLIT.split((nick or "").strip()) if t]


def is_name_shaped(nick: str | None) -> bool:
    """Two tokens, built like a forename and a surname.

    A single given name is not enough, and does not need to be: measured over
    this corpus, 5 of 1,666 one-word handles are given names. Presenting as a
    person is done with a full name here, which is why no list of first names
    is needed anywhere in this module.
    """
    t = _tokens(nick)
    if len(t) != 2 or not all(_WORD.match(x) for x in t):
        return False
    given, family = t
    if not family[:1].isupper() or len(family) < 2:
        return False
    return bool(_INITIALS.match(given) or (given[:1].isupper() and len(given) >= 2))


# A short all-capitals handle is an initialism, not a shouted word — JDE, NDR,
# ATPI. Needed because lowering the readable floor to three let `JDE` through on
# the strength of its E; a vowel does not make an initialism pronounceable.
MAX_INITIALISM = 4


def form(nick: str | None) -> str:
    """Which of the three shapes a handle takes."""
    if is_name_shaped(nick):
        return "personal-name"
    letters = _LETTERS_ONLY.sub("", nick or "")
    if len(letters) < MIN_READABLE_LETTERS or not _VOWEL.search(letters):
        return "opaque"
    if letters.isupper() and len(letters) <= MAX_INITIALISM:
        return "opaque"
    return "alias"


def read(nick: str | None) -> dict:
    """The full reading of one handle: its shape and the signals behind it.

    `full_caps_surname` is worth keeping separately. A handle written
    `Michel THOMAS` is the convention of an administrative form, not of a
    comment thread — the shape someone produces when they answer a box that
    asked for a legal name.
    """
    t = _tokens(nick)
    f = form(nick)
    return {
        "form": f,
        "meaning": FORM_MEANING[f],
        "tokens": len(t),
        "full_caps_surname": bool(
            f == "personal-name" and len(t[1]) > 2 and t[1].isupper()),
        "initialled": bool(f == "personal-name" and _INITIALS.match(t[0])
                           and len(t[0]) <= 3),
        "has_digits": any(ch.isdigit() for ch in (nick or "")),
    }


def annotate(rows, *, field: str = "nick", into: str = "handle_form"):
    """Attach the reading to each row in place; returns the rows."""
    for r in rows:
        r[into] = read(r.get(field))
    return rows


def distribution(nicks, *, unmasked=None) -> dict:
    """How a population presents itself.

    `unmasked` is the set of handles a reader has recognised as a reference or a
    pun — pass `nicknames.read` results in. Name-shaped handles are then split
    into those known to be somebody else's name or a joke, and the remainder,
    which is genuinely unresolved and reported as such rather than as "real".
    """
    nicks = [n for n in nicks if n]
    counts = Counter(form(n) for n in nicks)
    total = len(nicks) or 1
    named = [n for n in nicks if form(n) == "personal-name"]
    known = [n for n in named if n in (unmasked or ())]
    return {
        "total": len(nicks),
        "forms": [{"form": f, "n": counts.get(f, 0),
                   "share": round(counts.get(f, 0) / total, 4),
                   "meaning": FORM_MEANING[f]} for f in FORMS],
        "name_shaped": len(named),
        "name_shaped_recognised": len(known),
        "name_shaped_unresolved": len(named) - len(known),
        "full_caps_surname": sum(1 for n in named if read(n)["full_caps_surname"]),
    }


def overview(conn, *, min_nicks: int = 20) -> dict:
    """Everything the write-up quotes about self-presentation, computed live.

    Nothing here is ever typed into the prose: the shares move whenever the
    classifier is corrected, and a finding carrying a stale number is worse
    than one carrying none.
    """
    from . import nicknames
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT author_nick FROM comment "
                    "WHERE author_nick IS NOT NULL")
        nicks = [r[0] for r in cur.fetchall()]
    unmasked = {n for n in nicks if nicknames.read(n)}
    return {"handle_forms": split(conn, min_nicks=min_nicks),
            "handle_form_totals": distribution(nicks, unmasked=unmasked)}


def split(conn, *, min_nicks: int = 20) -> list[dict]:
    """How each public signed itself, by comment backend and capture era.

    Grouped by year as well as by community because the shape turns out not to
    be a property of a public at all: Le Matin's own share collapses across a
    platform change, with the same readership on either side of it. Any
    comparison between two communities is therefore comparing two sign-up
    forms first and two publics second, and this table exists to keep that
    visible rather than to be quoted as a difference between readerships.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT c.author_nick, j.community, a.origin,
                   EXTRACT(YEAR FROM cs.posted_at)::int AS yr
            FROM comment c
            JOIN comment_snapshot cs ON cs.comment_id = c.id
            JOIN article a ON a.id = c.article_id
            JOIN journal j ON j.id = a.journal_id
            WHERE c.author_nick IS NOT NULL AND cs.posted_at IS NOT NULL
        """)
        rows = cur.fetchall()

    groups: dict[tuple, set] = {}
    for nick, community, origin, year in rows:
        groups.setdefault((community, origin, year), set()).add(nick)

    out = []
    for (community, origin, year), nicks in sorted(groups.items()):
        if len(nicks) < min_nicks:
            continue
        d = distribution(nicks)
        out.append({"community": community, "origin": origin, "year": year,
                    "nicks": d["total"],
                    "name_shaped": d["name_shaped"],
                    "share": round(d["name_shaped"] / d["total"], 4)})
    return out
