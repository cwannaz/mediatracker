"""Reading archived Newsnetz pages, and spending the archive's bandwidth well."""
from mediatracker import archive_parse as ap
from mediatracker import backfill as bf

# Trimmed from a real 2013 lematin.ch capture: two comments, the second a reply.
PAGE = """
<html><body>
<h1>Drogues: 1,3 tonne de cocaïne saisie</h1>
<span class="comment_count">4</span>
<div class="commentRedesign evenParent" id="commentRedesign_17542845-7676256">
  <div class="komment" id="commentParent_7676256">
    <div class="kommentLeft">
      <!-- 17542845 7676256 0 -->
      <h4>Milton Jimenez</h4>
      <div class="kommentTime">21.09.2013, 22:56  Heures </div>
      <span class="recommendCommentCountNumber" id="recommendCommentCountNumber_7676256">2 </span>
      <span class="recommendCommentCountNumber" id="dislikeCommentCountNumber_7676256">1 </span>
    </div>
  </div>
  <p><span id="recommendCommentMessage_7676256">&ccedil;a me fait penser &agrave; GTA V</span></p>
</div>
<div class="commentRedesign oddParent" id="commentRedesign_17542845-7677046">
  <div class="komment" id="commentParent_7677046">
    <div class="kommentLeft">
      <!-- 17542845 7677046 7676256 -->
      <h4>Arlette Berney</h4>
      <div class="kommentTime">22.09.2013, 09:13  Heures </div>
      <span class="recommendCommentCountNumber" id="recommendCommentCountNumber_7677046">15 </span>
    </div>
  </div>
  <p><span id="recommendCommentMessage_7677046">Et bien voil&agrave; la vente</span></p>
</div>
<div id="commentform">
  <h4>Votre commentaire</h4>
  <div class="kommentTime">envoyer</div>
</div>
</body></html>
"""


def test_a_thread_comes_back_whole():
    cs = ap.parse_comments(PAGE)
    assert [c["source_id"] for c in cs] == ["7676256", "7677046"]
    assert cs[0]["author_nick"] == "Milton Jimenez"
    assert cs[0]["body_text"] == "ça me fait penser à GTA V"
    assert cs[0]["like_count"] == 2 and cs[0]["dislike_count"] == 1


def test_the_html_comment_is_what_carries_the_threading():
    # Nothing visible on the page says this is a reply; the "<!-- article msg
    # parent -->" marker does, and it is the only place the parent appears.
    cs = ap.parse_comments(PAGE)
    assert cs[0]["parent_id"] is None
    assert cs[1]["parent_id"] == "7676256"


def test_the_empty_reply_form_is_not_read_as_a_comment():
    # The form at the foot of the page has an <h4> and a kommentTime too. A
    # block with no message body is dropped rather than stored half-filled.
    assert len(ap.parse_comments(PAGE)) == 2


def test_a_swiss_timestamp_is_read_in_zurich_time():
    ts = ap.parse_timestamp("21.09.2013, 22:56  Heures")
    assert (ts.year, ts.month, ts.day, ts.hour) == (2013, 9, 21, 22)
    assert ts.utcoffset().total_seconds() == 7200        # CEST in September
    assert ap.parse_timestamp("not a date") is None


def test_the_article_carries_its_thread_size_even_with_no_thread():
    art = ap.parse_article(PAGE, "http://www.lematin.ch/x/story/17542845")
    assert art["comment_count"] == 4
    assert art["source_id"] == "17542845"
    assert "cocaïne" in art["headline"]


# --------------------------------------------------------------------------- #

def test_every_view_of_one_thread_folds_to_one_article():
    for u in ("http://www.lematin.ch/a/story/123456?comments=1",
              "http://www.lematin.ch/a/story/123456/print.html?comments=1",
              "http://www.lematin.ch/a/story/123456/print.html"):
        assert bf.article_url_of(u) == "http://www.lematin.ch/a/story/123456"


def test_a_section_index_is_not_worth_a_request():
    # "/story/?comments=1" with no id: the archive holds thousands and none of
    # them has a thread.
    assert not bf.worth_fetching("http://www.tdg.ch/auto-moto/x/story/?comments=1")
    assert bf.worth_fetching("http://www.tdg.ch/auto-moto/x/story/25053924?comments=1")
    assert bf.worth_fetching("http://www.lematin.ch/14457038/print.html?comments=1")


def test_the_print_view_wins_because_it_is_not_paginated():
    rows = [{"original": "http://x.ch/a/story/9999999?comments=1", "timestamp": "20150301000000"},
            {"original": "http://x.ch/a/story/9999999/print.html?comments=1", "timestamp": "20140101000000"}]
    kept = bf.newest_per_article(rows)
    assert len(kept) == 1
    assert "print.html" in kept[0]["original"]


def test_otherwise_the_latest_capture_wins_because_threads_only_grow():
    rows = [{"original": "http://x.ch/a/story/9999999?comments=1", "timestamp": "20150301000000"},
            {"original": "http://x.ch/a/story/9999999?comments=1", "timestamp": "20160101000000"}]
    kept = bf.newest_per_article(rows)
    assert len(kept) == 1 and kept[0]["timestamp"] == "20160101000000"


def test_archive_captures_never_join_the_live_rescan_list():
    # A wayback article's URL is a real one the live site no longer serves, and
    # it carries no published_at on purpose, so `first_seen` stands in and it
    # looks published the moment the backfill wrote it. Left in the work-list,
    # 4,595 recovered articles pushed one Tribune scan from 27 minutes to six
    # and a half hours.
    from mediatracker import db
    assert "wayback" in db.ARCHIVE_ORIGINS
    assert "pdf" in db.ARCHIVE_ORIGINS
    # whatever origin the backfill writes must be one the work-list excludes
    import inspect
    src = inspect.getsource(bf.ingest)
    assert 'origin="wayback"' in src
