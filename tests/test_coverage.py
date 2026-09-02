"""Deciding when a denominator is good enough to draw a band from."""
from collections import Counter

from mediatracker import coverage as cv


def test_a_month_needs_most_of_its_days_mirrored():
    assert cv._month_is_mirrored("2013-06", 30)
    assert cv._month_is_mirrored("2013-06", 24)        # 80% of 30
    assert not cv._month_is_mirrored("2013-06", 12)
    # The trap this exists for: Le Matin's mirror holds 31 scattered files for
    # all of 2011, so November's count is two articles. Drawing 0% coverage
    # from that would put a confident black band over a month nobody measured.
    assert not cv._month_is_mirrored("2011-11", 1)


def test_a_mirror_without_day_files_is_not_second_guessed():
    # The sisters date each record, so there are no days to count. None means
    # "not applicable", which must not read as zero days seen.
    assert cv._month_is_mirrored("2013-06", None)


def test_a_month_far_below_the_title_s_normal_output_is_not_a_denominator():
    typical = 2000.0
    assert cv._month_is_plausible(2100, typical)
    assert cv._month_is_plausible(500, typical)        # exactly 25%
    assert not cv._month_is_plausible(130, typical)    # the sisters' 2011
    assert not cv._month_is_plausible(0, typical)


def test_no_typical_volume_disables_the_volume_guard():
    # A title with no mirror at all must fall through to the archive basis
    # rather than have every month rejected by a zero baseline.
    assert cv._month_is_plausible(5, 0.0)


def test_the_typical_month_is_the_median_not_the_mean():
    # One 20,000-article month must not raise the bar for every other month.
    pub = Counter({"2013-01": 2000, "2013-02": 2100, "2013-03": 1900,
                   "2013-04": 20000})
    assert cv._typical_month(pub) == 2100
    assert cv._typical_month(Counter()) == 0.0


def test_empty_months_are_ignored_when_taking_the_median():
    pub = Counter({"a": 0, "b": 0, "c": 2000, "d": 2200})
    assert cv._typical_month(pub) == 2200


def test_live_outranks_any_fraction():
    # A live-scanned month is the only kind where absence means absence, so it
    # wins even against a better-looking number from another title.
    live = {"live": True, "coverage": 0.30}
    archived = {"live": False, "coverage": 0.99}
    assert cv._rank(live) > cv._rank(archived)


def test_a_known_fraction_outranks_an_unknown_one():
    known = {"live": False, "coverage": 0.0}
    unknown = {"live": False, "coverage": None}
    assert cv._rank(known) > cv._rank(unknown)
