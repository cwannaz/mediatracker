"""Reading what a commenter said about themselves, and not what they didn't."""
from datetime import datetime

from mediatracker import disclosures as d


def _c(text, *, year=2014):
    return {"body_text": text, "posted_at": datetime(year, 6, 1),
            "journal": "lematin", "author_nick": "x"}


def test_a_plain_statement_is_a_disclosure():
    assert d.find("Je suis médecin depuis vingt ans.") == [
        ("occupation", "Je suis médecin depuis vingt ans.")]


def test_negation_is_not_a_disclosure():
    # Matches the occupation pattern and asserts the opposite of it.
    assert d.find("Je ne suis pas médecin, mais je lis.") == []
    assert d.find("Je ne suis plus enseignant depuis 2019.") == []


def test_a_hypothetical_is_an_argument_not_a_circumstance():
    assert d.find("Si j'étais retraité, je voyagerais.") == []
    assert d.find("Quand je serai à la retraite, j'habiterai au Portugal.") == []
    assert d.find("Imaginez que j'habite à Genève: le loyer me tuerait.") == []


def test_a_disclosure_inside_quotation_marks_belongs_to_someone_else():
    # This population argues by quoting each other back. Attributing the
    # quoted person's circumstances to the quoter is the one error that would
    # put words in someone's mouth on a page headed with their name.
    assert d.find("Il a dit « je suis médecin » et on l'a cru.") == []
    assert d.find('Vous écrivez "je suis propriétaire", donc vous payez.') == []


def test_stripping_quotes_preserves_offsets():
    # Offsets must survive, or the sentence returned is taken from the wrong
    # place in the comment.
    text = "Avant « cité » après."
    assert len(d.strip_quoted(text)) == len(text)


def test_several_dimensions_in_one_sentence_are_all_reported():
    got = dict(d.find("J'ai 62 ans et j'habite à Vevey depuis 1998."))
    assert set(got) == {"age", "housing"}


def test_a_category_fires_once_per_comment():
    # Otherwise a subject who says "ma femme" six times looks six times more
    # disclosing than one who said it once.
    text = "Ma femme et mon fils. Ma fille aussi. Mes parents encore."
    assert [k for k, _ in d.find(text)] == ["household"]


def test_the_quote_always_contains_what_triggered_it():
    # Trimming a long sentence from its start is how a quote ends up without
    # the phrase that produced it.
    filler = "le contexte reste discutable et " * 12
    got = d.find("Alors que " + filler + "je suis retraité depuis 2011.")
    assert got and "retraité" in got[0][1]
    assert len(got[0][1]) <= d.MAX_QUOTE + 2      # room for the ellipses


def test_an_elided_quote_is_marked_as_elided():
    filler = "le contexte reste discutable et " * 12
    quote = d.find("Alors que " + filler + "je suis retraité.")[0][1]
    assert quote.startswith("…")


def test_repeated_lines_are_counted_but_quoted_once():
    same = _c("Je suis retraité depuis 2011.")
    out = d.for_subject([same, same, same])
    group = out["groups"][0]
    assert group["n"] == 3
    assert len(group["quotes"]) == 1


def test_rate_travels_with_the_count():
    # A prolific writer discloses more by writing more, so the count alone
    # cannot compare two subjects.
    out = d.for_subject([_c("Je suis retraité. " + "mot " * 500)])
    assert out["n_disclosures"] == 1
    assert 0 < out["per_1000"] < 5
    assert out["n_comments"] == 1


def test_a_subject_with_nothing_to_say_reports_nothing():
    out = d.for_subject([_c("Le match était bon."), _c("D'accord avec vous.")])
    assert out["groups"] == []
    assert out["n_disclosures"] == 0


def test_groups_keep_the_declared_category_order():
    out = d.for_subject([_c("Je suis médecin."), _c("Ma voiture est vieille.")])
    order = [g["key"] for g in out["groups"]]
    declared = [k for k, _, _ in d.CATEGORIES]
    assert order == sorted(order, key=declared.index)


def test_a_comment_that_only_mentions_someone_else_is_not_a_disclosure():
    # The pattern is first-person throughout; a third party's circumstances
    # must never land on this page.
    assert d.find("Sa femme est médecin et il habite à Vevey.") == []
    assert d.find("Vous êtes retraité, vous ne payez plus.") == []


def test_a_negation_anywhere_in_the_clause_flips_the_match():
    # Verb-adjacent forms are not enough in a population that argues for a
    # living: here the negation sits eight words from the match and asserts
    # the opposite of the company it appears to disclose.
    assert d.find("De nous deux, ce n'est pas non plus moi qui ai "
                  "comme associé de ma société des citoyens russes.") == []
    assert d.find("Aucun de mes enfants ne vote.") == []


def test_a_negation_in_the_PREVIOUS_clause_does_not_reach_this_one():
    # Otherwise every "je n'ai pas de X, mais je suis Y" is lost.
    got = d.find("Je n'ai pas de voiture, mais je suis retraité depuis 2011.")
    assert [k for k, _ in got] == ["occupation"]
