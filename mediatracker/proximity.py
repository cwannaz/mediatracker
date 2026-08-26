"""Stylometric proximity between commenters.

One question: which two accounts write alike? The eventual use is to catch a
commenter who abandoned a nickname and came back under an unrelated one — the
spelling-based clustering in `alias_candidates` cannot see that, because the
two handles have nothing in common as strings.

Two independent signals, deliberately kept apart in the output:

  * **style** — how the two write, from the deterministic `metrics` only.
    Never from the LLM-inferred fields: those were produced by reading the
    text, so scoring a match on them would be scoring the reading twice.
  * **rhythm** — when they write, from the hour-of-day and day-of-week
    histograms. Two people can share a register; sharing a register *and* the
    same odd posting hours is much less common.

and a third that is not a similarity at all:

  * **overlap / gap** — whether the two accounts were ever live at the same
    time. Overlap is evidence *against* a succession, and that makes it a
    discriminator in both directions rather than a filter.

Nothing here decides anything. A high score is a pair worth a human look, and
belongs in `persona_alias` as `candidate` at best, with the evidence attached.
"""
from __future__ import annotations

import math
from datetime import datetime

# Scale-free style measures. Volume itself is excluded on purpose: a pair of
# prolific writers must not look alike merely for being prolific.
FEATURES = (
    "avg_word_length",
    "avg_words_per_comment",
    "avg_sentence_length_words",
    "vocabulary_richness_ttr",
    "hapax_ratio",
    "accented_word_rate",
    "accent_consistency",
    "all_caps_word_rate",
    "ellipsis_per_comment",
    "questions_per_comment",
    "exclamations_per_comment",
    "apostrophe_typographic_share",
    "space_before_punctuation_per_comment",
)

# Type-token ratio and hapax ratio both fall as a corpus grows, so on raw
# values every small account resembles every other small account — which is
# exactly the population a succession search cares about. Each is regressed on
# log(n_chars) and replaced by its residual, so what is left is the writer's
# richness relative to others who wrote as much.
LENGTH_SENSITIVE = ("vocabulary_richness_ttr", "hapax_ratio")

# The ranking is style alone. Blending rhythm into it was the obvious design
# and it is wrong: measured against the confirmed personas (see `calibrate`),
# style alone separates same-person pairs better than any blend tried, and
# every weight given to rhythm lowered the figure. Rhythm is still computed and
# reported — it is worth reading on a candidate — but it does not move the
# ranking, because nothing here says it should.


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _fit_line(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Least-squares slope and intercept. Flat line if x does not vary."""
    n = len(xs)
    if n < 3:
        return 0.0, (sum(ys) / n if n else 0.0)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return 0.0, my
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    return slope, my - slope * mx


def _hist(metrics: dict, name: str, size: int) -> list[float] | None:
    """A histogram as proportions. None when the subject has no counts."""
    raw = metrics.get(name) or {}
    vals = [float(raw.get(str(i), raw.get(i, 0)) or 0) for i in range(size)]
    total = sum(vals)
    return [v / total for v in vals] if total > 0 else None


def _affinity(p: list[float] | None, q: list[float] | None) -> float | None:
    """Bhattacharyya coefficient: 1.0 for identical distributions, 0 for
    disjoint ones. Bounded and reads as a proportion, unlike a divergence."""
    if not p or not q:
        return None
    return min(1.0, sum(math.sqrt(a * b) for a, b in zip(p, q)))


def load_subjects(conn, *, community: str | None = None,
                  min_comments: int = 5) -> list[dict]:
    """Profiled subjects with enough text for their metrics to mean anything.

    Below a handful of comments the style measures are mostly noise, and a
    proximity built on noise produces confident nonsense.
    """
    where = ["n_comments >= %s"]
    args: list = [min_comments]
    if community:
        where.append("community = %s")
        args.append(community)
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT community, subject_kind, subject_key, label, n_comments,
                   n_chars, first_seen, last_seen, metrics
            FROM author_profile
            WHERE {' AND '.join(where)}
            ORDER BY label
        """, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def build_space(subjects: list[dict]) -> dict:
    """Turn the raw metrics into a comparable space.

    Residualise the length-sensitive features, then standardise every feature
    so a difference of 1 means one standard deviation of this population. The
    population is whatever was passed in — restrict to a community and the
    z-scores describe that community, which is the intended reading.
    """
    usable = [s for s in subjects if isinstance(s.get("metrics"), dict)]
    raw: dict[str, list[float | None]] = {
        f: [_num((s["metrics"] or {}).get(f)) for f in [f] for s in usable]
        for f in FEATURES
    }

    # The scale each feature is measured on, taken BEFORE residualising: what
    # counts as "no variation left" has to be judged against the quantity
    # itself, not against the residuals, which may all be floating-point dust.
    scale = {f: (max((abs(v) for v in raw[f] if v is not None), default=0.0) or 1.0)
             for f in FEATURES}

    # Residualise against log(size). Only over the subjects that have both.
    logn = [math.log(max(1, s.get("n_chars") or 1)) for s in usable]
    for f in LENGTH_SENSITIVE:
        pairs = [(x, y) for x, y in zip(logn, raw[f]) if y is not None]
        if len(pairs) >= 3:
            slope, intercept = _fit_line([x for x, _ in pairs], [y for _, y in pairs])
            raw[f] = [None if y is None else y - (slope * x + intercept)
                      for x, y in zip(logn, raw[f])]

    # A feature that does not vary carries no information, and dividing by its
    # standard deviation would turn floating-point dust into a full standard
    # deviation of apparent difference — residualising a perfectly collinear
    # feature leaves residuals around 1e-16, whose spread is meaningless. Such
    # features are dropped rather than standardised.
    stats: dict[str, tuple[float, float] | None] = {}
    for f in FEATURES:
        vals = [v for v in raw[f] if v is not None]
        if len(vals) < 2:
            stats[f] = None
            continue
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
        sd = math.sqrt(var)
        stats[f] = None if sd <= 1e-9 * scale[f] else (mean, sd)

    space = {}
    for i, s in enumerate(usable):
        m = s["metrics"] or {}
        vec = []
        for f in FEATURES:
            v, st = raw[f][i], stats[f]
            if v is None or st is None:
                vec.append(None)
            else:
                mean, sd = st
                vec.append((v - mean) / sd)
        space[(s["community"], s["subject_kind"], s["subject_key"])] = {
            "subject": s, "vec": vec, "hour": _hist(m, "hour_histogram_ch", 24),
            "weekday": _hist(m, "weekday_histogram", 7),
        }
    return {"space": space, "stats": stats, "n": len(usable)}


def _days_between(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 86400.0


def compare(x: dict, y: dict) -> dict | None:
    """Score one pair. None when too few features are shared to mean anything."""
    diffs = [(a - b) for a, b in zip(x["vec"], y["vec"])
             if a is not None and b is not None]
    if len(diffs) < 6:
        return None

    # Root-mean-square z-difference: "on average these two are N standard
    # deviations apart per feature". Reported as well as the similarity,
    # because it is the number that can be argued with.
    distance = math.sqrt(sum(d * d for d in diffs) / len(diffs))
    style = math.exp(-distance)

    hour = _affinity(x["hour"], y["hour"])
    weekday = _affinity(x["weekday"], y["weekday"])
    parts = [v for v in (hour, weekday) if v is not None]
    rhythm = sum(parts) / len(parts) if parts else None
    score = style

    a, b = x["subject"], y["subject"]
    # Which of the two came first decides what a gap would mean.
    earlier, later = (a, b) if (a["first_seen"] or datetime.max.replace(tzinfo=None)) \
        <= (b["first_seen"] or datetime.max.replace(tzinfo=None)) else (b, a)
    overlap = _days_between(later["first_seen"], earlier["last_seen"])
    gap = None
    if overlap is not None and overlap < 0:
        gap, overlap = -overlap, 0.0

    # The feature-by-feature difference, so a match can be read rather than
    # trusted. Largest disagreements first: those are what would refute it.
    per_feature = sorted(
        ({"feature": f, "z_diff": round(abs(a_ - b_), 2)}
         for f, a_, b_ in zip(FEATURES, x["vec"], y["vec"])
         if a_ is not None and b_ is not None),
        key=lambda d: -d["z_diff"])

    return {
        "score": round(score, 4),
        "style": round(style, 4),
        "distance": round(distance, 3),
        "rhythm": None if rhythm is None else round(rhythm, 4),
        "rhythm_hour": None if hour is None else round(hour, 4),
        "rhythm_weekday": None if weekday is None else round(weekday, 4),
        "features_compared": len(diffs),
        "overlap_days": None if overlap is None else round(overlap, 1),
        "gap_days": None if gap is None else round(gap, 1),
        "per_feature": per_feature,
    }


def pairs(conn, *, community: str | None = None, min_comments: int = 5,
          limit: int = 200, succession_only: bool = False,
          cross_community: bool = False, sort: str = "score") -> dict:
    """Ranked pairs of subjects.

    `succession_only` keeps pairs whose activity never overlapped — the shape a
    changed nickname would leave. `cross_community` compares across comment
    backends, which asks a different question: not "did this person change
    nickname" but "is this the same person on both platforms", where two
    accounts genuinely exist and overlap proves nothing.
    """
    subjects = load_subjects(conn, community=community, min_comments=min_comments)
    built = build_space(subjects)
    space = built["space"]
    keys = list(space)

    out = []
    for i in range(len(keys)):
        xi = space[keys[i]]
        for j in range(i + 1, len(keys)):
            yj = space[keys[j]]
            if not cross_community and xi["subject"]["community"] != yj["subject"]["community"]:
                continue
            c = compare(xi, yj)
            if c is None:
                continue
            if succession_only and (c["overlap_days"] is None or c["overlap_days"] > 0):
                continue
            a, b = xi["subject"], yj["subject"]
            out.append({
                **c,
                "a": _brief(a), "b": _brief(b),
                "same_community": a["community"] == b["community"],
            })

    key = {"score": lambda p: -p["score"],
           "style": lambda p: -p["style"],
           "rhythm": lambda p: -(p["rhythm"] or 0),
           "gap": lambda p: (p["gap_days"] if p["gap_days"] is not None else 1e9),
           }.get(sort, lambda p: -p["score"])
    out.sort(key=key)
    return {"pairs": out[:limit], "compared": len(out), "subjects": built["n"],
            "features": list(FEATURES)}


def _brief(s: dict) -> dict:
    return {"kind": s["subject_kind"], "key": s["subject_key"], "label": s["label"],
            "community": s["community"], "n_comments": s["n_comments"],
            "first_seen": s["first_seen"].isoformat() if s["first_seen"] else None,
            "last_seen": s["last_seen"].isoformat() if s["last_seen"] else None}


def neighbours(conn, *, kind: str, key: str, community: str | None = None,
               min_comments: int = 5, limit: int = 25) -> dict:
    """The subjects closest to one given subject."""
    subjects = load_subjects(conn, community=community, min_comments=min_comments)
    space = build_space(subjects)["space"]
    me = next((v for k, v in space.items() if k[1] == kind and k[2] == key
               and (community is None or k[0] == community)), None)
    if me is None:
        return {"subject": None, "neighbours": []}
    out = []
    for k, other in space.items():
        if other is me:
            continue
        c = compare(me, other)
        if c:
            out.append({**c, "b": _brief(other["subject"])})
    out.sort(key=lambda p: -p["score"])
    return {"subject": _brief(me["subject"]), "neighbours": out[:limit]}


def timeline(conn, subjects: list[dict], *, bucket: str = "month") -> dict:
    """Monthly (or weekly) comment counts per subject, on one shared axis.

    Each entry of `subjects` is {"kind", "key", "community"}. The series are
    returned over a single list of buckets covering all of them, so two
    profiles can be drawn against the same time axis without the caller having
    to align anything.
    """
    trunc = "week" if bucket == "week" else "month"
    series, all_buckets = [], set()
    for s in subjects:
        kind, key, comm = s.get("kind"), s.get("key"), s.get("community")
        with conn.cursor() as cur:
            if kind == "persona":
                cur.execute(f"""
                    SELECT to_char(date_trunc('{trunc}', cs.posted_at), 'YYYY-MM-DD') AS b,
                           count(*)
                    FROM comment c
                    JOIN LATERAL (SELECT posted_at FROM comment_snapshot
                                  WHERE comment_id = c.id
                                  ORDER BY fetched_at DESC LIMIT 1) cs ON true
                    JOIN article a ON a.id = c.article_id
                    JOIN journal j ON j.id = a.journal_id
                    JOIN persona_alias pa ON pa.nick = c.author_nick
                                         AND pa.community = j.community
                    WHERE pa.persona_id = %s::bigint AND j.community = %s
                      AND cs.posted_at IS NOT NULL
                    GROUP BY 1 ORDER BY 1
                """, (key, comm))
            else:
                cur.execute(f"""
                    SELECT to_char(date_trunc('{trunc}', cs.posted_at), 'YYYY-MM-DD') AS b,
                           count(*)
                    FROM comment c
                    JOIN LATERAL (SELECT posted_at FROM comment_snapshot
                                  WHERE comment_id = c.id
                                  ORDER BY fetched_at DESC LIMIT 1) cs ON true
                    JOIN article a ON a.id = c.article_id
                    JOIN journal j ON j.id = a.journal_id
                    WHERE c.author_nick = %s AND j.community = %s
                      AND cs.posted_at IS NOT NULL
                    GROUP BY 1 ORDER BY 1
                """, (key, comm))
            counts = {b: int(n) for b, n in cur.fetchall()}
        all_buckets |= set(counts)
        series.append({"kind": kind, "key": key, "community": comm,
                       "label": s.get("label") or key, "counts": counts})

    buckets = sorted(all_buckets)
    return {"bucket": trunc, "buckets": buckets,
            "series": [{**s, "values": [s["counts"].get(b, 0) for b in buckets],
                        "counts": None} for s in series]}


# ---------------------------------------------------------------- calibration

def _confirmed_alias_subjects(conn, min_comments: int) -> list[dict]:
    """Confirmed personas split back into their separate nicknames.

    The only ground truth this corpus has. A persona is normally ONE subject —
    that is the point of the layer — so to test whether the proximity would
    have found the link, the aliases have to be measured apart again, exactly
    as two unrelated accounts would be.
    """
    from . import profiling as pf
    with conn.cursor() as cur:
        cur.execute("""
            SELECT j.community, c.author_nick, cs.posted_at, cs.body_text,
                   cs.like_count, pa.persona_id
            FROM comment c
            JOIN LATERAL (SELECT posted_at, body_text, like_count
                          FROM comment_snapshot WHERE comment_id = c.id
                          ORDER BY fetched_at DESC LIMIT 1) cs ON true
            JOIN article a ON a.id = c.article_id
            JOIN journal j ON j.id = a.journal_id
            JOIN persona_alias pa ON pa.nick = c.author_nick
                                 AND pa.community = j.community
            WHERE cs.body_text IS NOT NULL AND pa.confidence = 'confirmed'
        """)
        rows = cur.fetchall()

    per: dict = {}
    for comm, nick, posted, body, likes, pid in rows:
        per.setdefault((comm, nick, pid), []).append(
            {"posted_at": posted, "body_text": body, "like_count": likes})

    subs = []
    for (comm, nick, pid), cs in per.items():
        deduped, _ = pf._dedupe(cs)
        if len(deduped) < min_comments:
            continue
        dated = sorted(c["posted_at"] for c in deduped if c["posted_at"])
        subs.append({
            "community": comm, "subject_kind": "nick", "subject_key": nick,
            "label": nick, "persona_id": pid, "n_comments": len(deduped),
            "n_chars": sum(len(c["body_text"] or "") for c in deduped),
            "first_seen": dated[0] if dated else None,
            "last_seen": dated[-1] if dated else None,
            "metrics": pf.measure(deduped),
        })
    return subs


def _auc(pos: list[float], neg: list[float]) -> float | None:
    """Probability that a random same-person pair outranks a random other one.
    0.5 is a coin toss; 1.0 is perfect separation."""
    if not pos or not neg:
        return None
    wins = sum(1 for p in pos for n in neg if p > n)
    ties = sum(1 for p in pos for n in neg if p == n)
    return (wins + 0.5 * ties) / (len(pos) * len(neg))


def _quantile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(len(s) * q))]


def calibrate(conn, *, min_comments: int = 8) -> dict:
    """How well the score actually separates same-person pairs, measured now.

    Computed live rather than quoted from a commit message, so it moves as the
    ground truth grows: every persona the user confirms adds pairs to it. With
    a handful of confirmed personas the figure carries a wide error bar, and
    the caller is told how many pairs it rests on so it can say so.
    """
    truth = _confirmed_alias_subjects(conn, min_comments)
    population = load_subjects(conn, min_comments=min_comments)
    space = build_space(population + truth)["space"]
    index = {(t["community"], "nick", t["subject_key"]): t for t in truth}
    keys = [k for k in space if k in index]

    got = {"score": ([], []), "style": ([], []), "rhythm": ([], [])}
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = index[keys[i]], index[keys[j]]
            c = compare(space[keys[i]], space[keys[j]])
            if c is None:
                continue
            same = 0 if a["persona_id"] == b["persona_id"] else 1
            got["score"][same].append(c["score"])
            got["style"][same].append(c["style"])
            if c["rhythm"] is not None:
                got["rhythm"][same].append(c["rhythm"])

    out = {"min_comments": min_comments,
           "aliases": len(truth),
           "personas": len({t["persona_id"] for t in truth}),
           "same_pairs": len(got["score"][0]),
           "different_pairs": len(got["score"][1]),
           "signals": {}}
    for name, (pos, neg) in got.items():
        out["signals"][name] = {
            "auc": None if _auc(pos, neg) is None else round(_auc(pos, neg), 3),
            "same_median": None if not pos else round(_quantile(pos, 0.5), 3),
            "different_p99": None if not neg else round(_quantile(neg, 0.99), 3),
            "n_same": len(pos), "n_different": len(neg),
        }
    return out
