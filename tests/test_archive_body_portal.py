"""Reading the pre-2012 Le Matin portal pages.

`extract_body` refuses this era on purpose: its teasers, contest promos and
video captions sit in bare <p> exactly like article prose, so a page once
produced a 9,640-character "body" made of a dozen unrelated stories. The
portal reader does not filter that soup better -- it never reaches it, because
the template names the article's own text in a container and the other stories
sit outside it.

The markup below is the real shape of both eras, reduced. Every number quoted
in a docstring here was measured on 24 captures sampled across 2008-2011.
"""
from mediatracker.archive_body import (extract_body, extract_body_portal,
                                       looks_like_portal)

LEAD = ("Une journaliste de CBS a été battue et agressée sexuellement alors "
        "qu'elle couvrait les célébrations sur la place Tahrir au Caire.")
P1 = ("Agée de 39 ans, Lara Logan, originaire d'Afrique du Sud, est habituée "
      "des zones de conflit et travaille pour la chaîne américaine depuis "
      "bientôt dix ans selon ses proches.")
P2 = ("D'après le Comité pour la protection des journalistes, au moins 52 "
      "journalistes ont été agressés pendant les manifestations qui ont "
      "secoué la capitale égyptienne.")
TEASER = ("Un institut scientifique spécialisé dans la recherche sur les "
          "yétis devrait voir le jour en Sibérie, selon les autorités "
          "locales de la région de Kemerovo.")


def _drupal(lead=LEAD, paras=(P1, P2), teaser=TEASER):
    """2009-2011: one bloc_article, one text_article, a dozen chapo."""
    return f"""
    <html><body>
      <div class="bloc_article">
        <h1>Une journaliste de CBS violée et battue au Caire</h1>
        <div class="chapo"><p class="chapo">{lead}</p></div>
        <div class="text_article">
          <p>{paras[0]}</p>
          <p>{paras[1] if len(paras) > 1 else ''}</p>
        </div>
      </div>
      <div class="bloc_accroche">
        <div class="textl"><p class="chapo">{teaser}</p></div>
      </div>
      <div class="footer"><p>edicom.ch | 24heures.ch | tdg.ch</p></div>
    </body></html>"""


def _php(lead=LEAD, paras=(P1, P2)):
    """2008: article_text, the mirror name, and the chapo is a div or an h3."""
    return f"""
    <html><body>
      <h3 class="chapo"><p><strong>{lead}</strong></p></h3>
      <div class="article_text">
        <p>{paras[0]}</p>
        <p>{paras[1] if len(paras) > 1 else ''}</p>
      </div>
      <div class="bloc_accroche"><p class="chapo">{TEASER}</p></div>
    </body></html>"""


# -- what it must find ---------------------------------------------------- #

def test_the_drupal_era_article_is_read():
    body = extract_body_portal(_drupal())
    assert body and LEAD in body and P1 in body and P2 in body


def test_the_php_era_article_is_read():
    """2008 spells the container "article_text" -- the mirror of 2009's name."""
    body = extract_body_portal(_php())
    assert body and LEAD in body and P1 in body


def test_the_lead_is_kept_whatever_tag_carries_it():
    """A <div>, an <h3>, or a <div> wrapping a <p class="chapo">.

    While this looked only for a <p>, every 2008 lead was dropped -- and the
    lead is the sentences most likely to name the subject.
    """
    for page in (_php(), _drupal()):
        assert LEAD in (extract_body_portal(page) or "")


# -- what it must never find ---------------------------------------------- #

def test_the_other_stories_on_the_page_are_not_in_the_body():
    """The whole reason this reader exists."""
    for page in (_drupal(), _php()):
        body = extract_body_portal(page) or ""
        assert TEASER not in body
        assert "yétis" not in body


def test_a_teasers_chapo_is_not_mistaken_for_the_articles():
    """These pages carry 9 to 12 chapo paragraphs; one belongs to the article."""
    page = _drupal().replace('<div class="bloc_article">',
                             '<div class="bloc_accroche">'
                             '<div class="textl"><p class="chapo">'
                             'Rappel des événements en Libye, au cinquième '
                             'jour de l\'opération de la coalition.</p></div>'
                             '</div><div class="bloc_article">')
    body = extract_body_portal(page) or ""
    assert LEAD in body
    assert "Libye" not in body


def test_the_footer_is_not_prose():
    assert "edicom.ch" not in (extract_body_portal(_drupal()) or "")


# -- the shape of the answer ---------------------------------------------- #

def test_a_page_without_the_container_is_declined():
    assert extract_body_portal("<html><body><p>" + P1 + "</p></body></html>") is None
    assert not looks_like_portal("<html><body><p>hello</p></body></html>")
    assert looks_like_portal(_drupal()) and looks_like_portal(_php())


def test_a_teaser_length_body_is_declined_rather_than_stored():
    """A teaser stored as a body is worse than no body: it marks it done."""
    assert extract_body_portal(_drupal(lead="Court.", paras=("Aussi court.",))) is None


def test_a_dateline_is_not_a_lead():
    """Some 2008 chapos hold only "12/03/2008 PARIS (AFP)" and an empty <p>."""
    # A third paragraph, so the body still clears the 400-character floor
    # once the dateline is correctly refused -- otherwise this would pass for
    # the wrong reason, by being too short either way.
    page = _php(paras=(P1, P2 + " " + P1)).replace(
        f"<p><strong>{LEAD}</strong></p>",
        "<p><strong></strong></p><br/>"
        "<span>12/03/2008&nbsp;PARIS (AFP)&nbsp;</span>")
    body = extract_body_portal(page) or ""
    assert body.startswith(P1[:40])
    assert "AFP" not in body and "12/03/2008" not in body


def test_nested_divs_do_not_truncate_the_body():
    """Measured: 2 of 24 captures nest divs, and stopping at the first
    </div> cut one body from 2,559 characters to 1,296 -- silently."""
    page = _drupal().replace(
        f"<p>{P1}</p>",
        f'<div class="encadre"><p>{P1}</p></div>')
    assert P2 in (extract_body_portal(page) or "")


def test_no_tag_debris_survives():
    """Slicing past the closing tag leaves a bare "</div" that no
    tag-stripping regex matches, because it has no ">"."""
    for page in (_drupal(), _php()):
        body = extract_body_portal(page) or ""
        for debris in ("</div", "<div", "</p", "<p", "&nbsp;"):
            assert debris not in body, debris


# -- the two readers must not both claim a page --------------------------- #

def test_the_generic_reader_is_still_the_one_that_swallows_the_soup():
    """Not a criticism of it -- the reason the era is refused there.

    If this ever stops being true the portal reader has become redundant, and
    that is worth failing a test over rather than discovering by drift.
    """
    page = _drupal()
    generic = extract_body(page) or ""
    portal = extract_body_portal(page) or ""
    assert TEASER in generic          # the soup, kept
    assert TEASER not in portal       # never reached


# -- the wire dateline ---------------------------------------------------- #

def test_a_paragraph_that_is_only_a_dateline_is_dropped():
    """It opened 5 of the first 24 recovered bodies, and clears the floor."""
    from mediatracker.archive_body import _is_dateline

    for s in ("17/03/2008 NEW YORK (AFP)", "12/03/2008 PARIS (AFP)",
              "WASINGTON (AFP)", "(ATS)", "17.03.2008 GENEVE (Reuters) ",
              "17/03/2008 NEW YORK (AFP)  "):
        assert _is_dateline(s), s


def test_the_same_words_inside_a_sentence_are_prose():
    """Only an entire paragraph counts, or real text would be deleted."""
    from mediatracker.archive_body import _is_dateline

    for s in ("Le baril a chuté selon (AFP) hier à New York.",
              "L'agence (AFP) a confirmé l'information lundi matin.",
              "A NEW YORK, les marchés ont ouvert en baisse de trois points."):
        assert not _is_dateline(s), s


def test_a_dateline_lead_does_not_open_the_article():
    page = _php(paras=(P1, P2 + " " + P1)).replace(
        f"<p><strong>{LEAD}</strong></p>",
        "<p><strong>17/03/2008&nbsp;NEW YORK (AFP)</strong></p>")
    body = extract_body_portal(page) or ""
    assert body.startswith(P1[:40])
    assert "NEW YORK (AFP)" not in body
