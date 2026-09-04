"""Measuring a rhetorical position without measuring the corpus instead."""
from datetime import datetime

from mediatracker import stance as st


def _c(text, *, year=2014, origin="wayback", journal="lematin", reply=False):
    return {"body_text": text, "posted_at": datetime(year, 6, 1),
            "origin": origin, "journal": journal, "is_reply": reply}


def _subject(comments, key="x"):
    return {"community": "lematin", "kind": "nick", "key": key,
            "label": key, "comments": comments}


def test_the_milieu_lexicon_follows_the_year():
    # The trap: a 2010s wordlist makes every modern writer look disengaged,
    # and a 2020s one does the same to a 2013 writer. Neither is a fact about
    # the writer.
    assert "chemtrails" in st.milieu_for(2013)
    assert "chemtrails" not in st.milieu_for(2024)
    assert "plandémie" in st.milieu_for(2024)
    assert "plandémie" not in st.milieu_for(2013)
    # Terms that never went away must be in both, or the eras stop being
    # comparable in the wrong direction.
    assert "complot" in st.milieu_for(2013) and "complot" in st.milieu_for(2024)


def test_an_undated_comment_is_scored_on_the_current_lexicon():
    assert st.milieu_for(None) is st.MILIEU_ERAS[-1][1]


def test_reply_baseline_is_split_by_journal_origin_and_year():
    subs = [_subject([_c("a", reply=True), _c("b"), _c("c")] * 20)]
    base = st.reply_baseline(subs)
    assert base[("lematin", "wayback", 2014)] == 20 / 60


def test_reply_baseline_ignores_cells_too_small_to_trust():
    subs = [_subject([_c("a", reply=True)] * 10)]
    assert st.reply_baseline(subs) == {}


def test_pdf_rows_never_enter_the_reply_baseline():
    # Printed pages carry no threading at all, so counting them would report
    # the parser's blind spot as everyone writing fewer replies.
    subs = [_subject([_c("a", origin="pdf")] * 100)]
    assert st.reply_baseline(subs) == {}


def test_reply_is_a_ratio_against_the_writer_s_own_rooms():
    # Same person, same behaviour, read from two routes that preserve
    # threading differently. The ratio must not notice the difference.
    baseline = {("lematin", "wayback", 2014): 0.25,
                ("lematin", "sitemap", 2014): 0.50}
    archived = _subject([_c("word " * 200, reply=(i % 4 == 0))
                         for i in range(40)])
    live = _subject([_c("word " * 200, origin="sitemap", reply=(i % 2 == 0))
                     for i in range(40)])
    a = st.measure(archived, baseline)
    b = st.measure(live, baseline)
    assert abs(a["reply"] - 1.0) < 0.01
    assert abs(b["reply"] - 1.0) < 0.01


def test_a_writer_with_no_threaded_comments_has_no_reply_signal():
    # Missing, not zero. Scoring it zero would rank the least observable
    # writers as the least argumentative ones.
    subj = _subject([_c("word " * 200, origin="pdf") for _ in range(60)])
    m = st.measure(subj, {})
    assert m is not None
    assert m["reply"] is None


def test_too_little_text_is_not_measured_at_all():
    assert st.measure(_subject([_c("trois petits mots")]), {}) is None


def test_rates_are_per_thousand_words_not_per_comment():
    long = _subject([_c(("vous " + "mot " * 99) * 30)])
    short = _subject([_c("vous " + "mot " * 99) for _ in range(30)])
    a, b = st.measure(long, {}), st.measure(short, {})
    assert abs(a["address"] - b["address"]) < 0.01


def test_shrinkage_pulls_a_thin_sample_toward_the_mean():
    # A rate per 1,000 words from 3,000 words has many times the sampling
    # spread of the same rate from 700,000. Without this the tails of every
    # axis fill with small accounts and the ranking measures sample size.
    mu, sd = 2.0, 1.5
    thin = st._shrink(8.0, exposure=3.0, mu=mu, sd=sd)
    thick = st._shrink(8.0, exposure=700.0, mu=mu, sd=sd)
    assert mu < thin < thick < 8.0
    assert (thick - mu) > (thin - mu)


def test_shrinkage_leaves_a_mean_rate_alone():
    assert st._shrink(2.0, exposure=5.0, mu=2.0, sd=1.5) == 2.0


def test_no_exposure_collapses_to_the_mean():
    assert st._shrink(9.0, exposure=0.0, mu=2.0, sd=1.5) == 2.0


def test_reply_exposure_is_counted_in_comments_and_the_rest_in_words():
    r = {"n_words": 50_000, "n_threaded": 40}
    assert st._exposure(r, "reply") == 40.0
    assert st._exposure(r, "address") == 50.0


def test_an_unmeasured_axis_counts_as_typical_rather_than_absent():
    # Averaging over only the axes that exist quietly rewards a subject whose
    # missing axis would have been a weak one.
    rows = st.build([_subject([_c("vous " + "mot " * 99, origin="pdf")
                               for _ in range(60)], key=f"s{i}")
                     for i in range(30)])
    assert rows
    for r in rows:
        assert r["z"].get("reply") is None
        assert r["n_axes"] == len(st.AXES) - 1
        assert r["stance"] == sum(r["z"].values()) / len(st.AXES)
