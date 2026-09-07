"""Which literals a regex actually guarantees.

Every test here is about the same failure: a prefilter that excludes a row the
regex would have matched. It is the worst kind of search bug because it is
invisible -- the reader gets a shorter list, not an error -- so the rule is
that a literal is only usable if EVERY string matching the pattern contains
it. When in doubt the extractor must return nothing and let the scan run.
"""
import re

import pytest

from mediatracker import search as s


def _guaranteed(pattern, literal, samples):
    """Every sample matching `pattern` must contain `literal`."""
    rx = re.compile(pattern, re.I)
    return all(literal.lower() in t.lower()
               for t in samples if rx.search(t))


def test_a_plain_word_is_guaranteed():
    assert s.literals_from_regex("Schwab") == ["Schwab"]


def test_a_trailing_optional_character_is_dropped():
    # "chemtrails?" matches "chemtrail", which has no final s.
    assert s.literals_from_regex("chemtrails?") == ["chemtrail"]


def test_a_zero_repetition_bound_drops_the_character():
    assert s.literals_from_regex("plandemie{0,2}") == ["plandemi"]


def test_a_character_class_does_not_fuse_its_neighbours():
    # "Gen[eè]ve" must never yield "Genve", which appears nowhere.
    assert "Genve" not in s.literals_from_regex("Gen[eè]ve")


def test_an_optional_group_guarantees_nothing_inside_it():
    # The bug this file exists for: prefiltering on "grand" would have hidden
    # every plain "remplacement" in the corpus.
    got = s.literals_from_regex("(?:grand )?remplacement")
    assert got == ["remplacement"]
    assert _guaranteed("(?:grand )?remplacement", "remplacement",
                       ["le remplacement", "le grand remplacement"])
    assert not _guaranteed("(?:grand )?remplacement", "grand",
                           ["le remplacement", "le grand remplacement"])


def test_alternation_inside_a_group_keeps_the_stem_outside_it():
    assert s.literals_from_regex("complot(isme|iste)") == ["complot"]


def test_top_level_alternation_guarantees_nothing():
    assert s.literals_from_regex("chemtrail|contrail") == []


def test_a_pattern_of_pure_metacharacters_yields_nothing():
    assert s.literals_from_regex(r"^\d+$") == []
    assert s.literals_from_regex(r"\s{2,}") == []


def test_a_repeated_group_is_still_guaranteed_once():
    # "+" is one-or-more, so the contents must appear.
    assert s.literals_from_regex("(Davos )+forum") == ["Davos", "forum"]


def test_a_wildcard_between_runs_keeps_both():
    assert s.literals_from_regex("vaccin.*obligatoire") == ["vaccin", "obligatoire"]


def test_escaped_metacharacters_do_not_become_literals():
    # \b is a word boundary, not the letter b.
    assert s.literals_from_regex(r"\bWEF\b") == ["WEF"]


def test_runs_shorter_than_a_trigram_are_not_worth_probing():
    assert s.literals_from_regex("de") == []
    assert s.literals_from_regex("ab?c") == []


@pytest.mark.parametrize("pattern", [
    "chemtrails?", "(?:grand )?remplacement", "complot(isme|iste)",
    r"\bWEF\b", "vaccin.*obligatoire", "Gen[eè]ve", "(Davos )+forum",
    "plandemie{0,2}", "polic(iers|iere)",
])
def test_every_extracted_literal_really_is_mandatory(pattern):
    """Property: generate strings that match, and check the claim holds.

    A unit test per pattern proves the cases I thought of. This proves the
    claim itself on text drawn from the corpus's own vocabulary.
    """
    corpus = [
        "le grand remplacement", "un remplacement", "chemtrail", "chemtrails",
        "complotisme", "complotiste", "le WEF de Davos", "Genève", "Geneve",
        "Davos Davos forum", "plandemi", "plandemie", "policiers", "policiere",
        "vaccin rendu obligatoire", "vaccination obligatoire imposée",
    ]
    for lit in s.literals_from_regex(pattern):
        assert _guaranteed(pattern, lit, corpus), (
            f"{lit!r} is not guaranteed by {pattern!r}")


def test_the_configuration_switch_is_a_literal_regconfig():
    # Generated columns require an immutable expression, and to_tsvector is
    # immutable only when its configuration is a literal rather than a lookup.
    sql = s._config_case("lang")
    assert "::regconfig" in sql
    assert "%s" not in sql
