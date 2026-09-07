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


# --------------------------------------------------------------------------- #
# query grammar: quoted phrases and ~ exclusions
# --------------------------------------------------------------------------- #

def test_bare_words_are_separate_terms():
    p = s.parse_terms("grand remplacement")
    assert p["terms"] == ["grand", "remplacement"]
    assert p["phrases"] == []


def test_quotes_make_one_block():
    # Without this the two words are ANDed, and an article using them a
    # paragraph apart matches -- a different claim about the corpus.
    p = s.parse_terms('"grand remplacement"')
    assert p["phrases"] == ["grand remplacement"]
    assert p["terms"] == []


def test_a_tilde_excludes_a_word():
    p = s.parse_terms("chat ~chien")
    assert p["terms"] == ["chat"]
    assert p["not_terms"] == ["chien"]


def test_a_tilde_before_a_quote_excludes_the_whole_phrase():
    # Quoting binds tighter than negation: this must not exclude "petit" and
    # then search for "chat".
    p = s.parse_terms('~"petit chat"')
    assert p["not_phrases"] == ["petit chat"]
    assert p["terms"] == [] and p["not_terms"] == []


def test_phrases_terms_and_exclusions_mix():
    p = s.parse_terms('geneve "conseil federal" ~vaud ~"petit chat"')
    assert p["terms"] == ["geneve"]
    assert p["phrases"] == ["conseil federal"]
    assert p["not_terms"] == ["vaud"]
    assert p["not_phrases"] == ["petit chat"]


def test_a_lone_tilde_is_not_a_term():
    assert s.parse_terms("~") == {"phrases": [], "terms": [],
                                  "not_phrases": [], "not_terms": []}


def test_empty_quotes_are_dropped():
    assert s.parse_terms('""').get("phrases") == []


def test_inner_spacing_does_not_change_a_phrase():
    # "regardless of the spaces": the block is the words, not the whitespace.
    a = s.parse_terms('"grand remplacement"')["phrases"][0]
    b = s.parse_terms('"grand    remplacement"')["phrases"][0]
    assert a.split() == b.split()


def test_the_builder_emits_adjacency_for_a_phrase():
    params = {}
    sql = s._query_sql(s.parse_terms('"grand remplacement"'),
                       "'french_ua'::regconfig", params)
    assert "phraseto_tsquery" in sql
    assert params["p0"] == "grand remplacement"


def test_the_builder_emits_negation_for_an_exclusion():
    params = {}
    sql = s._query_sql(s.parse_terms("chat ~chien"), "'french_ua'::regconfig", params)
    assert "!!plainto_tsquery" in sql
    assert params["nt0"] == "chien"


def test_the_positive_only_build_drops_exclusions():
    # ts_headline must not be asked to mark a term that is by definition
    # absent, or the snippet is chosen for the wrong reason.
    params = {}
    sql = s._query_sql(s.parse_terms("chat ~chien"), "'french_ua'::regconfig",
                       params, positive_only=True)
    assert "!!" not in sql
    assert "nt0" not in params


def test_a_query_of_only_stopwords_builds_nothing():
    # Returns None so the caller can fall back rather than emit "()".
    assert s._query_sql({"phrases": [], "terms": [], "not_phrases": [],
                         "not_terms": []}, "'french_ua'::regconfig", {}) is None


def test_every_typed_value_is_a_bound_parameter():
    # The grammar takes arbitrary text from a search box; none of it may reach
    # SQL as literal text.
    params = {}
    sql = s._query_sql(s.parse_terms("""a'b "c'd" ~e'f"""),
                       "'french_ua'::regconfig", params)
    assert "'" not in sql.replace("'french_ua'::regconfig", "")
    assert any("'" in v for v in params.values())
