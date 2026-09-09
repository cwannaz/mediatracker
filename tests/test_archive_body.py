"""The archive body extractor.

Written after measuring that the era-specific readers returned a body for 0 of
60 archived captures, silently. These pin the two things that were actually
wrong and the one thing that would be worse than being wrong.
"""
from __future__ import annotations

from mediatracker.archive_body import extract_body

LONG = ("Le Conseil federal a decide vendredi de prolonger les mesures "
        "sanitaires jusqu au printemps prochain, malgre l opposition d une "
        "partie du Parlement et des cantons romands qui reclamaient un "
        "assouplissement immediat des restrictions en vigueur. ") * 3


def page(body: str) -> str:
    return f"<html><body><div class='article'>{body}</div></body></html>"


def test_a_plain_article_page_yields_its_prose():
    got = extract_body(page(f"<p>{LONG}</p>"))
    assert got is not None and "Conseil federal" in got


def test_a_teaser_is_not_a_body():
    """A comment view carries the lead and a 'Plus...' link. Storing that would
    mark the article done with 233 characters of summary."""
    teaser = ("<h2>La Vaudoise Adele Thorens a la tete des Verts</h2>"
              "<p>La conseillere nationale vaudoise Adele Thorens, 40 ans, a "
              "ete elue samedi a la co-presidence des Verts suisses. Plus...</p>")
    assert extract_body(page(teaser)) is None


def test_the_comment_thread_never_becomes_the_body():
    """The failure that would read like signal: readers' words stored as the
    article's own text, then indexed and fed to entity extraction."""
    doc = page(
        f"<p>{LONG}</p>"
        "<div class='commentRedesign'>"
        "<p>Moi je pense que ce journaliste raconte n importe quoi et je le "
        "dis depuis des annees sur ce site, personne ne m ecoute jamais.</p>"
        "<p>Encore un commentaire tout aussi long et tout aussi inutile que "
        "le precedent, avec assez de caracteres pour passer le seuil.</p>"
        "</div>")
    got = extract_body(doc)
    assert got is not None
    assert "Conseil federal" in got
    assert "journaliste raconte" not in got
    assert "tout aussi inutile" not in got


def test_nested_comment_containers_are_cut_whole():
    doc = page(
        f"<p>{LONG}</p>"
        "<div class='comments'><div class='komment'>"
        "<p>Un commentaire imbrique assez long pour depasser le seuil minimal "
        "de vingt-cinq caracteres et donc etre retenu par erreur.</p>"
        "</div></div>")
    got = extract_body(doc)
    assert "commentaire imbrique" not in got


def test_furniture_is_dropped():
    doc = page(
        "<p class='smallPrint'>Nous vous invitons ici a donner votre point de "
        "vue et vos informations sur ce sujet dans le respect d autrui.</p>"
        f"<p>{LONG}</p>"
        "<p class='caption'>Adele Thorens lors de l assemblee des delegues</p>")
    got = extract_body(doc)
    assert "invitons ici" not in got and "assemblee des delegues" not in got


def test_scripts_and_styles_are_not_prose():
    doc = page(f"<script>var s = '<p>fake body text here</p>';</script><p>{LONG}</p>")
    got = extract_body(doc)
    assert "fake body" not in got


def test_a_repeated_lead_is_not_stored_twice():
    """Archived pages repeat the lead in a meta block and again in the article;
    keeping both would double the text of every short piece."""
    lead = ("Les delegues reunis a Carouge ont porte deux femmes a la tete "
            "du parti ecologiste suisse, une premiere dans son histoire "
            "recente, au terme d un vote sans surprise notable. ")
    doc = page(f"<p>{lead}</p><p>{lead}</p><p>{LONG}</p>")
    got = extract_body(doc)
    assert got.count("delegues reunis a Carouge") == 1


def test_entities_are_decoded():
    doc = page("<p>" + "L &eacute;conomie suisse a recul&eacute; de mani&egrave;re "
               "sensible durant le dernier trimestre selon les chiffres. " * 8 + "</p>")
    got = extract_body(doc)
    assert "économie" in got and "&eacute;" not in got


def test_an_empty_or_bodyless_page_is_none():
    assert extract_body("") is None
    assert extract_body(page("<p>Trop court.</p>")) is None


def test_the_body_is_capped():
    from mediatracker.archive_body import MAX_CHARS
    got = extract_body(page(f"<p>{LONG * 400}</p>"))
    assert len(got) <= MAX_CHARS
