"""Reading the counter widget the archive kept for the years whose words are gone."""
import json

from mediatracker import community_counts as cc

BODY = json.dumps({"communities": [
    {"type": "dark", "count": 0, "article_id": "5dcd1942ab5c371e9c000001",
     "refresh": "14.11.2019 15:34:53"},
    {"type": "facebook", "count": 183, "article_id": "5dcd1942ab5c371e9c000001",
     "url": "https://www.lematin.ch/10001941",
     "title": "Les primes maladie impay&eacute;es ne p&eacute;naliseront plus les enfants",
     "refresh": "14.11.2019 15:34:53"},
    {"type": "twitter", "count": 0, "article_id": "5dcd1942ab5c371e9c000001"},
    {"type": "comment", "count": 3, "article_id": "5dcd1942ab5c371e9c000001",
     "refresh": "14.11.2019 15:34:53"},
]})
URL = "https://www.lematin.ch/api/communities/10001941"


def test_the_thread_size_and_the_headline_both_survive():
    r = cc.parse(BODY, captured_url=URL)
    assert r["source_key"] == "10001941"
    assert r["comment_count"] == 3
    assert r["headline"].startswith("Les primes maladie impayées")   # entities decoded
    assert r["shares"]["facebook"] == 183


def test_a_widget_that_did_not_report_is_not_a_thread_of_zero():
    # "no comment row in the payload" and "a comment row saying 0" are
    # different facts. Only the second is a measurement, so the first is None.
    silent = json.dumps({"communities": [
        {"type": "facebook", "count": 4, "url": "https://www.lematin.ch/999111"}]})
    r = cc.parse(silent, captured_url="https://www.lematin.ch/api/communities/999111")
    assert r["comment_count"] is None
    said_none = json.dumps({"communities": [
        {"type": "comment", "count": 0, "url": "https://www.lematin.ch/999111"}]})
    assert cc.parse(said_none, captured_url=URL)["comment_count"] == 0


def test_the_story_id_comes_from_the_path_when_no_row_carries_a_url():
    bare = json.dumps({"communities": [{"type": "comment", "count": 7}]})
    r = cc.parse(bare, captured_url="https://www.lematin.ch/api/communities/10052475")
    assert r["source_key"] == "10052475"
    assert r["comment_count"] == 7


def test_the_publishers_own_refresh_time_is_kept():
    # Nearer the truth than the capture timestamp, which can be hours later.
    r = cc.parse(BODY, captured_url=URL)
    ts = r["refreshed_at"]
    assert (ts.year, ts.month, ts.day, ts.hour) == (2019, 11, 14, 15)
    assert ts.utcoffset().total_seconds() == 3600          # CET in November


def test_a_body_that_is_not_this_shape_is_refused():
    assert cc.parse("not json", captured_url=URL) is None
    assert cc.parse(json.dumps({"communities": []}), captured_url=URL) is None
    # and no story id anywhere means no usable row
    assert cc.parse(json.dumps({"communities": [{"type": "comment", "count": 1}]}),
                    captured_url="https://www.lematin.ch/api/whatever") is None
