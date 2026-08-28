"""How a commenter chose to appear — and what that reading must never claim."""
from mediatracker import handles as hd
from mediatracker import nicknames as nn


def test_a_forename_and_a_surname_read_as_a_name():
    for n in ("Vincent Zellweger", "Isaline Perrenoud", "Jean-Pierre Dubouloz",
              "Michel THOMAS", "P-A Gallay", "Pierre.Bonvin", "Olivier_Reymond"):
        assert hd.form(n) == "personal-name", n


def test_name_shaped_is_a_statement_about_the_string_and_nothing_else():
    # The whole point. Every one of these is built exactly like a name and is
    # a joke, a borrowing, or the form field's own labels. Nothing in this
    # module may be read as "this commenter uses their real name".
    for n in ("Alex Tincteur", "Paul Ochon", "Jean Eymar", "Anna Coluthe",
              "Nom Prénom", "Yitzhak Rabin", "Guillaume TELL"):
        assert hd.form(n) == "personal-name", n
    # and the hand lexicon is what unmasks them
    assert nn.read("Alex Tincteur")["refers_to"] == "un extincteur"
    assert nn.read("Anna Coluthe")["device"] == "pun"


def test_a_persona_is_not_a_name():
    for n in ("Colargol", "oscarthegrouch", "C est pas mal hein", "Le glaneur",
              "le-vrai-looser-de-mar-a-lago", "guignolo1920"):
        assert hd.form(n) == "alias", n


def test_a_string_that_names_nothing():
    for n in ("dcrc60", "JDE", "E13", "gtrht", "U_u", "Jp", "NDR"):
        assert hd.form(n) == "opaque", n


def test_a_forename_with_a_number_is_a_way_of_appearing_not_a_refusal():
    # `luc45` is somebody being casual, not somebody hiding. It reads as a
    # persona, which is why the readable-letter floor is three and not four.
    for n in ("luc45", "Ali14", "Roby55", "aziz93"):
        assert hd.form(n) == "alias", n


def test_a_single_given_name_is_not_treated_as_presenting_as_a_person():
    # Deliberate, and cheap: measured over the corpus, 5 of 1,666 one-word
    # handles are given names. Claiming otherwise would need a list of first
    # names, and the list would misfire on every persona that happens to be one.
    assert hd.form("Fabienne") == "alias"
    assert hd.form("Marc") == "alias"


def test_the_administrative_shape_is_kept_apart():
    # A surname in capitals is what people produce for a box that asked for a
    # legal name — a different act from choosing a handle.
    assert hd.read("Michel THOMAS")["full_caps_surname"] is True
    assert hd.read("Vincent Zellweger")["full_caps_surname"] is False
    assert hd.read("P-A Gallay")["initialled"] is True


def test_a_distribution_reports_what_is_unresolved_rather_than_what_is_real():
    nicks = ["Vincent Zellweger", "Alex Tincteur", "Colargol", "dcrc60"]
    d = hd.distribution(nicks, unmasked={"Alex Tincteur"})
    assert d["name_shaped"] == 2
    assert d["name_shaped_recognised"] == 1
    # The remainder is "not resolved", never "genuine".
    assert d["name_shaped_unresolved"] == 1
    assert {f["form"] for f in d["forms"]} == set(hd.FORMS)


def test_nothing_at_all_is_opaque_rather_than_an_error():
    for n in ("", None, "  ", "42"):
        assert hd.form(n) == "opaque"
