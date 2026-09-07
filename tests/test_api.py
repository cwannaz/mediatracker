"""The API is a contract with other projects, so its shape is tested, not just
its behaviour.

The daemon's WebSocket surface may change whenever a view changes. This one may
not: another program depends on the names of these fields, and renaming one is
a breaking change even when nothing else moved. These tests fail loudly if a
field disappears.
"""
import json

from mediatracker import api


class _FakeCursor:
    def __init__(self, rows):
        self._rows, self._i = rows, 0
    def execute(self, *a, **k):
        pass
    def fetchall(self):
        r = self._rows[min(self._i, len(self._rows) - 1)]
        self._i += 1
        return r
    def fetchone(self):
        r = self.fetchall()
        return r[0] if r else None
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_csv_accepts_comma_separated_values():
    assert api._csv("article,image") == ("article", "image")


def test_csv_ignores_blanks_and_padding():
    assert api._csv(" article , , image ") == ("article", "image")


def test_csv_of_nothing_is_empty():
    assert api._csv("") == () and api._csv(None) == ()


def _hit(kind, extra):
    h = api._Handler.__new__(api._Handler)
    h.blob_base = "http://host:55031"
    return h._hit({"kind": kind, "ref": "abc", "journal": "tdg", "when": "2020-01-01",
                   "title": "T", "snippet": "a <<b>> c", "rank": 1.0, "extra": extra})


def test_every_result_carries_the_common_fields():
    # A consumer switches on `kind`; everything else must be present whatever
    # the kind is, or the switch has to become a series of lookups.
    for kind in ("article", "image", "comment"):
        h = _hit(kind, {})
        for field in ("kind", "id", "journal", "date", "title", "snippet",
                      "highlighted", "score", "document_url"):
            assert field in h, f"{kind} result is missing {field}"


def test_the_snippet_is_plain_and_the_highlight_is_kept_separately():
    # Marks are useful to render and unhelpful to index; both are offered.
    h = _hit("article", {})
    assert h["snippet"] == "a b c"
    assert h["highlighted"] == "a <<b>> c"


def test_an_image_result_carries_three_absolute_urls():
    h = _hit("image", {"width": 10, "height": 5})
    img = h["image"]
    for k in ("blob_url", "thumbnail_url", "preview_url"):
        assert img[k].startswith("http://host:55031/"), k
    assert img["width"] == 10


def test_a_comment_result_names_its_author():
    h = _hit("comment", {"nick": "someone", "is_reply": True})
    assert h["comment"]["author"] == "someone"
    assert h["comment"]["is_reply"] is True


def test_an_article_result_carries_its_desk_and_byline():
    h = _hit("article", {"section": "sport", "author": "X", "comments": 3})
    assert h["article"]["section"] == "sport"
    assert h["article"]["comment_count"] == 3


def test_the_document_url_can_be_followed():
    h = _hit("article", {})
    assert h["document_url"] == "/v1/document/article/abc"


def test_a_bad_kind_is_refused_with_the_legal_values():
    # Guessing costs a round trip; the error carries the answer.
    h = api._Handler.__new__(api._Handler)
    out = h._search(None, {"kind": "bogus"}, {})
    assert out["ok"] is False
    assert out["allowed"] == list(api._search_kinds())


def test_a_bad_mode_is_refused():
    h = api._Handler.__new__(api._Handler)
    out = h._search(None, {"mode": "sql"}, {})
    assert out["ok"] is False and "mode" in out["error"]


def test_a_non_numeric_limit_is_refused_rather_than_ignored():
    h = api._Handler.__new__(api._Handler)
    out = h._search(None, {"limit": "lots"}, {})
    assert out["ok"] is False


def test_the_version_is_pinned():
    # The path carries /v1; a change here is a change of contract.
    assert api.VERSION.startswith("1.")
