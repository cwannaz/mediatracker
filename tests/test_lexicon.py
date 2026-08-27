"""The n-gram fingerprint: the two artefacts, and the statistic.

Each test here exists because the thing it checks produced a wrong answer
during development, not because the function looked testable.
"""
from datetime import datetime, timedelta, timezone

from mediatracker import lexicon as lx


def _comments(texts, start=None):
    t0 = start or datetime(2026, 8, 1, tzinfo=timezone.utc)
    return [{"body_text": t, "posted_at": t0 + timedelta(hours=i)}
            for i, t in enumerate(texts)]


def test_mentions_and_urls_never_reach_the_index():
    # A match between two strangers answering the same commenter was once the
    # strongest result this produced: every n-gram driving it was a fragment of
    # the handle they had both typed. A handle is who you answer, not how you
    # write.
    t = lx.normalise("@Fluide-Glacial voyez https://x.test/a — d'accord")
    assert "fluide" not in t and "glacial" not in t
    assert "x.test" not in t and "http" not in t
    assert "d'accord" in t


def test_only_the_recent_tail_is_kept_and_undated_text_is_dropped():
    # Size was the other artefact: uncapped, the corpus's longest profile
    # headed the ranking for five unrelated probes out of five.
    long_ago = _comments(["z" * 5000], start=datetime(2020, 1, 1, tzinfo=timezone.utc))
    recent = _comments(["a" * 2000], start=datetime(2026, 8, 1, tzinfo=timezone.utc))
    undated = [{"body_text": "q" * 4000, "posted_at": None}]
    text = lx.tail(long_ago + recent + undated, cap=2500)
    assert "q" not in text                      # undated cannot be placed in time
    assert text.count("a") == 2000              # the recent end is the kept end
    assert len(text) <= 2500 + 5000             # and the old bulk is not all pulled in
    assert lx.tail([], cap=100) == ""


def test_a_shared_habit_beats_a_shared_subject():
    # Two people writing about the same thing must not outrank one person's own
    # two halves. The habit here is a spaced ellipsis, the subject is a name.
    habit_a = _comments(["il faut voir ... enfin bon ... c'est ainsi"] * 4)
    habit_b = _comments(["on verra ... malgré tout ... rien de neuf"] * 4)
    topic_1 = _comments(["dittli quitte le centre, dittli fonde un parti"] * 4)
    topic_2 = _comments(["dittli et le centre, dittli encore et toujours"] * 4)
    subs = [{"community": "c", "subject_kind": "nick", "subject_key": k,
             "comments": v}
            for k, v in (("habit_a", habit_a), ("habit_b", habit_b),
                         ("topic_1", topic_1), ("topic_2", topic_2))]
    ix = lx.build(subs)
    k = lambda n: ("c", "nick", n)
    assert ix.similarity(k("habit_a"), k("habit_b")) > 0
    assert ix.drivers(k("habit_a"), k("habit_b"))     # and it can say why


def test_drivers_name_what_the_match_rests_on():
    a = _comments(["hmm, vraiment ?!? je crois pas"] * 3)
    b = _comments(["hmm, sérieux ?!? on verra bien"] * 3)
    c = _comments(["le budget augmente et les soins reculent"] * 3)
    subs = [{"community": "x", "subject_kind": "nick", "subject_key": n, "comments": v}
            for n, v in (("a", a), ("b", b), ("c", c))]
    ix = lx.build(subs)
    d = ix.drivers(("x", "nick", "a"), ("x", "nick", "b"))
    assert any("hmm" in g for g in d) or any("?!?" in g for g in d)
    assert ix.similarity(("x", "nick", "a"), ("x", "nick", "b")) > \
           ix.similarity(("x", "nick", "a"), ("x", "nick", "c"))


def test_standout_measures_against_what_a_field_that_size_is_worth():
    # The maximum of many draws is high by construction. A lift that does not
    # beat sqrt(2 ln n) is what a coincidence looks like, and must read as one.
    flat = [0.5 + i * 1e-6 for i in range(400)]
    s = lx.standout(flat)
    assert s["chance"] > 3 and s["excess"] < 0

    spike = [0.9] + [0.1 + i * 1e-4 for i in range(400)]
    assert lx.standout(spike)["excess"] > 0

    assert lx.standout([0.4] * 50)["lift"] is None      # no spread to divide by
    assert lx.standout([0.9, 0.1])["lift"] is None      # field too small to judge


def test_ranking_reports_the_field_it_beat_and_can_be_filtered():
    subs = [{"community": "c", "subject_kind": "nick", "subject_key": f"s{i}",
             "comments": _comments([f"texte numero {i} avec des mots {i}"] * 3)}
            for i in range(30)]
    ix = lx.build(subs)
    full = ix.rank(("c", "nick", "s0"))
    assert full["field"] == 29 and full["excess"] is not None
    # A succession search only considers accounts that had already gone quiet,
    # and the standout figure has to describe that field, not the whole corpus.
    half = ix.rank(("c", "nick", "s0"),
                   eligible=lambda k: int(k[2][1:]) % 2 == 0)
    assert half["field"] < full["field"]
    assert lx.MIN_CHARS < lx.TAIL_CHARS
