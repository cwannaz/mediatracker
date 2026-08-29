"""Handles built from the same letters — and the three ways that means nothing."""
from mediatracker import anagrams as ag


def _rows(*nicks, community="lematin"):
    return [{"nick": n, "community": community} for n in nicks]


def test_a_reordering_somebody_had_to_build_is_reported():
    got = ag.find(_rows("PierreErreip", "erreippierre1"))
    assert got["PierreErreip"]["partners"] == ["erreippierre1"]
    # and the construction itself is named: Erreip is Pierre backwards
    assert got["PierreErreip"]["built_from"] == "pierre"


def test_one_handle_spelled_backwards_is_called_a_reversal():
    got = ag.find(_rows("Talion.", "Noilat"))
    assert got["Talion."]["kind"] == "reversal"
    assert got["Noilat"]["partners"] == ["Talion."]


def test_a_reversal_is_admissible_shorter_than_a_plain_anagram():
    # "talion" is six letters, below MIN_LETTERS. Sharing a letter multiset at
    # that length is a coincidence; being the reverse of another handle is not.
    assert len(ag.letters("Talion.")) < ag.MIN_LETTERS
    assert ag.is_reversal("Talion.", "Noilat")


def test_the_same_word_with_different_decoration_is_not_an_anagram():
    # `Caribou 13` / `Caribou0427` share every letter in the same order.
    for pair in (("Caribou 13", "Caribou0427"), ("Lolowin", "Lolowin1260"),
                 ("magnumforce", "Magnumforce44"), ("François", "françois002")):
        assert ag.find(_rows(*pair)) == {}, pair


def test_a_forename_and_surname_in_the_other_order_is_not_reported():
    # The 2012 sign-up form took two boxes and did not care which went where.
    for pair in (("herve tinguely", "tinguely herve"),
                 ("rochat georgette", "georgette rochat"),
                 ("Nom Prénom", "Prénom Nom")):
        assert ag.find(_rows(*pair)) == {}, pair


def test_the_swap_is_caught_even_when_one_side_dropped_the_separator():
    # `ruiz.alfredo` / `alfredoruiz` is the same artefact wearing one separator.
    assert ag.find(_rows("ruiz.alfredo", "alfredoruiz")) == {}


def test_but_an_unsplit_pair_is_left_for_a_reader_to_judge():
    # Nothing in `guydebord` says where the words end, so it is reported
    # rather than silently dropped on a guess about its two halves.
    assert "guydebord" in ag.find(_rows("guydebord", "debordguy"))


def test_a_typo_is_left_to_alias_candidates():
    # `La pas comrpis` is one transposition away and already turns up there.
    assert ag.find(_rows("La pas compris", "La pas comrpis")) == {}


def test_a_handle_that_mirrors_itself_needs_no_partner():
    got = ag.find(_rows("n3tfl1xx1lft3n"))
    assert got["n3tfl1xx1lft3n"]["kind"] == "mirror"
    assert got["n3tfl1xx1lft3n"]["built_from"] == "ntflx"
    # ...but a run of one repeated letter is not a construction
    assert ag.mirror_of("oooo oooo") is None


def test_letters_are_only_compared_inside_one_community():
    # The same string in two comment backends is two people until something
    # says otherwise, so their letters are not each other's evidence. A
    # self-mirror still reads, because that is a property of one handle.
    rows = (_rows("Talion.") + _rows("Noilat", community="tx-romandie"))
    assert ag.find(rows) == {}
    rows = (_rows("PierreErreip") +
            _rows("erreippierre1", community="tx-romandie"))
    assert all(not v["partners"] for v in ag.find(rows).values())


def test_short_handles_are_not_matched_at_all():
    assert ag.find(_rows("abc", "cba")) == {}
    assert ag.signature("Alain Deloin") == ag.signature("Deloin Alain")
