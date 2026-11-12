"""The order a person's comment history comes back in, and which end survives.

A commenter page shows what someone has been writing lately, so the history
arrives newest first. That is not only a display choice: 720 nicknames have
written more than a thousand comments and the busiest has 31,165, so when the
list is cut, the end that is kept is the part the reader came for.
"""
from mediatracker import db


COLS = ("id", "article_id", "author_nick", "posted_at", "body_text",
        "like_count", "headline", "journal", "origin", "canonical_url")


def _conn(rows):
    """A connection whose one query answers with `rows`, in column order."""
    class _Cur:
        description = [(c,) for c in COLS]
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None): self.sql = " ".join(sql.split())
        def fetchall(self): return rows

    class _Conn:
        def cursor(self): return _Cur()

    return _Conn()


def _row(cid, posted_at, journal="lematin", url="https://x/a"):
    return (cid, f"a{cid}", "nick", posted_at, "text", 0, "head", journal,
            "crawl", url)


def test_oldest_first_stays_the_default():
    """The analysis passes read the whole history and expect reading order."""
    rows = [_row(1, "2020-01-01"), _row(2, "2024-01-01"), _row(3, "2022-01-01")]
    out = db.comments_for_nicks(_conn(rows), ["nick"])
    assert [c["posted_at"] for c in out] == ["2020-01-01", "2022-01-01", "2024-01-01"]


def test_newest_first_when_asked():
    rows = [_row(1, "2020-01-01"), _row(2, "2024-01-01"), _row(3, "2022-01-01")]
    out = db.comments_for_nicks(_conn(rows), ["nick"], newest_first=True)
    assert [c["posted_at"] for c in out] == ["2024-01-01", "2022-01-01", "2020-01-01"]


def test_the_cut_keeps_the_newest_end():
    """The point of the flag: a truncated history must not lose the present."""
    rows = [_row(i, f"20{i:02d}-01-01") for i in range(10, 30)]
    out = db.comments_for_nicks(_conn(rows), ["nick"], limit=3, newest_first=True)
    assert [c["posted_at"] for c in out] == ["2029-01-01", "2028-01-01", "2027-01-01"]


def test_undated_comments_sort_last_in_both_directions():
    """A capture whose timestamp was never recovered belongs at neither end.

    Sorting them first would be the whole budget spent on the rows that say
    least, and reversing a "None sorts last" key is exactly what does that.
    """
    rows = [_row(1, None), _row(2, "2024-01-01"), _row(3, "2020-01-01")]
    asc = db.comments_for_nicks(_conn(rows), ["nick"])
    desc = db.comments_for_nicks(_conn(rows), ["nick"], newest_first=True)
    assert asc[-1]["posted_at"] is None
    assert desc[-1]["posted_at"] is None
    assert [c["posted_at"] for c in desc[:2]] == ["2024-01-01", "2020-01-01"]


def test_undated_comments_do_not_consume_the_whole_budget():
    rows = [_row(i, None) for i in range(5)] + [_row(9, "2024-01-01")]
    out = db.comments_for_nicks(_conn(rows), ["nick"], limit=2, newest_first=True)
    assert out[0]["posted_at"] == "2024-01-01"


def test_the_source_url_travels_with_each_comment():
    """The page links out to the article as published, so the url must be read."""
    out = db.comments_for_nicks(_conn([_row(1, "2024-01-01")]), ["nick"])
    assert out[0]["canonical_url"] == "https://x/a"


def test_the_query_asks_for_the_canonical_url():
    conn = _conn([])
    cur = conn.cursor()
    conn.cursor = lambda: cur          # keep the one we can read back
    db.comments_for_nicks(conn, ["nick"])
    assert "a.canonical_url" in cur.sql


def test_no_nicknames_is_not_a_query():
    assert db.comments_for_nicks(_conn([]), []) == []
