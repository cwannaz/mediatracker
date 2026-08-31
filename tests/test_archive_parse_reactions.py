"""Reading the PHP era (to ~2009) — the oldest stack, and the best for identity."""
from mediatracker import archive_parse_reactions as rx

# Trimmed verbatim from the 2008-10-20 capture of
# lematin.ch/fr/actu/economie/6-milliards-de-cadeau-pour-ubs_11-271110
PAGE = """
<html><head><meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1"></head>
<body>
<h1>6 milliards de cadeau pour UBS</h1>
<a name="Commentaires">
<h3 id="vos_reactions"> <span>Vos r&eacute;actions</span>
<em id="nbcomment" class="commentaires"><strong>172</strong> commentaires</em> </h3>
</a>
<!-- BEGIN COMMENT HTML -->
<div class="reaction" style="padding-bottom:20px">
  <div class="reaction_vignette">
    <a href="/membership/ma_page.php?idUser=387782"><img src="/x.gif"></a>
  </div>
  <div class="reaction_text">
    <p>Et les centaines de millions de bonus encaiss&eacute;s aux states ?</p>
  </div>
  <div class="reaction_text">
  <div style="float:left"><p class="reaction_date"><small>
      16.10.2008 - 00:03 par <strong><a href="/membership/ma_page.php?idUser=387782">Ge1602</a></strong>
  </small></p></div>
  <div style="float:right">
    <a href="/fr/aide/contact/detail.php?idContent=161815&amp;sign=271152" class="link_sign_abus">&gt;Signaler un abus</a>
  </div>
  </div>
</div>
<!-- END COMMENT HTML -->
<!-- BEGIN COMMENT HTML -->
<div class="reaction" style="padding-bottom:20px">
  <div class="reaction_vignette">
    <a href="/membership/ma_page.php?idUser=323689"><img src="/x.gif"></a>
  </div>
  <div class="reaction_text">
    <p>et pendant ce temps-l&agrave;, les ex-employ&eacute;s de Swissair regardent passer les fourgons...</p>
  </div>
  <div class="reaction_text">
  <div style="float:left"><p class="reaction_date"><small>
      16.10.2008 - 00:09 par <strong><a href="/membership/ma_page.php?idUser=323689">chriz-tian</a></strong>
  </small></p></div>
  <div style="float:right">
    <a href="/fr/aide/contact/detail.php?idContent=161815&amp;sign=271160" class="link_sign_abus">&gt;Signaler un abus</a>
  </div>
  </div>
</div>
<!-- END COMMENT HTML -->
</body></html>
"""

URL = "http://www.lematin.ch/fr/actu/economie/6-milliards-de-cadeau-pour-ubs_11-271110"


def test_a_php_era_thread_comes_back_whole():
    cs = rx.parse_comments(PAGE)
    assert [c["source_id"] for c in cs] == ["271152", "271160"]
    assert cs[0]["body_text"].startswith("Et les centaines de millions")
    assert "Swissair" in cs[1]["body_text"]


def test_the_numeric_account_id_is_what_makes_this_era_worth_having():
    # idUser is a number, so it survives a change of display name in a way the
    # Drupal era's /users/<slug> does not. It is the sturdiest identity anchor
    # in the corpus, and the only one that can confirm a rename outright.
    cs = rx.parse_comments(PAGE)
    assert cs[0]["author_key"] == "387782"
    assert cs[0]["author_nick"] == "Ge1602"
    assert cs[1]["author_key"] == "323689"
    assert cs[1]["author_nick"] == "chriz-tian"


def test_the_comment_id_is_the_sign_not_the_article():
    # idContent is constant down the page and names the article; sign differs
    # per comment. Reading the wrong one collapses a whole thread to one id.
    cs = rx.parse_comments(PAGE)
    assert len({c["source_id"] for c in cs}) == 2
    assert all(c["source_id"] != "161815" for c in cs)


def test_what_this_platform_never_had_stays_none():
    cs = rx.parse_comments(PAGE)
    assert all(c["parent_id"] is None for c in cs)
    assert all(c["like_count"] is None and c["dislike_count"] is None for c in cs)


def test_a_swiss_timestamp_is_read_in_zurich_time():
    ts = rx.parse_timestamp("16.10.2008 - 00:03")
    assert (ts.year, ts.month, ts.day, ts.hour, ts.minute) == (2008, 10, 16, 0, 3)
    assert ts.utcoffset().total_seconds() == 7200          # CEST in October


def test_the_article_carries_its_url_id_and_thread_size():
    art = rx.parse_article(PAGE, URL)
    assert art["source_id"] == "271110"
    assert art["comment_count"] == 172
    assert "UBS" in art["headline"]


def test_the_three_eras_are_told_apart():
    from mediatracker import archive_parse as ap
    from mediatracker import archive_parse_lmo as lmo
    assert rx.looks_like_reactions(PAGE)
    assert not lmo.looks_like_lmo(PAGE)
    assert not ap.looks_like_newsnetz(PAGE)
