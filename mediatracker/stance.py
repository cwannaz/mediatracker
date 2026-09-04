"""Not how someone writes, but what they are doing in the thread.

Every similarity measure in this project asks one question: whose prose most
resembles whose. That question is answered largely by *register*, so two
careful writers of French score alike whoever they are, and two careless ones
likewise. Measured on the whole Le Matin population, a search for the writer
Cedric can recognise on sight returned a best match scoring **worse than
coincidence** -- not because the method is weak but because it was answering
the wrong question.

What a reader actually recognises is a rhetorical position. Cedric put it as
someone being "above the pack on many aspects ... a human eye can spot the
whole as a singularity". Read the comments and the singularity is not
vocabulary at all:

    "donnez-vous les moyens de vérifier l'info, la crédibilité des sources"
    "quand le rapport d'autopsie précise qu'un véhicule lui a roulé dessus,
     vous dites que je lis n'importe quoi"

That is a debunker embedded in a conspiracy milieu: arguing with commenters
rather than about the article, quoting opponents back at them, citing sources.
It is a role, and roles are measurable. Scored on the five axes below, the same
writer the style metrics ranked 153rd of 735 comes first, at eight standard
deviations on milieu engagement.

Roles also survive a nickname change better than habits do. Someone can drop a
punctuation tic on purpose; they do not stop being the person who argues.

**Every axis here is confounded, and the confound is the whole difficulty.**

  * *Reply rate* looks like a fact about the writer and is partly a fact about
    the page. Threading survives only where the platform recorded it and the
    parser found it: Le Matin's archive rows carry a parent on 24.9% of
    comments and its sitemap rows on 37.7%, and `pdf` rows carry none at all.
    A writer whose comments happen to come from the archive would look less
    argumentative than an identical writer read from the live site. So reply
    rate is scored against the baseline for that writer's OWN mix of journal,
    origin and year -- see `reply_baseline` -- and `pdf` is excluded outright
    rather than counted as silence.

  * *Milieu engagement* is period-bound. A 2010s lexicon (chemtrails, Soral,
    11-septembre) makes any modern writer look disengaged, and a 2020s one
    (WEF, plandémie, grand remplacement) does the same to a 2013 writer. Each
    subject is scored against the lexicon of the years they actually wrote in,
    which is what makes an old writer and a new one comparable at all.

Nothing here identifies anybody. A joint outlier is a person worth reading, and
the drivers are returned beside the score so the reading can start.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict

log = logging.getLogger(__name__)

WORD = re.compile(r"[A-Za-zÀ-ÿ']+")

# Second person. Arguing with a commenter rather than about the article -- the
# single clearest mark of the debunker role, and cheap to count.
ADDRESS = frozenset("vous vos votre vôtre".split())

# Appeals to evidence. Not "is this person right" but "does this person argue
# by citation at all", which is a stance whether or not the sources are good.
EVIDENCE = frozenset("""
source sources preuve preuves vérifier vérifiez vérification rapport étude
études crédibilité prétendre prétendez affirmer affirmez démontrer factuel
faits information informations citer citez contredire prouve prouver lien
article référence chiffres statistiques auteur documenté documentée
""".split())

# Topical lexicons, one per period. The overlap between them is deliberate:
# terms like `complot` and `propagande` never went away, and dropping them
# from either list would make the two eras incomparable in the wrong direction.
MILIEU_ERAS: tuple[tuple[range, frozenset], ...] = (
    (range(2006, 2018), frozenset("""
        complot complots complotiste complotistes chemtrails illuminati
        reptilien reptiliens maçon maçons maçonnerie sioniste sionistes
        mainstream moutons réveillez endormis pharma puce puces 11-septembre
        wtc dieudonné soral quenelle désinformation propagande manipulation
        élite élites lobby lobbies officielle version
    """.split())),
    (range(2018, 2100), frozenset("""
        complot complots complotiste complotistes désinformation propagande
        manipulation élite élites lobby lobbies mainstream moutons réveillez
        endormis narratif narratifs wef davos schwab remplacement woke wokisme
        deepstate soros plandémie plandemie nanoparticules arn nwo mondialiste
        mondialistes reset merdias msm factcheckers transhumanisme eugénisme
        orwellien orwell troupeau covidisme
    """.split())),
)

# Origins whose rows never carry threading, so a reply rate computed over them
# is a measurement of the parser, not of the writer.
NO_THREADING = frozenset({"pdf"})

AXES = ("reply", "address", "evidence", "quotation", "milieu")

# Below this a rate is arithmetic on too little text to mean anything. The
# style work already produced one misleading shortlist from 16-comment
# accounts scoring 0.77 where a 3,000-comment account scored 0.52.
MIN_WORDS = 2000
MIN_THREADED = 30      # comments from threading-bearing origins


def milieu_for(year: int | None) -> frozenset:
    """The topical lexicon of a given year."""
    if year is None:
        return MILIEU_ERAS[-1][1]
    for years, lex in MILIEU_ERAS:
        if year in years:
            return lex
    return MILIEU_ERAS[-1][1]


def reply_baseline(subjects: list[dict]) -> dict[tuple, float]:
    """Population reply rate per (journal, origin, year).

    This is the correction that makes reply rate a fact about writers. The
    same person read from Le Matin's archive and from its sitemap would show
    24.9% and 37.7% purely from what each route preserves, so the question has
    to become "more or less than others writing in the same place at the same
    time", never "more or less than everyone".
    """
    seen: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
    for s in subjects:
        for c in s["comments"]:
            if c.get("origin") in NO_THREADING:
                continue
            when = c.get("posted_at")
            cell = (c.get("journal"), c.get("origin"), when.year if when else None)
            slot = seen[cell]
            slot[0] += 1
            slot[1] += 1 if c.get("is_reply") else 0
    return {k: (v[1] / v[0]) for k, v in seen.items() if v[0] >= 50}


def measure(subject: dict, baseline: dict[tuple, float]) -> dict | None:
    """One subject's stance rates. None when there is too little to measure.

    Rates are per 1,000 words so a prolific writer and an occasional one are
    on the same scale; `reply` is the exception and is a ratio against what
    the subject's own journal/origin/year mix would predict, where 1.0 means
    "replies exactly as often as the room did".
    """
    words: list[str] = []
    quotes = 0
    milieu_hits = 0
    threaded = replies = 0
    expected = 0.0
    years: Counter = Counter()

    for c in subject["comments"]:
        text = c.get("body_text") or ""
        ws = WORD.findall(text)
        words.extend(w.lower() for w in ws)
        quotes += text.count('"') + text.count("«")

        when = c.get("posted_at")
        year = when.year if when else None
        years[year] += 1
        lex = milieu_for(year)
        milieu_hits += sum(1 for w in ws if w.lower() in lex)

        if c.get("origin") not in NO_THREADING:
            cell = (c.get("journal"), c.get("origin"), year)
            rate = baseline.get(cell)
            if rate is not None:
                threaded += 1
                replies += 1 if c.get("is_reply") else 0
                expected += rate

    nw = len(words)
    if nw < MIN_WORDS:
        return None

    out = {
        "address": 1000 * sum(1 for w in words if w in ADDRESS) / nw,
        "evidence": 1000 * sum(1 for w in words if w in EVIDENCE) / nw,
        "quotation": 1000 * quotes / nw,
        "milieu": 1000 * milieu_hits / nw,
        "n_words": nw,
        "n_comments": len(subject["comments"]),
        "n_threaded": threaded,
        "years": sorted(y for y in years if y),
    }
    # A subject read entirely from pdf rows, or from cells too small to have a
    # baseline, has no measurable reply behaviour. That is missing, not zero:
    # scoring it as zero would rank the least observable writers as the least
    # argumentative ones.
    out["reply"] = (replies / expected) if (threaded >= MIN_THREADED and expected > 0) else None
    return out


def _standardise(rows: list[dict], axis: str) -> tuple[float, float]:
    vals = [r[axis] for r in rows if r.get(axis) is not None]
    if len(vals) < 20:
        return 0.0, 1.0
    mu = sum(vals) / len(vals)
    sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals))
    return mu, (sd or 1.0)


def _exposure(r: dict, axis: str) -> float:
    """How much material the rate on this axis was measured from.

    Words for the lexical axes; threaded comments for the reply ratio, which
    is counted per comment and not per word.
    """
    return float(r["n_threaded"]) if axis == "reply" else float(r["n_words"]) / 1000.0


def _shrink(value: float, exposure: float, mu: float, sd: float) -> float:
    """Pull a rate toward the population mean in proportion to its noise.

    Volume-neutral in expectation is not the same as volume-neutral, and the
    difference is the whole tail of the ranking. A rate per 1,000 words from
    3,000 words of text has roughly fifteen times the sampling spread of the
    same rate from 700,000, so without this the top and bottom of every axis
    fill up with small accounts and the ranking measures sample size.

    The weight is the standard empirical-Bayes one for a count rate: the
    population variance over itself plus the sampling variance, the latter
    approximated as Poisson (`mu / exposure`). A subject with plenty of
    material keeps almost all of its deviation; one with little keeps almost
    none, which is the honest statement about what was observed.
    """
    if exposure <= 0:
        return mu
    sampling = mu / exposure if mu > 0 else 0.0
    if sampling <= 0:
        return value
    w = (sd ** 2) / ((sd ** 2) + sampling)
    return mu + (value - mu) * w


def build(subjects: list[dict], *, min_comments: int = 3) -> list[dict]:
    """Score every subject and standardise against this population.

    The z-scores describe whoever was passed in, so restricting to a community
    makes them describe that community -- the same population-relative reading
    `lexicon.Index` uses, and the intended one.
    """
    baseline = reply_baseline(subjects)
    rows = []
    for s in subjects:
        if len(s["comments"]) < min_comments:
            continue
        m = measure(s, baseline)
        if m is None:
            continue
        m.update(community=s["community"], kind=s["kind"], key=s["key"],
                 label=s.get("label") or str(s["key"]))
        rows.append(m)

    stats = {a: _standardise(rows, a) for a in AXES}
    for r in rows:
        z = {}
        for a in AXES:
            if r.get(a) is None:
                continue
            mu, sd = stats[a]
            z[a] = (_shrink(r[a], _exposure(r, a), mu, sd) - mu) / sd
        r["z"] = z
        # An unmeasured axis counts as typical (z = 0), never as absent. The
        # alternative -- averaging over only the axes that exist -- quietly
        # rewards a subject whose missing axis would have been a weak one, and
        # `pdf`-only writers have no reply signal through no fault of their own.
        r["stance"] = sum(z.get(a, 0.0) for a in AXES) / len(AXES)
        r["n_axes"] = len(z)
    rows.sort(key=lambda r: -r["stance"])
    return rows


def rank(conn, *, community: str | None = None, min_comments: int = 3,
         limit: int = 50) -> dict:
    """The population ordered by stance, strongest first."""
    from . import profiling as pf
    subjects = [s for s in pf.build_subjects(conn, min_comments=min_comments)
                if community is None or s["community"] == community]
    rows = build(subjects, min_comments=min_comments)
    return {"rows": [_brief(r) for r in rows[:limit]],
            "field": len(rows), "community": community}


def neighbours(conn, *, kind: str, key: str, community: str | None = None,
               min_comments: int = 3, limit: int = 25,
               after: bool = False) -> dict:
    """Who occupies the same stance position as one subject.

    `after` keeps only subjects that began writing once this one had stopped,
    which is the succession question. Distance is Euclidean over the shared
    axes and normalised by how many were shared, so a pair compared on four
    axes is not flattered against a pair compared on five.
    """
    from . import profiling as pf
    subjects = [s for s in pf.build_subjects(conn, min_comments=min_comments)
                if community is None or s["community"] == community]
    span = {}
    for s in subjects:
        ds = [c["posted_at"] for c in s["comments"] if c.get("posted_at")]
        if ds:
            span[(s["community"], s["kind"], str(s["key"]))] = (min(ds), max(ds))

    rows = build(subjects, min_comments=min_comments)
    idx = {(r["community"], r["kind"], str(r["key"])): r for r in rows}
    me = idx.get((community or rows[0]["community"] if rows else None, kind, str(key)))
    if me is None:
        for r in rows:
            if r["kind"] == kind and str(r["key"]) == str(key):
                me = r
                break
    if me is None:
        return {"subject": None, "candidates": [], "field": 0}

    mine = span.get((me["community"], me["kind"], str(me["key"])))
    out = []
    for r in rows:
        if r is me:
            continue
        if after:
            theirs = span.get((r["community"], r["kind"], str(r["key"])))
            if not (mine and theirs and theirs[0] > mine[1]):
                continue
        shared = [a for a in AXES if a in me["z"] and a in r["z"]]
        if len(shared) < 3:
            continue
        d = math.sqrt(sum((me["z"][a] - r["z"][a]) ** 2 for a in shared) / len(shared))
        out.append((d, r, shared))
    out.sort(key=lambda t: t[0])
    return {"subject": _brief(me), "field": len(out),
            "candidates": [{**_brief(r), "distance": round(d, 3),
                            "on_axes": shared} for d, r, shared in out[:limit]]}


def _brief(r: dict) -> dict:
    return {"community": r["community"], "kind": r["kind"], "key": r["key"],
            "label": r["label"], "stance": round(r["stance"], 3),
            "z": {k: round(v, 2) for k, v in r["z"].items()},
            "rates": {a: (round(r[a], 3) if r.get(a) is not None else None)
                      for a in AXES},
            "n_comments": r["n_comments"], "n_words": r["n_words"],
            "n_threaded": r["n_threaded"], "n_axes": r["n_axes"],
            "years": r["years"]}


def main(argv=None) -> int:
    import argparse
    from . import db
    from .config import load_config
    p = argparse.ArgumentParser(prog="mediatracker.stance")
    p.add_argument("--community", default="lematin")
    p.add_argument("--min-comments", type=int, default=3)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--near", default=None,
                   help="nick or persona key to find stance neighbours for")
    p.add_argument("--kind", default="nick", choices=("nick", "persona"))
    p.add_argument("--after", action="store_true",
                   help="only subjects that started after this one stopped")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    conn = db.connect(load_config())
    if conn is None:
        raise SystemExit("no database")

    if a.near:
        res = neighbours(conn, kind=a.kind, key=a.near, community=a.community,
                         min_comments=a.min_comments, limit=a.limit,
                         after=a.after)
        me = res["subject"]
        if not me:
            raise SystemExit(f"no subject {a.kind}:{a.near} with enough text")
        print(f"{me['label']}  stance {me['stance']:+.2f}  "
              f"{me['n_comments']} comments  z={me['z']}")
        print(f"field {res['field']}")
        for c in res["candidates"]:
            print(f"  d={c['distance']:.2f}  {c['label'][:32]:34s} "
                  f"stance {c['stance']:+.2f}  {c['years'][:1]}..{c['years'][-1:]}")
        return 0

    res = rank(conn, community=a.community, min_comments=a.min_comments,
               limit=a.limit)
    print(f"field {res['field']}")
    hdr = f"{'#':>4s} {'subject':32s}{'stance':>8s}" + "".join(f"{x:>10s}" for x in AXES)
    print(hdr)
    for i, r in enumerate(res["rows"], 1):
        cells = "".join(f"{r['z'][x]:+10.2f}" if x in r["z"] else f"{'--':>10s}"
                        for x in AXES)
        print(f"{i:4d} {r['label'][:31]:32s}{r['stance']:+8.2f}{cells}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
