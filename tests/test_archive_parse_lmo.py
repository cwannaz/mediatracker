"""Reading the Drupal-era (2009 - March 2012) pages that preceded Newsnetz."""
from mediatracker import archive_parse_lmo as lmo

# Trimmed verbatim from the 2010-06-15 capture of
# lematin.ch/sports/coupe-monde/vuvuzelas-bientot-interdits-stades-287915
PAGE = """
<html><body>
<h1>Les vuvuzelas bientôt interdits dans les stades</h1>
<a name="comments"></a>
<div class="comments_titre"><small class="right">
<strong>27</strong> commentaires</small>
<p>Vos réactions</p>
</div>
<div id="comments"><a id="comment-410104"></a>
<div class="commentaire ">
  <a href="/users/affreujojo883">  <img class="vignette_comment" src="/sites/default/themes/lmo/img/x.gif"/>
  </a>  <p>Oui!oui! Piti&eacute;!
C'EST INSUPPORTABLE !</p>
  <p><small><ul class="links"><li class="first last comment_forbidden"><span class="comment_forbidden">Vous devez <a href="/user/login?destination=node/287915%2523comment-form">vous identifier</a> pour &eacute;crire des commentaires</span></li>
</ul></small></p>
  <p class="left"><small>13.06.2010 - 15:59 par 
  <a href="/users/affreujojo883">  <strong>affreujojo883</strong>
  </a></small></p>
  <p class="right"><small> > <a href="/comment/410104/signal?destination=node%2F287915" rel="nofollow">Signaler un abus</a></small></p>
</div>
<a id="comment-410110"></a>
<div class="commentaire ">
  <a href="/users/mountaidiver">  <img class="vignette_comment" src="/sites/default/themes/lmo/img/x.gif"/>
  </a>  <p>tant qu'elles ont pas le m&ecirc;me effet que les trompettes de Jericho, sur mon LCD &ccedil;a va ... lol</p>
  <p><small><ul class="links"><li class="first last comment_forbidden"><span class="comment_forbidden">Vous devez vous identifier</span></li>
</ul></small></p>
  <p class="left"><small>13.06.2010 - 16:16 par 
  <a href="/users/mountaidiver">  <strong>MountaiDiver</strong>
  </a></small></p>
  <p class="right"><small> > <a href="/comment/410110/signal?destination=node%2F287915" rel="nofollow">Signaler un abus</a></small></p>
</div>
</div>
</body></html>
"""

URL = "http://www.lematin.ch/sports/coupe-monde/vuvuzelas-bientot-interdits-stades-287915"


def test_a_drupal_thread_comes_back_whole():
    cs = lmo.parse_comments(PAGE)
    assert [c["source_id"] for c in cs] == ["410104", "410110"]
    assert cs[0]["body_text"].startswith("Oui!oui! Pitié!")
    assert "trompettes de Jericho" in cs[1]["body_text"]


def test_the_account_key_and_the_displayed_name_are_kept_apart():
    # This is the whole reason the era is worth more per page: the site had
    # accounts. /users/mountaidiver is the site's own key and "MountaiDiver"
    # is how the person chose to appear; the case differs and both matter.
    cs = lmo.parse_comments(PAGE)
    assert cs[1]["author_key"] == "mountaidiver"
    assert cs[1]["author_nick"] == "MountaiDiver"


def test_the_login_notice_is_not_read_as_the_comment():
    # Every block carries a "Vous devez vous identifier" paragraph that is
    # longer than some real comments. It is told apart by carrying <small>
    # and <ul>, not by its position.
    cs = lmo.parse_comments(PAGE)
    for c in cs:
        assert "identifier" not in c["body_text"]


def test_what_this_platform_never_had_stays_none():
    # No threading and no like counts existed before Newsnetz. None says the
    # platform had no such field; 0 would claim a measurement.
    cs = lmo.parse_comments(PAGE)
    assert all(c["parent_id"] is None for c in cs)
    assert all(c["like_count"] is None and c["dislike_count"] is None for c in cs)


def test_a_swiss_timestamp_is_read_in_zurich_time():
    ts = lmo.parse_timestamp("13.06.2010 - 15:59")
    assert (ts.year, ts.month, ts.day, ts.hour, ts.minute) == (2010, 6, 13, 15, 59)
    assert ts.utcoffset().total_seconds() == 7200          # CEST in June
    assert lmo.parse_timestamp("21.09.2013, 22:56 Heures") is None   # Newsnetz form


def test_the_article_carries_its_node_id_and_thread_size():
    art = lmo.parse_article(PAGE, URL)
    assert art["source_id"] == "287915"
    assert art["comment_count"] == 27
    assert "vuvuzelas" in art["headline"].lower()


def test_the_two_eras_are_told_apart():
    assert lmo.looks_like_lmo(PAGE)
    from mediatracker import archive_parse as ap
    assert not ap.looks_like_newsnetz(PAGE)
