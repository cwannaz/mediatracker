"""New profiles: what counts as an arrival, and what an arrival is worth.

Every assertion here is about the thing that makes this module easy to get
wrong — that "new" describes our observation, not the site. A profile arriving
on the day the crawl started is not news; an account that stopped before the
crawl started did not disappear.
"""
from datetime import date, datetime, timedelta, timezone

from mediatracker import newcomers as nc
from mediatracker import proximity as px

CH = nc.pf.CH


def _daily(pairs):
    from collections import Counter
    return Counter({date.fromisoformat(d): n for d, n in pairs})


def _row(key, days, **kw):
    """A subject reduced to what coverage/evidence actually read."""
    d = _daily(days)
    first = datetime.combine(min(d), datetime.min.time(), tzinfo=timezone.utc) \
        + timedelta(hours=12)
    last = datetime.combine(max(d), datetime.min.time(), tzinfo=timezone.utc) \
        + timedelta(hours=12)
    return {"community": "c", "subject_kind": "nick", "subject_key": key,
            "label": key, "n_comments": sum(d.values()), "n_chars": 100 * sum(d.values()),
            "first_seen": kw.get("first", first), "last_seen": kw.get("last", last),
            "metrics": {}, "daily": d}


def test_coverage_starts_where_the_crawl_got_dense():
    # Two thin days of archive, then five days of real crawling. The dense run
    # must begin at the crawl, not at the first comment we happen to hold.
    rows = [_row("a", [("2026-08-01", 3), ("2026-08-10", 5)]),
            _row("b", [(f"2026-08-2{i}", 400) for i in range(3, 8)])]
    cov = nc.coverage(rows)
    assert cov["dense_from"] == "2026-08-23"
    assert cov["dense_days"] == 5


def test_a_missing_day_breaks_the_dense_run():
    # A day with nothing at all is the crawl stopping, not the site going
    # silent — treating it as observation would invent disappearances.
    rows = [_row("a", [("2026-08-20", 400), ("2026-08-21", 400),
                       ("2026-08-23", 400), ("2026-08-24", 400)])]
    assert nc.coverage(rows)["dense_from"] == "2026-08-23"


def test_arriving_on_day_one_is_worth_nothing():
    rows = [_row("old", [(f"2026-08-2{i}", 300) for i in range(3, 8)]),
            _row("dayone", [("2026-08-23", 5)]),
            _row("later", [("2026-08-26", 5)])]
    cov = nc.coverage(rows)
    from collections import Counter
    total = Counter()
    for r in rows:
        total.update(r["daily"])

    day_one = nc._evidence(rows[1], cov, total)
    assert day_one == {"absent_days": 0, "absent_comments": 0}

    later = nc._evidence(rows[2], cov, total)
    assert later["absent_days"] == 3            # 23, 24, 25
    assert later["absent_comments"] > 900       # and that much went past without it


# ------------------------------------------------------------------ ranking

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


def _subject(key, first, last, *, jitter=0):
    def ts(d):
        return datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
    return {"community": "c", "subject_kind": "nick", "subject_key": key, "label": key,
            "n_comments": 20, "n_chars": 4000, "first_seen": ts(first), "last_seen": ts(last),
            "metrics": {f: v + jitter * step for f, v, step in _SPREAD}}


def _space(subjects):
    return px.build_space(subjects)["space"]


def test_an_account_still_posting_is_not_a_predecessor():
    # Identical style, but it never stopped: a rename is a disappearance and
    # an appearance, and this candidate supplies only half of that.
    arrival = _subject("new", "2026-08-25", "2026-08-27")
    twin_live = _subject("twin", "2026-01-01", "2026-08-26")      # overlaps
    twin_gone = _subject("gone", "2026-01-01", "2026-08-20")      # quiet first
    space = _space([arrival, twin_live, twin_gone,
                    *[_subject(f"o{i}", "2025-01-01", "2025-06-01", jitter=i + 1)
                      for i in range(6)]])
    me = space[("c", "nick", "new")]
    out = nc.rank(space, me, cut=me["subject"]["first_seen"] - timedelta(hours=12),
                  dense_from=date(2026, 8, 23))
    keys = [c["b"]["key"] for c in out]
    assert "twin" not in keys
    assert out[0]["b"]["key"] == "gone"


def test_a_silence_before_the_crawl_is_labelled_as_ours():
    arrival = _subject("new", "2026-08-25", "2026-08-27")
    watched = _subject("watched", "2026-08-23", "2026-08-24")
    before = _subject("before", "2024-01-01", "2024-06-01", jitter=1)
    space = _space([arrival, watched, before,
                    *[_subject(f"o{i}", "2025-01-01", "2025-06-01", jitter=i + 2)
                      for i in range(6)]])
    me = space[("c", "nick", "new")]
    out = nc.rank(space, me, cut=me["subject"]["first_seen"],
                  dense_from=date(2026, 8, 23))
    label = {c["b"]["key"]: c["disappearance"] for c in out}
    assert label["watched"] == "observed"
    assert label["before"] == "unobserved"

    only = nc.rank(space, me, cut=me["subject"]["first_seen"],
                   dense_from=date(2026, 8, 23), observed_only=True)
    assert [c["b"]["key"] for c in only] == ["watched"]


def test_lift_is_silent_on_a_small_field_and_flat_on_a_flat_one():
    assert nc._lift([{"score": 0.9}] * 5)["lift"] is None      # too few to divide by
    assert nc._lift([{"score": 0.5}] * 30)["lift"] is None     # no spread at all
    flat = [{"score": 0.50 + i * 0.0001} for i in range(30)][::-1]
    tall = [{"score": 0.9}] + [{"score": 0.5}] * 29
    assert nc._lift(tall)["lift"] > nc._lift(flat)["lift"]
    # And the field size sets what a coincidence already buys.
    assert nc._lift(tall)["excess"] == round(nc._lift(tall)["lift"] - nc._lift(tall)["chance"], 2)
