"""The wayback body backfill.

The job re-reads captures this project already paid for, so the tests are about
not wasting that: attempt each row once, never lose one to a bad night, and
never write a body onto the wrong record.
"""
from __future__ import annotations

import pytest

from mediatracker import body_backfill as bb
from mediatracker.wayback import GaveUp


class FakeCursor:
    def __init__(self, conn): self.conn = conn; self._rows = []
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None):
        self.conn.sql.append((" ".join(sql.split()), params))
        s = sql.lower()
        if "select 1 from article_snapshot" in s:
            self._rows = [(1,)] if self.conn.hash_exists else []
        elif "update article_snapshot" in s:
            self.conn.updates.append(params)
            self._rows = []
    def fetchone(self): return self._rows[0] if self._rows else None
    def fetchall(self): return self._rows


class FakeConn:
    def __init__(self, hash_exists=False):
        self.sql = []; self.updates = []; self.commits = 0
        self.hash_exists = hash_exists
    def cursor(self): return FakeCursor(self)
    def commit(self): self.commits += 1


class FakeClient:
    """Serves pages by capture id; raises what the test asks it to."""
    def __init__(self, pages, raises=None):
        self.pages = pages; self.raises = raises or {}; self.asked = []
    def snapshot(self, timestamp, original):
        self.asked.append((timestamp, original))
        if timestamp in self.raises:
            raise self.raises[timestamp]
        return self.pages.get(timestamp, "")


LONG = ("Le Conseil federal a decide vendredi de prolonger les mesures "
        "sanitaires jusqu au printemps prochain malgre l opposition d une "
        "partie du Parlement et des cantons romands concernes. ") * 4
# The era gate refuses anything that is not a Newsnetz page, so fixtures that
# mean to exercise the parsing paths have to look like one.
NEWSNETZ = "<meta name='generator' content='newsnetz'>"
ARTICLE = f"<html><head>{NEWSNETZ}</head><body><div class='art'><p>{LONG}</p></div></body></html>"

def row(snap=1, cap="20150101000000", url="http://www.lematin.ch/123/story/1"):
    return (snap, "aid-1", "lematin", cap, url, url, "Un titre", 12)


def test_a_recovered_body_is_written(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(bb, "candidates", lambda c, **k: [row()])
    out = bb.run(conn, client=FakeClient({"20150101000000": ARTICLE}))
    assert out["bodies"] == 1
    assert any("body_text" in (s or "") for s, _ in conn.sql)


def test_a_page_without_a_body_is_marked_not_retried(monkeypatch):
    """The 2017-2019 captures are 2 KB stubs. Retrying them every night would
    spend the run on rows that can never yield anything."""
    conn = FakeConn()
    monkeypatch.setattr(bb, "candidates", lambda c, **k: [row()])
    stub = f"<html><head>{NEWSNETZ}</head><body><p>Trop court.</p></body></html>"
    out = bb.run(conn, client=FakeClient({"20150101000000": stub}))
    assert out["no_body"] == 1 and out["bodies"] == 0
    assert any(bb.MARKER in str(p) and "no_body" in str(p) for _, p in conn.sql)


def test_a_fetch_failure_is_marked_so_the_row_is_not_retried_forever(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(bb, "candidates", lambda c, **k: [row()])
    client = FakeClient({}, raises={"20150101000000": TimeoutError("slow")})
    out = bb.run(conn, client=client)
    assert out["failed"] == 1
    assert any("failed:TimeoutError" in str(p) for _, p in conn.sql)


def test_giving_up_does_not_mark_the_row(monkeypatch):
    """GaveUp means the archive is unwell, not that this article has no body.
    Marking it would quietly lose an article to one bad night."""
    conn = FakeConn()
    monkeypatch.setattr(bb, "candidates", lambda c, **k: [row()])
    client = FakeClient({}, raises={"20150101000000": GaveUp("stop")})
    out = bb.run(conn, client=client)
    assert out["bodies"] == 0 and out["failed"] == 0
    assert not any(bb.MARKER in str(p) for _, p in conn.sql if p)


def test_the_plain_article_url_is_fetched_not_the_comment_view(monkeypatch):
    """The whole point: the stored capture is of a comment view, which has no
    body. Fetching that again would recover nothing, exactly as before."""
    conn = FakeConn()
    commented = "http://www.lematin.ch/123/print.html?comments=1"
    monkeypatch.setattr(bb, "candidates", lambda c, **k: [row(url=commented)])
    client = FakeClient({"20150101000000": ARTICLE})
    bb.run(conn, client=client)
    asked_url = client.asked[0][1]
    assert "comments=1" not in asked_url and "print.html" not in asked_url


def test_the_stored_capture_timestamp_is_reused(monkeypatch):
    """One request per article. Asking CDX first was measured at 18.2s/article
    against 3.1s for this."""
    conn = FakeConn()
    monkeypatch.setattr(bb, "candidates", lambda c, **k: [row(cap="20081115093000")])
    client = FakeClient({"20081115093000": ARTICLE})
    bb.run(conn, client=client)
    assert client.asked[0][0] == "20081115093000"
    assert len(client.asked) == 1


def test_an_identical_snapshot_is_not_overwritten(monkeypatch):
    """content_hash is UNIQUE per article; a collision means this exact content
    is already recorded."""
    conn = FakeConn(hash_exists=True)
    monkeypatch.setattr(bb, "candidates", lambda c, **k: [row()])
    out = bb.run(conn, client=FakeClient({"20150101000000": ARTICLE}))
    assert out["conflict"] == 1 and out["bodies"] == 0
    assert conn.updates and all("body_text" not in str(u) for u in conn.updates)


def test_the_budget_stops_the_run_and_keeps_what_it_did(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(bb, "candidates", lambda c, **k: [row(snap=i) for i in range(5)])
    with pytest.raises(bb.OutOfTime):
        bb.run(conn, client=FakeClient({}), max_hours=-1)
    assert conn.commits >= 0


def test_progress_is_committed_per_row(monkeypatch):
    """A killed stint must not lose the hours it already spent."""
    conn = FakeConn()
    monkeypatch.setattr(bb, "candidates",
                        lambda c, **k: [row(snap=i, cap=f"2015010100000{i}") for i in range(3)])
    pages = {f"2015010100000{i}": ARTICLE for i in range(3)}
    bb.run(conn, client=FakeClient(pages))
    assert conn.commits == 3


PORTAL = ("<html><body><div><p>Gagnez un bon de 500 francs grace a vos qualites "
          "de footballeur amateur et venez le retirer en nos bureaux.</p>"
          "<p>Le site America.com va etre propose aux encheres. Il pourrait "
          "battre tous les records de vente connus a ce jour. Suite</p></div></body></html>")


def test_a_portal_page_is_refused_not_parsed(monkeypatch):
    """Pre-2012 Le Matin wrapped a dozen unrelated teasers in bare <p>. One such
    page yielded a 9,640-char 'body' of a dozen stories, and nothing downstream
    could have told it from an article."""
    conn = FakeConn()
    monkeypatch.setattr(bb, "candidates", lambda c, **k: [row()])
    out = bb.run(conn, client=FakeClient({"20150101000000": PORTAL}))
    assert out["wrong_era"] == 1 and out["bodies"] == 0
    assert any("not_newsnetz" in str(p) for _, p in conn.sql if p)


def test_a_newsnetz_article_still_passes_the_gate(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(bb, "candidates", lambda c, **k: [row()])
    page = ARTICLE.replace("<body>", "<body><div class='commentRedesign'></div>")
    out = bb.run(conn, client=FakeClient({"20150101000000": page}))
    assert out["bodies"] == 1


def test_min_capture_is_pushed_into_the_query():
    """Le Matin's pre-2012 captures are 63,852 rows the gate always refuses.
    Asking the archive for them anyway would cost 54 hours and 64,000 requests
    to learn what the capture date already says."""
    conn = FakeConn()
    bb.candidates(conn, min_capture="2012")
    sql, params = conn.sql[-1]
    assert "raw_meta->>'capture' >= %(min_capture)s" in sql
    assert params["min_capture"] == "2012"


def test_without_min_capture_nothing_is_filtered_by_date():
    conn = FakeConn()
    bb.candidates(conn)
    sql, params = conn.sql[-1]
    assert "min_capture" not in sql and "min_capture" not in params


def test_a_failed_row_is_skipped_by_default():
    """One stint must not loop on a row the archive would not serve it."""
    conn = FakeConn()
    bb.candidates(conn)
    sql, _ = conn.sql[-1]
    assert "NOT (s.raw_meta ? %(marker)s)" in sql
    assert "failed:" not in sql


def test_retry_failed_reopens_them():
    """Most failures are URLError -- the archive briefly unreachable, which is
    not a verdict about the article. Retiring those permanently would silently
    drop about 6% of the backlog."""
    conn = FakeConn()
    bb.candidates(conn, retry_failed=True)
    sql, _ = conn.sql[-1]
    assert "LIKE 'failed:%'" in sql or "LIKE 'failed:%%'" in sql
