"""Storing a counter reading from a year whose comments are gone."""
import json

import pytest

from mediatracker import backfill as bf
from mediatracker import community_backfill as cb

PAYLOAD = json.dumps({"communities": [
    {"type": "facebook", "count": 183,
     "url": "https://www.lematin.ch/10001941",
     "title": "Les primes maladie impay&eacute;es",
     "article_id": "5dcd1942ab5c37000117d1a1"},
    {"type": "comment", "count": 3, "article_id": "5dcd1942ab5c37000117d1a1",
     "refresh": "14.11.2019 15:34:53"},
]})

CAPTURED = "https://www.lematin.ch/api/communities/10001941"


class Recorder:
    """Stands in for db, remembering what the runner asked it to write."""

    def __init__(self, existing=None, accept=True):
        self.existing = existing
        self.accept = accept
        self.articles = []
        self.snapshots = []

    def find_article_by_source_key(self, conn, jid, key):
        return self.existing

    def upsert_article(self, conn, **kw):
        self.articles.append(kw)

    def insert_article_snapshot(self, conn, **kw):
        self.snapshots.append(kw)
        return 1 if self.accept else None


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(cb, "db", r)
    return r


def test_a_reading_is_stored_as_a_count_with_no_comments(rec):
    stats = bf.Stats()
    assert cb.ingest_one(None, payload=PAYLOAD, original=CAPTURED,
                         timestamp="20191114160000", stats=stats)
    snap = rec.snapshots[0]
    assert snap["fields"]["comment_count"] == 3
    assert snap["fields"]["headline"] == "Les primes maladie impayées"
    # The flag that stops anyone downstream reading "no comment rows" as
    # "nobody commented". Three people did; we hold none of them.
    assert snap["fields"]["raw_meta"]["counts_only"] is True
    assert snap["fields"]["raw_meta"]["shares"] == {"facebook": 183}
    assert stats.snapshots == 1


def test_the_publishers_own_clock_is_kept(rec):
    # It can precede the capture by hours and is the better date for the count.
    cb.ingest_one(None, payload=PAYLOAD, original=CAPTURED,
                  timestamp="20191114160000", stats=bf.Stats())
    meta = rec.snapshots[0]["fields"]["raw_meta"]
    assert meta["refreshed_at"].startswith("2019-11-14T15:34:53")
    assert meta["capture"] == "20191114160000"


def test_a_story_already_held_keeps_its_own_url(rec):
    # The same story crawled live lives at /story/<slug>-<id>. Hanging a second
    # row off the short form the counter prints would split one article in two.
    rec.existing = ("art-abc", "https://www.lematin.ch/story/primes-10001941")
    cb.ingest_one(None, payload=PAYLOAD, original=CAPTURED,
                  timestamp="20191114160000", stats=bf.Stats())
    assert rec.articles[0]["aid"] == "art-abc"
    assert rec.articles[0]["canonical_url"] == "https://www.lematin.ch/story/primes-10001941"
    assert rec.snapshots[0]["article_id"] == "art-abc"


def test_an_unchanged_counter_is_not_a_new_measurement(rec):
    rec.accept = False          # the content hash was already stored
    stats = bf.Stats()
    assert not cb.ingest_one(None, payload=PAYLOAD, original=CAPTURED,
                             timestamp="20191201000000", stats=stats)
    assert stats.snapshots == 0


def test_a_body_of_another_shape_is_refused(rec):
    assert not cb.ingest_one(None, payload="<html>404</html>", original=CAPTURED,
                             timestamp="20191114160000", stats=bf.Stats())
    assert rec.snapshots == []


def test_the_filter_matches_the_endpoint_and_not_the_article():
    import re
    keep = re.compile(cb.PATTERN)
    assert keep.fullmatch("https://www.lematin.ch/api/communities/10001941")
    # Cache-busting query strings are common on this endpoint.
    assert keep.fullmatch("https://www.lematin.ch/api/communities/10001941?_=157")
    assert not keep.fullmatch("https://www.lematin.ch/story/primes-10001941")
    assert not keep.fullmatch("https://www.lematin.ch/api/communities/")


def test_the_counter_endpoint_is_not_offered_as_a_fetchable_kind():
    # ingest() would run the article parsers over JSON. Keeping "communities"
    # out of KINDS is what stops backfill_cli accepting --kind communities.
    assert cb.KIND not in bf.KINDS
