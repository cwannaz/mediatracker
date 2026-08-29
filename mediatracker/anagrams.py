"""Handles built from the same letters as another handle.

A commenter who wants a new name without giving up the old one has a small set
of moves, and two of them leave the letters intact:

    PierreErreip  ->  erreippierre  ->  erreippierre1
    Talion.       <-> Noilat

Neither is reachable by the tools already here. `alias_candidates` folds case,
accents and punctuation, so it catches respellings and near-misses within two
edits; a reordering of eleven letters is neither. Stylometry did not find the
Pierre chain either. A letter-multiset index finds it in one pass and, unlike
everything else in this study, is not a guess — the letters either match or
they do not.

**Token permutations are excluded on purpose.** `herve tinguely` /
`tinguely herve` and `Nom Prénom` / `Prénom Nom` are the same words in the other
order, which in this corpus is the 2012 sign-up form letting people fill the
two boxes either way. That is a fact about the form, not a choice by the writer,
and it would swamp the interesting cases.

What is left is a reordering someone had to construct. It is still not proof of
anything — two handles can share letters by accident, which is why the ratio of
groups to handles matters and is reported — but a match here is a fact about
the strings rather than an inference about the writers.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from itertools import permutations

from .alias_candidates import MAX_LEV as MAX_LEV_ALREADY_SEEN
from .alias_candidates import _lev

# Only the letters carry the anagram. Digits are stripped rather than kept:
# `erreippierre` and `erreippierre1` are the same construction with a counter
# bolted on, and treating the 1 as significant would split them.
_NOT_LETTER = re.compile(r"[^a-z]")
_TOKEN_SPLIT = re.compile(r"[^0-9a-zà-öø-ÿ]+", re.IGNORECASE)

# Below this a coincidence is likelier than a construction: over three thousand
# handles, six letters collide by chance constantly.
MIN_LETTERS = 7

# A reversal is allowed to be shorter. Sharing a letter multiset happens by
# accident; being the other handle backwards does not, so the evidence carries
# at a length where a plain anagram would not.
MIN_REVERSAL_LETTERS = 6


def fold(nick: str | None) -> str:
    """Accent-, case- and punctuation-free form. Same rule as alias_candidates."""
    s = unicodedata.normalize("NFD", nick or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def letters(nick: str | None) -> str:
    """The handle's letters in order, digits and punctuation gone.

    Digits are dropped because `erreippierre` and `erreippierre1` are one
    construction with a counter bolted on. That makes this the test for "same
    word, different decoration" — `Caribou 13` and `Caribou0427` have identical
    letter sequences and are a respelling, not an anagram.
    """
    return _NOT_LETTER.sub("", fold(nick))


def signature(nick: str | None) -> str:
    """The letters of a handle, sorted — its anagram class."""
    return "".join(sorted(_NOT_LETTER.sub("", fold(nick))))


def token_bag(nick: str | None) -> tuple[str, ...]:
    """The handle's words, folded and sorted. Two handles with the same bag are
    the same words reordered — a form-field artefact, not a construction."""
    s = unicodedata.normalize("NFD", nick or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return tuple(sorted(t for t in _TOKEN_SPLIT.split(s.lower()) if t))


def is_reversal(a: str | None, b: str | None) -> bool:
    """One handle is the other spelled backwards."""
    fa, fb = letters(a), letters(b)
    return len(fa) >= MIN_REVERSAL_LETTERS and fa != fb and fa == fb[::-1]


def mirror_of(nick: str | None) -> str | None:
    """A handle that is a word followed by its own reverse, or None.

    `PierreErreip`, `n3tfl1xx1lft3n`. A property of one handle rather than of a
    pair, and worth flagging on its own: it says the writer built the name, and
    it says what they built it from.
    """
    f = letters(nick)
    n = len(f)
    if n >= 8 and n % 2 == 0 and f[:n // 2] == f[n // 2:][::-1]:
        half = f[:n // 2]
        return half if len(set(half)) > 1 else None
    return None


def _worth_saying(a: str, b: str) -> bool:
    """Whether an anagram between two handles tells us anything new.

    Three ways it does not, and all three dominate the raw output:

      * **Same letters in the same order.** `Caribou 13` / `Caribou0427` —
        one word, two decorations. A respelling.
      * **Same words in another order.** `herve tinguely` / `tinguely herve`,
        `Nom Prénom` / `Prénom Nom`. The 2012 sign-up form took a forename and
        a surname in two boxes and did not care which went where; that is a
        fact about the form.
      * **Within two edits.** `La pas compris` / `La pas comrpis` is a typo,
        and `alias_candidates` already reports it as a weak pair.

    What survives is a reordering somebody had to build.
    """
    if letters(a) == letters(b):
        return False
    if token_bag(a) == token_bag(b):
        return False
    if _joined_permutation(a, b) or _joined_permutation(b, a):
        return False
    if _lev(fold(a), fold(b)) <= MAX_LEV_ALREADY_SEEN:
        return False
    return True


def _joined_permutation(spaced: str, run_together: str) -> bool:
    """`ruiz.alfredo` against `alfredoruiz`: the same two words in the other
    order, with the separator dropped on one side.

    The separator is what makes this decidable. Where neither handle is split —
    `guydebord` / `debordguy` — nothing in the strings says where the words end,
    so the pair is reported and left to a reader.
    """
    toks = [t for t in token_bag(spaced) if t]
    if len(toks) < 2 or len(toks) > 4:
        return False
    target = letters(run_together)
    return any("".join(p) == target for p in permutations(toks))


def find(rows, *, key: str = "nick", community: str = "community") -> dict[str, dict]:
    """Map each handle to what it shares its letters with.

    Rows need a handle and the community it writes in; anything else on them is
    ignored. Matching is per community for the same reason everything else is:
    the same string in two comment backends is two people.
    """
    by_sig: dict[tuple, list] = defaultdict(list)
    for r in rows:
        nick = r.get(key)
        sig = signature(nick)
        if len(sig) >= MIN_LETTERS:
            by_sig[(r.get(community), sig)].append(nick)

    out: dict[str, dict] = {}
    for (_, _sig), nicks in by_sig.items():
        uniq = list(dict.fromkeys(n for n in nicks if n))
        if len(uniq) < 2:
            continue
        for nick in uniq:
            partners = [o for o in uniq if o != nick and _worth_saying(nick, o)]
            if not partners:
                continue
            out[nick] = {
                "partners": partners,
                "kind": "reversal" if any(is_reversal(nick, p) for p in partners)
                        else "anagram",
                "letters": len(signature(nick)),
            }

    # Reversals are swept separately because they are admissible shorter than
    # a plain anagram, so the signature index above has already dropped some.
    by_letters: dict[tuple, str] = {}
    for r in rows:
        nick = r.get(key)
        if nick:
            by_letters[(r.get(community), letters(nick))] = nick
    for (comm, f), nick in by_letters.items():
        back = by_letters.get((comm, f[::-1]))
        if back and back != nick and is_reversal(nick, back):
            entry = out.setdefault(nick, {"partners": [], "kind": "reversal",
                                          "letters": len(f)})
            entry["kind"] = "reversal"
            if back not in entry["partners"]:
                entry["partners"].append(back)

    # A self-mirror needs no partner to be worth showing.
    for r in rows:
        nick = r.get(key)
        m = mirror_of(nick)
        if m and nick not in out:
            out[nick] = {"partners": [], "kind": "mirror", "built_from": m,
                         "letters": len(signature(nick))}
        elif m and nick in out:
            out[nick]["built_from"] = m
    return out


def annotate(rows, *, key: str = "nick", community: str = "community",
             into: str = "anagram", index: dict | None = None):
    """Attach `find`'s reading to each row in place; returns the rows."""
    index = index if index is not None else find(rows, key=key, community=community)
    for r in rows:
        r[into] = index.get(r.get(key))
    return rows


def load(conn, *, min_comments: int = 1) -> dict[str, dict]:
    """Build the index over every handle in the corpus.

    Deliberately over the whole corpus rather than over one page of results: a
    handle's partner is usually not on the same screen, and a page-local index
    would report a handle as unmatched purely because of pagination.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.author_nick AS nick, j.community, count(*) AS n
            FROM comment c
            JOIN article a ON a.id = c.article_id
            JOIN journal j ON j.id = a.journal_id
            WHERE c.author_nick IS NOT NULL
            GROUP BY 1, 2
            HAVING count(*) >= %s
        """, (min_comments,))
        rows = [{"nick": r[0], "community": r[1]} for r in cur.fetchall()]
    return find(rows)
