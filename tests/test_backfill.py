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


def test_a_thumbnail_is_not_an_article():
    # /files/imagecache/150x100/story/090109_Faitdiv.jpg has "/story/" and six
    # digits, so it satisfies the article-id pattern exactly as a story id
    # does. Unfiltered, the 2009-2011 24 heures article legs enumerated 100%
    # images and one night fetched 529 JPEGs into the article table.
    assert not bf.worth_fetching(
        "http://www.24heures.ch/files/imagecache/150x100/story/090109_Faitdiv.jpg")
    assert not bf.worth_fetching("http://www.tdg.ch/story/12345678/photo.PNG")
    # and the real thing still passes
    assert bf.worth_fetching("http://www.24heures.ch/vaud/story/12345678")
    assert bf.worth_fetching("http://www.lematin.ch/12420661/print.html?comments=1")


def test_the_drupal_url_grammar_is_recognised():
    # The pre-2012 article URL glues the id to the end of the slug, with no
    # /story/ and no trailing-slash id. Unrecognised, the whole era is
    # invisible to the fetcher no matter what the parser can read.
    assert bf.worth_fetching(
        "http://www.lematin.ch/sports/coupe-monde/vuvuzelas-bientot-interdits-stades-287915")
    assert bf.worth_fetching("http://www.lematin.ch/actu/suisse/elevage-crevettes-tendance-133009")
    # still not a section index, and still not a thumbnail
    assert not bf.worth_fetching("http://www.lematin.ch/actu/suisse/")
    assert not bf.worth_fetching("http://www.lematin.ch/actu/suisse/photo-287915.jpg")


def test_the_reader_is_chosen_by_the_markup_not_the_year():
    # The changeover was a deployment, so a capture from the same week can be
    # either stack. ingest asks the page.
    import inspect
    src = inspect.getsource(bf.ingest)
    assert "looks_like_lmo(page)" in src
    assert 'author_key=c.get("author_key")' in src


def test_the_drupal_era_is_a_leg_the_runner_knows_about():
    spec = bf.KINDS["lmo"]
    assert 2009 in spec["years"] and 2012 in spec["years"]
    assert 2013 not in spec["years"]      # Newsnetz by then


def test_a_latin1_page_is_not_read_as_utf8():
    # The pre-2009 pages declare charset=iso-8859-1 and mean it. Decoded as
    # UTF-8 every accent becomes a replacement character, silently, because
    # errors="replace" does not raise — and the accent measures are exactly
    # what the profiling pass reads.
    from mediatracker.wayback import decode
    raw = ('<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1">'
           'bonus encaiss\xe9s \xe0 Gen\xe8ve').encode("cp1252")
    assert "encaissés à Genève" in decode(raw)
    # a page that says nothing and is valid UTF-8 stays UTF-8
    assert decode("recommandé".encode("utf-8")) == "recommandé"


def test_the_php_era_is_a_leg_the_runner_knows_about():
    spec = bf.KINDS["reactions"]
    assert 2008 in spec["years"] and 2006 in spec["years"]
    assert 2010 not in spec["years"]        # Drupal by then
    # the section-before-id URL grammar the fetcher must accept
    assert bf.worth_fetching(
        "http://www.lematin.ch/fr/actu/economie/6-milliards-de-cadeau-pour-ubs_11-271110")


def test_the_reader_is_chosen_among_three_eras():
    import inspect
    src = inspect.getsource(bf.ingest)
    assert "looks_like_reactions(page)" in src
    assert "looks_like_lmo(page)" in src


def test_a_year_is_asked_for_a_month_at_a_time():
    # A whole-year CDX query against a busy domain does not error, it trickles
    # — and a socket timeout measures inactivity, so the call hangs for half an
    # hour and never raises. Two legs were lost to this. Months come back.
    calls = []

    class FakeClient:
        def cdx(self, domain, *, frm=None, to=None, url_filter=None, **kw):
            calls.append((frm, to))
            if frm.endswith("0201"):
                # February refuses both the filtered query and the plain one
                raise RuntimeError("archive said 504")
            return [{"timestamp": frm + "000000",
                     "original": f"http://x/story-{frm}"}]

    rows = bf.cdx_by_month(FakeClient(), "lematin.ch", kind="lmo", year=2010)
    # twelve months, and February asked twice: filtered, then plain
    assert len(calls) == 13
    assert calls[0] == ("20100101", "20100131")
    assert calls[1] == ("20100201", "20100228")      # 2010 is not a leap year
    assert calls[2] == ("20100201", "20100228")      # the unfiltered retry
    # a month that refuses both ways costs a month, not the leg
    assert len(rows) == 11


def test_a_leap_february_is_asked_for_whole():
    calls = []

    class FakeClient:
        def cdx(self, domain, *, frm=None, to=None, url_filter=None, **kw):
            calls.append((frm, to)); return []

    bf.cdx_by_month(FakeClient(), "lematin.ch", kind="lmo", year=2012)
    assert ("20120201", "20120229") in calls


def test_the_same_capture_seen_in_two_months_is_kept_once():
    class FakeClient:
        def cdx(self, domain, *, frm=None, to=None, url_filter=None, **kw):
            return [{"timestamp": "20100115000000", "original": "http://x/a"}]

    rows = bf.cdx_by_month(FakeClient(), "lematin.ch", kind="lmo", year=2010)
    assert len(rows) == 1


def test_a_month_the_archive_will_not_filter_is_asked_for_plain():
    # A server-side filter= makes the archive scan every capture in the span,
    # and under load that is the query it answers by dribbling. Asked plain it
    # does an index range scan and finishes, so the regex is applied here.
    seen = []

    class FakeClient:
        def cdx(self, domain, *, frm=None, to=None, url_filter=None, **kw):
            seen.append(url_filter)
            if url_filter is not None:
                raise TimeoutError("still arriving")
            return [{"timestamp": "1", "original": "http://x.ch/a/story-12345"},
                    {"timestamp": "2", "original": "http://x.ch/section/"}]

    rows = bf.cdx_by_month(FakeClient(), "x.ch", kind="lmo", year=2010)
    # tried filtered first, fell back to plain
    assert seen[0] == bf.KINDS["lmo"]["filter"] and seen[1] is None
    # and only the matching URL survived, deduped across twelve months
    assert len(rows) == 1
    assert rows[0]["original"].endswith("story-12345")
