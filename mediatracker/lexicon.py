"""Who writes with the same hands — from the text itself, not from rates.

`proximity` measures a writer as thirteen averages: word length, punctuation
per comment, accent discipline. Those are stable and cheap, and on a short
sample they are close to useless. Measured against this corpus, a probe of
1300 characters ranks its own author top of 744 profiles **8%** of the time.

Character 4-grams do the same job at **49%**, because they carry the things a
person actually repeats — a contraction, a slang turn, a habitual misspelling,
a space before a colon, an accent skipped on one word and not another — instead
of averaging them away. Held out by time, so the two halves of a writer never
share a thread:

    method                          top-1   top-5   median rank (of 744)
    aggregate rates (proximity.py)     8%     46%        8
    rare words, idf-weighted          40%     60%        4
    character 4-grams                 49%     71%        2
    turns of phrase (2-3 words)       17%     37%       12

Two artefacts had to be removed before any of that was true, and both are
still handled here rather than left to the caller:

  * **Size.** Uncapped, the corpus's largest profile headed the ranking for
    five unrelated probes out of five — a long document overlaps everything.
    Every candidate is cut to its most recent `TAIL_CHARS`.
  * **@mentions.** The strongest match found for one arrival was driven
    entirely by fragments of `@Fluide-Glacial`: both had answered the same
    commenter that afternoon. That is co-presence in a thread, not authorship.
    Mentions and URLs are stripped before anything is counted.

Capping bounds the size effect without abolishing it: a profile sitting at the
cap still overlaps more of the n-gram space than the median profile's 900-odd
characters, and such profiles do turn up as hubs across many pairs. Every
result therefore carries the character count of both sides and the n-grams the
match rests on, so a hub is visible as one rather than read as a discovery.

What this still cannot do is tell you the right answer is present at all. With
the true author in the population the best score is 0.175; with that author
removed it is 0.154. The ORDER carries the signal; the number does not. Read
`standout` — every ranking here reports how far its top sits above what the
best of a field that size is worth by chance.
"""
from __future__ import annotations

import math
import re
from collections import Counter

NGRAM = 4

# Every candidate is compared on the same amount of text, and the recent end is
# the one kept: a rename hypothesis is about how somebody wrote just before
# they stopped, not about how they wrote in 2013.
TAIL_CHARS = 3000

# Below this a ranking is worse than the coin-toss it looks like. See the table
# above: at ~1300 characters the true author is top of 744 half the time at
# best, and the tail below that was not worth measuring.
MIN_CHARS = 1000

# A handle written by the commenter is evidence about who they answer, not
# about how they write, and it is dense in rare n-grams — it will dominate a
# match between two strangers replying to the same person. URLs likewise.
_MENTION = re.compile(r"@[\wÀ-ɏ.\-_']+", re.UNICODE)
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_SPACE = re.compile(r"\s+")


def normalise(text: str | None) -> str:
    """Lower-cased text with mentions, URLs and run-together space removed."""
    if not text:
        return ""
    return _SPACE.sub(" ", _URL.sub(" ", _MENTION.sub(" ", text))).strip().lower()


def tail(comments: list[dict], *, cap: int = TAIL_CHARS) -> str:
    """The most recent `cap` characters a subject wrote, oldest-first.

    Undated comments are dropped rather than guessed at: without a timestamp
    there is no way to know whether they belong to the recent end.
    """
    dated = sorted((c for c in comments if c.get("posted_at")),
                   key=lambda c: c["posted_at"], reverse=True)
    picked: list[str] = []
    total = 0
    for c in dated:
        if total >= cap:
            break
        t = normalise(c.get("body_text"))
        if t:
            picked.append(t)
            total += len(t)
    return " ".join(reversed(picked))


def grams(text: str, n: int = NGRAM) -> list[str]:
    return [text[i:i + n] for i in range(max(0, len(text) - n + 1))]


class Index:
    """Every subject as a bag of character n-grams, weighted by rarity.

    The weighting is inverse document frequency over this population: an
    n-gram everybody writes says nothing, one only two profiles ever produce
    says a great deal. That makes the index population-relative — restrict it
    to a community and the scores describe that community, which is the
    intended reading.
    """

    def __init__(self, texts: dict) -> None:
        self.texts = texts
        self.docs = {k: Counter(grams(t)) for k, t in texts.items()}
        n = max(1, len(self.docs))
        df: Counter = Counter()
        for bag in self.docs.values():
            df.update(bag.keys())
        self.idf = {g: math.log(n / d) for g, d in df.items()}
        self.norm = {
            k: math.sqrt(sum((c * self.idf.get(g, 0.0)) ** 2
                             for g, c in bag.items())) or 1.0
            for k, bag in self.docs.items()
        }

    def __contains__(self, key) -> bool:
        return key in self.docs

    def chars(self, key) -> int:
        return len(self.texts.get(key, ""))

    def similarity(self, a, b) -> float:
        """Cosine between two subjects' weighted n-gram bags."""
        da, db = self.docs.get(a), self.docs.get(b)
        if not da or not db:
            return 0.0
        shared = da.keys() & db.keys()
        if not shared:
            return 0.0
        num = sum(da[g] * db[g] * self.idf.get(g, 0.0) ** 2 for g in shared)
        return num / (self.norm[a] * self.norm[b])

    def drivers(self, a, b, limit: int = 12) -> list[str]:
        """The n-grams contributing most to a match, largest first.

        Printed beside every result on purpose. A score is a number to trust or
        not; this is the evidence, and it is what exposed the @mention artefact
        the module now strips — the reader can see whether a match rests on a
        habit or on two people naming the same third party.
        """
        da, db = self.docs.get(a), self.docs.get(b)
        if not da or not db:
            return []
        shared = da.keys() & db.keys()
        weighted = ((da[g] * db[g] * self.idf.get(g, 0.0) ** 2, g) for g in shared)
        return [g for _, g in sorted(weighted, reverse=True)[:limit]]

    def rank(self, key, *, eligible=None, limit: int = 25) -> dict:
        """Everyone else, closest first, with the standout figure for the top.

        `eligible` filters candidates before scoring — a succession search only
        wants accounts that had already gone quiet, and the standout figure has
        to be computed over the field actually considered, not over the corpus.
        """
        if key not in self.docs:
            return {"candidates": [], "field": 0, "n_chars": 0,
                    **standout([])}
        scored = []
        for other in self.docs:
            if other == key or (eligible is not None and not eligible(other)):
                continue
            scored.append((self.similarity(key, other), other))
        scored.sort(reverse=True)
        out = [{"key": k, "score": round(s, 4), "n_chars": self.chars(k),
                "drivers": self.drivers(key, k)} for s, k in scored[:limit]]
        return {"candidates": out, "field": len(scored),
                "n_chars": self.chars(key),
                **standout([s for s, _ in scored])}


def standout(scores: list[float]) -> dict:
    """How far the best score sits above its field, and above coincidence.

    The maximum of many draws is high by construction: over n samples of almost
    any well-behaved distribution the largest sits about sqrt(2 ln n) standard
    deviations above the mean with no signal present at all — near 3.6 for a
    field of seven hundred. `excess` is the only one of the three worth
    quoting on its own, and a negative one means the best match is doing worse
    than a coincidence would.

    Under a field of 20 the spread is itself too noisy to divide by, so nothing
    is offered rather than something bad.
    """
    if len(scores) < 20:
        return {"lift": None, "chance": None, "excess": None}
    mean = sum(scores) / len(scores)
    var = sum((s - mean) ** 2 for s in scores) / len(scores)
    sd = math.sqrt(var)
    if sd <= 0:
        return {"lift": None, "chance": None, "excess": None}
    lift = (max(scores) - mean) / sd
    chance = math.sqrt(2 * math.log(len(scores)))
    return {"lift": round(lift, 2), "chance": round(chance, 2),
            "excess": round(lift - chance, 2)}


_CACHE: dict = {}


def load(conn, *, community: str | None = None, min_comments: int = 3) -> Index:
    """The index for a community, built from the comments and cached.

    One loader for every caller. The subject keys have to fold nicknames into
    confirmed personas exactly as the rest of the study does, and reproducing
    that in a second query is how two views of the same corpus quietly stop
    agreeing — so this goes through `profiling.build_subjects` like everything
    else, and pays for it once per scan rather than once per request.
    """
    from . import profiling as pf
    with conn.cursor() as cur:
        cur.execute("SELECT max(last_seen) FROM comment")
        stamp = cur.fetchone()[0]
    key = (community, min_comments, stamp)
    if _CACHE.get("key") == key:
        return _CACHE["value"]
    subjects = [
        {"community": s["community"], "subject_kind": s["kind"],
         "subject_key": s["key"], "comments": s["comments"]}
        for s in pf.build_subjects(conn, min_comments=min_comments)
        if community is None or s["community"] == community
    ]
    index = build(subjects)
    _CACHE.clear()
    _CACHE.update(key=key, value=index)
    return index


def build(subjects: list[dict]) -> Index:
    """An index over subjects carrying their comment lists.

    Each subject needs `community`, `subject_kind`, `subject_key` and either a
    `comments` list or a ready `text`. Subjects with too little text are kept
    in the index — they are legitimate candidates, and a caller that wants to
    rank FROM one should check `MIN_CHARS` first.
    """
    texts = {}
    for s in subjects:
        key = (s["community"], s["subject_kind"], s["subject_key"])
        texts[key] = s["text"] if "text" in s else tail(s.get("comments") or [])
    return Index(texts)
