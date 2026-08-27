"""Profiles the corpus had never seen before, and who they might have been.

A nickname appearing for the first time is the observable half of a rename:
the account that stops and the account that starts are two rows in the same
table, and nothing but the writing links them. This module finds the arrivals,
draws what they do afterwards, and ranks who they could have been before.

The hard part is not the ranking. It is that "new" is a statement about our
observation, not about the site. A nickname absent from the corpus before
Tuesday is only news if we were watching on Monday — and this corpus started
watching Le Matin on 22 August 2026, with nothing behind it but a hand-built
archive of selected threads going back to 2011. So every arrival is reported
together with the evidence of absence that backs it: how many days of dense
coverage the account was missing from, and how many comments went past in
that stretch without it. An arrival on the first dense day is worth nothing;
an arrival on the fifth is worth something.

One more thing limits every reading below: neither comment backend exposes a
stable user id, so a nickname is the whole of an identity here. A name never
seen before may be a new registration, a renamed account, or the same person
typing their own handle a little differently — the four spellings of
`VladimirRobson` in this corpus are one commenter, and they arrive as four
separate profiles. Absence of a user id is why style has to do the work.

Nothing here concludes a rename. It produces a shortlist, with the reasons a
human would need to argue with it.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter
from datetime import date, datetime, timedelta

from . import lexicon as lx
from . import profiling as pf
from . import proximity as px

# What the style measures need is text, not comments — a floor on the comment
# count was the wrong gate. Measured by hiding part of a well-documented
# commenter's output and asking the remainder to find it again, over a field of
# 744 Le Matin profiles:
#
#   1300 chars (~8 comments)   top-1 13%   top-5 41%   median rank 8
#   3000 chars (~17 comments)  top-1 51%   top-5 69%   median rank 1
#   6000 chars (~26 comments)  top-1 53%   top-5 77%   median rank 1
#
# So a thousand characters is about where a ranking starts to beat guessing,
# and three thousand is where it becomes worth reading. Arrivals under the
# floor are still listed — the arrival itself is a fact — but get no
# predecessor search.
MIN_CHARS_FOR_COMPARISON = lx.MIN_CHARS
THIN_CHARS = lx.TAIL_CHARS   # ranked below this, but weakly; say so

# A day counts as covered when it carries a real share of the recent daily
# volume. Anything thinner is a gap in the crawl, not a quiet day on the site,
# and treating it as observation would manufacture disappearances.
DENSE_FRACTION = 0.2


def _day(ts: datetime) -> date:
    return ts.astimezone(pf.CH).date()


def rateable(n_chars: int | None) -> tuple[bool, bool]:
    """(worth ranking at all, but thin) for a subject of this much text."""
    n = n_chars or 0
    return n >= MIN_CHARS_FOR_COMPARISON, n < THIN_CHARS


# --------------------------------------------------------------------------- #
# The live subject space
# --------------------------------------------------------------------------- #

# Deliberately NOT read from author_profile. That table is refreshed by the
# LLM pass, so the accounts this module exists for — the ones that appeared
# today — would not be in it. The style measures are deterministic, so they can
# be recomputed from the comments in a fraction of a second, and then the
# newcomers and the established profiles are measured by the same ruler.
_CACHE: dict = {}


def build(conn, *, community: str, min_comments: int = 3) -> dict:
    """Subjects of one community with metrics and daily counts, from comments.

    Cached against the newest comment we hold, so repeated calls during a
    browsing session are free and a finished scan invalidates it.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT max(last_seen) FROM comment")
        stamp = cur.fetchone()[0]
    key = (community, min_comments, stamp)
    if _CACHE.get("key") == key:
        return _CACHE["value"]

    rows = []
    for s in pf.build_subjects(conn, min_comments=min_comments):
        if s["community"] != community:
            continue
        stamps = [c["posted_at"] for c in s["comments"] if c.get("posted_at")]
        if not stamps:
            continue                      # undated: cannot be placed in time
        m = pf.measure(s["comments"])
        rows.append({
            "community": s["community"], "subject_kind": s["kind"],
            "subject_key": s["key"], "label": s["label"],
            "n_comments": m["n_comments"], "n_chars": m["n_chars"],
            "first_seen": min(stamps), "last_seen": max(stamps),
            "metrics": m, "daily": Counter(_day(t) for t in stamps),
            # The recent tail, normalised once here: the row is what both the
            # rate space and the n-gram index are built from, so neither can
            # end up reading a different version of the same writer.
            "text": lx.tail(s["comments"]),
        })

    value = {"rows": rows, "space": px.build_space(rows)["space"],
             "lexicon": lx.load(conn, community=community,
                                min_comments=min_comments)}
    _CACHE.clear()
    _CACHE.update(key=key, value=value)
    return value


# --------------------------------------------------------------------------- #
# Coverage: what our absence of evidence is worth
# --------------------------------------------------------------------------- #

def coverage(rows: list[dict]) -> dict:
    """The daily volume, and the stretch of it dense enough to reason on.

    `dense_from` is the start of the unbroken run of well-covered days ending
    today. Before it the crawl is patchy and an account missing from a day
    proves nothing about the account.
    """
    total: Counter = Counter()
    for r in rows:
        total.update(r["daily"])
    if not total:
        return {"daily": [], "dense_from": None, "floor": 0}

    days = sorted(total)
    recent = [total[d] for d in days[-7:]]
    floor = max(1.0, DENSE_FRACTION * statistics.median(recent))

    # Walk back from the last day for as long as the days stay dense AND
    # contiguous: one missing calendar day is a hole in the observation too.
    dense_from = days[-1]
    cursor = days[-1]
    while True:
        prev = cursor - timedelta(days=1)
        if total.get(prev, 0) < floor:
            break
        dense_from, cursor = prev, prev

    return {
        "daily": [{"day": d.isoformat(), "comments": total[d]} for d in days],
        "dense_from": dense_from.isoformat(),
        "dense_days": (days[-1] - dense_from).days + 1,
        "floor": round(floor, 1),
    }


def _evidence(r: dict, cov: dict, total: Counter) -> dict:
    """How much observation the arrival was absent from.

    This is the number that decides whether an arrival means anything, so it
    is computed per profile rather than stated once for the tab: an account
    that turned up on the first dense day was absent from nothing.
    """
    debut = _day(r["first_seen"])
    if not cov["dense_from"]:
        return {"absent_days": 0, "absent_comments": 0}
    start = date.fromisoformat(cov["dense_from"])
    if debut <= start:
        return {"absent_days": 0, "absent_comments": 0}
    span = [d for d in total if start <= d < debut]
    return {"absent_days": (debut - start).days,
            "absent_comments": sum(total[d] for d in span)}


# --------------------------------------------------------------------------- #
# Arrivals
# --------------------------------------------------------------------------- #

def overview(conn, *, community: str = "lematin", since: str | None = None,
             min_comments: int = 3, limit: int = 200) -> dict:
    """Profiles whose first comment in the corpus falls on or after `since`.

    Default `since` is the day AFTER dense coverage begins. On the first dense
    day every account in the community looks new, because that is the day the
    crawl opened its eyes — including it would drown the real arrivals in a
    few hundred artefacts.
    """
    built = build(conn, community=community, min_comments=min_comments)
    rows = built["rows"]
    cov = coverage(rows)

    total: Counter = Counter()
    for r in rows:
        total.update(r["daily"])

    if since:
        cut = date.fromisoformat(since)
    elif cov["dense_from"]:
        cut = date.fromisoformat(cov["dense_from"]) + timedelta(days=1)
    else:
        cut = date.min

    arrivals = [r for r in rows if _day(r["first_seen"]) >= cut]

    # Daily volume split into what the arrivals wrote and what everyone else
    # did, on one axis. The share is the readable quantity: an absolute count
    # of newcomer comments rises and falls with the news cycle.
    days = [d["day"] for d in cov["daily"]]
    new_by_day: Counter = Counter()
    for r in arrivals:
        new_by_day.update(r["daily"])
    debuts_by_day: Counter = Counter(_day(r["first_seen"]) for r in rows)

    series = []
    for iso in days:
        d = date.fromisoformat(iso)
        series.append({"day": iso, "comments": total[d],
                       "from_arrivals": new_by_day.get(d, 0),
                       "debuting": debuts_by_day.get(d, 0)})

    out = []
    for r in sorted(arrivals, key=lambda r: (-r["n_comments"], r["label"])):
        ev = _evidence(r, cov, total)
        out.append({
            "kind": r["subject_kind"], "key": r["subject_key"], "label": r["label"],
            "community": r["community"], "n_comments": r["n_comments"],
            "n_chars": r["n_chars"],
            "debut": _day(r["first_seen"]).isoformat(),
            "last": _day(r["last_seen"]).isoformat(),
            "active_days": len(r["daily"]),
            "daily": [r["daily"].get(date.fromisoformat(d), 0) for d in days],
            "comparable": rateable(r["n_chars"])[0],
            "thin": rateable(r["n_chars"])[1],
            **ev,
        })

    return {"community": community, "since": cut.isoformat(),
            "days": days, "series": series, "coverage": cov,
            "arrivals": out[:limit], "total_arrivals": len(out),
            "subjects": len(rows), "min_chars": MIN_CHARS_FOR_COMPARISON,
            "thin_chars": THIN_CHARS}


# --------------------------------------------------------------------------- #
# Who they might have been
# --------------------------------------------------------------------------- #

def rank(space: dict, me: dict, *, cut: datetime, dense_from: date | None,
         observed_only: bool = False, index: "lx.Index | None" = None,
         sort: str = "lexical") -> list[dict]:
    """Score every account that had gone quiet by `cut`, closest first.

    Two independent readings of the same pair, deliberately not merged:

      * **lexical** — shared character n-grams, from `lexicon`. The stronger of
        the two on short samples by a wide margin, and the default order.
      * **style** — the thirteen aggregate rates from `proximity`. Slower to
        become useful, but it fails differently, so a pair the two agree on is
        worth more than either says alone.

    Blending them into one number was not attempted here for the same reason
    rhythm is not blended into `proximity`: there is no weighting this corpus
    can justify yet, and an unjustified one would read as precision.

    Split out from `predecessors` because this is the part with the argument in
    it — which accounts are eligible at all, and whether we watched each one
    stop. The database only decides who is in the room.
    """
    mine = (me["subject"]["community"], me["subject"]["subject_kind"],
            me["subject"]["subject_key"])
    scored = []
    for other in space.values():
        if other is me:
            continue
        ob = other["subject"]
        if ob["last_seen"] is None or ob["last_seen"] > cut:
            continue
        seen_stop = dense_from is not None and _day(ob["last_seen"]) >= dense_from
        if observed_only and not seen_stop:
            continue
        c = px.compare(me, other)
        theirs = (ob["community"], ob["subject_kind"], ob["subject_key"])
        lexical = index.similarity(mine, theirs) if index else None
        if c is None and lexical is None:
            continue
        quiet = (me["subject"]["first_seen"] - ob["last_seen"]).total_seconds() / 86400
        scored.append({
            **(c or {"score": 0.0}),
            "style": None if c is None else c["score"],
            "lexical": None if lexical is None else round(lexical, 4),
            "drivers": index.drivers(mine, theirs) if index else [],
            # Both sides' text volume travels with the match: a candidate
            # sitting at the cap overlaps more of the n-gram space than a short
            # one, and a hub has to be visible as a hub.
            "b_chars": index.chars(theirs) if index else None,
            "b": px._brief(ob),
            "disappearance": "observed" if seen_stop else "unobserved",
            "quiet_days": round(quiet, 1),
        })

    # `score` is whichever signal the caller is ranking on, so the standout
    # figure downstream is computed over the field that produced the order.
    field = "lexical" if sort == "lexical" and index is not None else "style"
    for c in scored:
        c["score"] = c[field] if c[field] is not None else 0.0
    scored.sort(key=lambda c: -c["score"])
    return scored


def _lift(scored: list[dict]) -> dict:
    """`lexicon.standout` over a list of scored candidates."""
    return lx.standout([c["score"] for c in scored])


def predecessors(conn, *, community: str, kind: str, key: str,
                 min_gap_days: float = 0.5, min_comments: int = 3,
                 observed_only: bool = False, sort: str = "lexical",
                 limit: int = 12) -> dict:
    """Accounts that had already gone quiet, ranked by how alike they write.

    Only accounts whose last comment precedes the arrival's first by at least
    `min_gap_days` are eligible: an account still posting is not a nickname
    somebody abandoned, whatever the style says. That filter is the whole
    reason this is not simply `proximity.neighbours`.

    Each candidate is also labelled by whether we *watched* it stop. An account
    whose last comment falls inside the densely covered stretch went quiet in
    front of us; one that stopped before it may have been posting all along on
    threads the crawl never fetched, and its silence is our gap rather than its
    absence. The distinction matters more than the score: a rename is a
    disappearance and an appearance, and half of that is unobservable here.
    """
    built = build(conn, community=community, min_comments=min_comments)
    space = built["space"]
    me = space.get((community, kind, key))
    if me is None:
        return {"subject": None, "candidates": [], "reason": "not enough comments"}

    cov = coverage(built["rows"])
    dense_from = date.fromisoformat(cov["dense_from"]) if cov["dense_from"] else None

    debut = me["subject"]["first_seen"]
    cut = debut - timedelta(days=min_gap_days)

    index = built["lexicon"]
    scored = rank(space, me, cut=cut, dense_from=dense_from,
                  observed_only=observed_only, index=index, sort=sort)

    # Both fields get a standout figure, because "is this above coincidence"
    # has to be answered per signal — a pair can stand out lexically and be
    # unremarkable on the rates, and that disagreement is information.
    return {"subject": px._brief(me["subject"]),
            "candidates": scored[:limit], "field": len(scored),
            "observed_field": sum(1 for c in scored if c["disappearance"] == "observed"),
            **_lift(scored), "sort": sort,
            "lexical_standout": lx.standout([c["lexical"] for c in scored
                                             if c["lexical"] is not None]),
            "style_standout": lx.standout([c["style"] for c in scored
                                           if c["style"] is not None]),
            "min_gap_days": min_gap_days,
            "observed_only": observed_only,
            "dense_from": cov["dense_from"], "dense_days": cov.get("dense_days"),
            "n_chars": me["subject"]["n_chars"],
            "comparable": rateable(me["subject"]["n_chars"])[0],
            "thin": rateable(me["subject"]["n_chars"])[1]}
