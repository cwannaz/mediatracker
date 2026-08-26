"""Proximity: the arithmetic, not the database.

The interesting failures here are silent ones — a similarity that ranks
identical writers below unrelated ones, a length correction that does nothing,
an overlap computed with the wrong sign. Each is checked directly.
"""
from datetime import datetime, timedelta, timezone

from mediatracker import proximity as px


# Standardisation needs a population that actually varies — a feature identical
# across every subject carries no information and is dropped by design, so
# fixtures spread each measure the way a real community does.
_SPREAD = (
    ("avg_word_length", 4.5, 0.4), ("avg_words_per_comment", 40.0, 9.0),
    ("avg_sentence_length_words", 18.0, 3.0), ("vocabulary_richness_ttr", 0.30, 0.04),
    ("hapax_ratio", 0.60, 0.05), ("accented_word_rate", 0.10, 0.02),
    ("accent_consistency", 0.90, 0.03), ("all_caps_word_rate", 0.002, 0.001),
    ("ellipsis_per_comment", 0.10, 0.05), ("questions_per_comment", 0.20, 0.07),
    ("exclamations_per_comment", 0.15, 0.06),
    ("apostrophe_typographic_share", 0.30, 0.10),
    ("space_before_punctuation_per_comment", 0.40, 0.12),
)


def _subject(key, *, community="c", n=50, chars=5000, first=None, last=None,
             jitter=0, **metrics):
    base = {f: base_v + jitter * step for f, base_v, step in _SPREAD}
    base.update(metrics)
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return {"community": community, "subject_kind": "nick", "subject_key": key,
            "label": key, "n_comments": n, "n_chars": chars,
            "first_seen": first or t0, "last_seen": last or (t0 + timedelta(days=100)),
            "metrics": base}


def _space(subjects):
    return px.build_space(subjects)["space"]


def test_identical_writers_score_higher_than_different_ones():
    a = _subject("a")
    b = _subject("b")                              # same metrics as a
    others = [_subject(f"o{i}", jitter=i + 1) for i in range(6)]
    sp = _space([a, b, *others])
    same = px.compare(sp[("c", "nick", "a")], sp[("c", "nick", "b")])
    diff = px.compare(sp[("c", "nick", "a")], sp[("c", "nick", "o5")])
    assert same["distance"] == 0.0
    assert same["score"] > diff["score"]


def test_score_is_style_only_and_rhythm_does_not_move_it():
    # Two subjects identical in style, wildly different in posting hours. The
    # ranking must not notice: rhythm is reported, never blended in.
    a = _subject("a")
    b = _subject("b")
    a["metrics"]["hour_histogram_ch"] = {str(h): (10 if h < 4 else 0) for h in range(24)}
    b["metrics"]["hour_histogram_ch"] = {str(h): (10 if h >= 20 else 0) for h in range(24)}
    sp = _space([a, b, *[_subject(f"o{i}", jitter=i + 1) for i in range(6)]])
    r = px.compare(sp[("c", "nick", "a")], sp[("c", "nick", "b")])
    assert r["rhythm"] == 0.0            # disjoint hours
    assert r["score"] == r["style"]      # and the score is untouched by it


def test_length_sensitive_features_are_residualised():
    # Richness that falls with corpus size is the artefact being removed: three
    # writers on the same downward line must end up equally distant from it.
    subs = []
    for i, (chars, ttr) in enumerate([(1_000, 0.60), (10_000, 0.40), (100_000, 0.20)]):
        subs.append(_subject(f"s{i}", jitter=i, chars=chars,
                             vocabulary_richness_ttr=ttr, hapax_ratio=ttr + 0.2))
    space = _space(subs)
    idx = px.FEATURES.index("vocabulary_richness_ttr")
    zs = [space[("c", "nick", f"s{i}")]["vec"][idx] for i in range(3)]
    # Perfectly collinear in log(chars), so nothing is left once the size
    # effect is removed. The feature must drop out rather than be standardised:
    # its residuals are floating-point dust, and dividing those by their own
    # spread would manufacture a standard deviation of difference from nothing.
    assert zs == [None, None, None]


def test_a_constant_feature_cannot_manufacture_difference():
    # Every subject silent in ALL CAPS. That must not separate anybody.
    subs = [_subject(f"s{i}", jitter=i, all_caps_word_rate=0.0) for i in range(4)]
    space = _space(subs)
    idx = px.FEATURES.index("all_caps_word_rate")
    assert all(space[("c", "nick", f"s{i}")]["vec"][idx] is None for i in range(4))


def test_overlap_and_gap_are_exclusive_and_correctly_signed():
    t0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
    early = _subject("early", first=t0, last=t0 + timedelta(days=100))
    later = _subject("later", jitter=1, first=t0 + timedelta(days=300),
                     last=t0 + timedelta(days=400))
    overlapping = _subject("over", jitter=2, first=t0 + timedelta(days=50),
                           last=t0 + timedelta(days=150))
    sp = _space([early, later, overlapping,
                 *[_subject(f"o{i}", jitter=i + 3) for i in range(5)]])

    succ = px.compare(sp[("c", "nick", "early")], sp[("c", "nick", "later")])
    assert succ["gap_days"] == 200.0 and succ["overlap_days"] == 0.0

    over = px.compare(sp[("c", "nick", "early")], sp[("c", "nick", "over")])
    assert over["gap_days"] is None and over["overlap_days"] == 50.0

    # Argument order must not change the answer.
    flipped = px.compare(sp[("c", "nick", "later")], sp[("c", "nick", "early")])
    assert flipped["gap_days"] == succ["gap_days"]


def test_affinity_bounds():
    flat = [1 / 4] * 4
    assert px._affinity(flat, flat) == 1.0
    assert px._affinity([1, 0, 0, 0], [0, 1, 0, 0]) == 0.0
    assert px._affinity(None, flat) is None


def test_auc_reads_as_a_probability():
    assert px._auc([1.0, 0.9], [0.1, 0.2]) == 1.0      # perfect separation
    assert px._auc([0.1, 0.2], [1.0, 0.9]) == 0.0      # perfectly inverted
    assert px._auc([0.5], [0.5]) == 0.5                # a tie is a coin toss
    assert px._auc([], [0.5]) is None
