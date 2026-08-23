from __future__ import annotations

from mediatracker import ids


def test_canonical_url_drops_tracking_and_sorts_query():
    a = ids.canonical_url("https://www.lematin.ch/Article?b=2&utm_source=nl&a=1#c")
    b = ids.canonical_url("HTTPS://www.lematin.ch/Article?a=1&b=2")
    assert a == b
    assert "utm_source" not in a
    assert a.endswith("a=1&b=2")


def test_canonical_url_strips_trailing_slash_and_fragment():
    assert ids.canonical_url("https://x.ch/a/b/") == "https://x.ch/a/b"
    assert "#" not in ids.canonical_url("https://x.ch/a#section")


def test_article_id_is_stable_and_idempotent():
    u1 = "https://www.24heures.ch/story-123?utm_medium=push"
    u2 = "https://www.24heures.ch/story-123"
    assert ids.article_id("24heures", u1) == ids.article_id("24heures", u2)


def test_article_id_differs_by_journal():
    url = "https://x.ch/story-1"
    assert ids.article_id("lematin", url) != ids.article_id("24heures", url)


def test_comment_id_prefers_source_key():
    aid = ids.article_id("lematin", "https://x.ch/a")
    assert ids.comment_id("lematin", aid, "42") == ids.comment_id("lematin", aid, "42")
    assert ids.comment_id("lematin", aid, "42") != ids.comment_id("lematin", aid, "43")


def test_synthetic_comment_id_changes_with_content():
    aid = ids.article_id("lematin", "https://x.ch/a")
    base = ids.synthetic_comment_id(aid, "bob", "2026-01-01", "hello")
    assert base == ids.synthetic_comment_id(aid, "bob", "2026-01-01", "hello")
    assert base != ids.synthetic_comment_id(aid, "bob", "2026-01-01", "hi")


def test_content_hash_detects_change():
    assert ids.content_hash("a", "b") == ids.content_hash("a", "b")
    assert ids.content_hash("a", "b") != ids.content_hash("a", "c")
