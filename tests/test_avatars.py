"""The picture on a commenter's account, and what it can and cannot prove.

The point of keeping avatars is narrow: an uploaded one is unique to the
account that uploaded it, so it survives a rename and links the new nickname to
the old. Everything here guards the line between that and the two populations
that look the same in the data and mean nothing -- the placeholder nearly
everyone used to wear, and the small palette of presets the site offers.
"""
import pytest

from mediatracker import avatars

HOST = "https://api.20min.ch"
UPLOADED = f"{HOST}/media/static/profile/bc56d53cf31c42ad9b1715f3f1ba7beb.png"
DEFAULT = (f"{HOST}/user/static/anonym."
           "1ae51b22a335a57ef191f7db73d053a1b3b1b2ad3cf74171fd4c1516adf5818d.png")
PRESET = (f"{HOST}/user/static/15@4x."
          "f92a27eb61d16d5cc1f954d5a5a5151a158fd0f9b119b3fd8d7a6ff4bff0d82c.png")


# --------------------------------------------------------------------------- #
# telling the three populations apart
# --------------------------------------------------------------------------- #

def test_an_uploaded_picture_is_recognised_and_keyed_by_its_own_name():
    """These are the only rows that identify anybody."""
    assert avatars.classify(UPLOADED) == (
        "uploaded", "bc56d53cf31c42ad9b1715f3f1ba7beb")


def test_the_placeholder_is_not_mistaken_for_a_picture_someone_chose():
    """241 of 244 nicknames on one 2021 thread wore this.

    Counting it as a link would join most of the corpus into one person.
    """
    kind, _ = avatars.classify(DEFAULT)
    assert kind == "default"


def test_a_preset_is_kept_but_marked_as_the_weak_signal_it_is():
    kind, key = avatars.classify(PRESET)
    assert kind == "preset"
    assert key.startswith("15@4x.")


def test_every_population_gets_a_distinct_kind():
    kinds = {avatars.classify(u)[0] for u in (UPLOADED, DEFAULT, PRESET)}
    assert kinds == {"uploaded", "default", "preset"}


def test_the_key_is_the_file_so_changing_the_picture_changes_the_key():
    a = avatars.classify(f"{HOST}/media/static/profile/" + "a" * 32 + ".png")
    b = avatars.classify(f"{HOST}/media/static/profile/" + "b" * 32 + ".png")
    assert a[1] != b[1]


def test_the_extension_is_dropped_so_a_reencoding_is_not_a_new_picture():
    png = avatars.classify(f"{HOST}/media/static/profile/{'c' * 32}.png")
    jpg = avatars.classify(f"{HOST}/media/static/profile/{'c' * 32}.jpg")
    assert png[1] == jpg[1]


def test_a_shape_we_have_never_seen_is_kept_rather_than_dropped():
    """A new url shape is worth noticing, not worth guessing at."""
    kind, key = avatars.classify("https://example.ch/img/someone.webp")
    assert kind == "other" and key == "someone"


@pytest.mark.parametrize("bad", [None, "", "not a url", 17, {}])
def test_nothing_useful_in_means_nothing_out(bad):
    assert avatars.classify(bad) is None or avatars.classify(bad)[0] == "other"


def test_a_query_string_does_not_change_the_key():
    """A cache-buster on the url must not read as a changed picture."""
    plain = avatars.classify(UPLOADED)
    busted = avatars.classify(UPLOADED + "?v=2")
    assert plain == busted


# --------------------------------------------------------------------------- #
# collapsing a thread
# --------------------------------------------------------------------------- #

class _C:
    def __init__(self, nick, url):
        self.author_nick, self.avatar_url = nick, url


def test_a_thread_yields_one_row_per_author_not_one_per_comment():
    """A busy thread would otherwise be a thousand upserts for 200 facts."""
    thread = [_C("JeanMoulin", UPLOADED)] * 40 + [_C("Bourdelle", PRESET)] * 12
    pairs = avatars.pairs_of(thread)
    assert set(pairs) == {"JeanMoulin", "Bourdelle"}
    assert pairs["JeanMoulin"] == ("uploaded", "bc56d53cf31c42ad9b1715f3f1ba7beb",
                                   UPLOADED)


def test_comments_without_a_nickname_or_a_picture_are_simply_skipped():
    pairs = avatars.pairs_of([_C(None, UPLOADED), _C("Someone", None),
                              _C("Real", UPLOADED)])
    assert set(pairs) == {"Real"}


def test_the_parser_no_longer_discards_the_avatar():
    """The regression this whole table exists to end."""
    from mediatracker.sources.tamedia import parse_comments
    out, _ = parse_comments({"comments": [{
        "id": "1", "authorNickname": "JeanMoulin", "body": "x",
        "authorAvatar": {"light": UPLOADED, "dark": UPLOADED},
        "createdAt": "2026-11-20T12:33:04.160Z", "replies": [],
    }], "totalCount": 1})
    assert out[0].avatar_url == UPLOADED
    assert avatars.classify(out[0].avatar_url)[0] == "uploaded"


def test_a_comment_with_no_avatar_in_the_payload_parses_fine():
    from mediatracker.sources.tamedia import parse_comments
    out, _ = parse_comments({"comments": [
        {"id": "1", "authorNickname": "N", "body": "x", "replies": []}]})
    assert out[0].avatar_url is None


# --------------------------------------------------------------------------- #
# the pipeline writing them, and never dying for them
# --------------------------------------------------------------------------- #

def test_the_pipeline_records_one_upsert_per_author(monkeypatch):
    from mediatracker import pipeline as pl

    seen = {}

    def record(conn, *, journal_id, pairs):
        seen["jid"], seen["pairs"] = journal_id, pairs
        return len(pairs)

    monkeypatch.setattr(pl.db, "record_avatars", record)
    p = pl.Pipeline(conn=object(), blobs=None, store=None, fetcher=None)
    stats = pl.IngestStats()
    p._write_avatars(7, [_C("JeanMoulin", UPLOADED)] * 3 + [_C("B", PRESET)], stats)

    assert seen["jid"] == 7
    assert set(seen["pairs"]) == {"JeanMoulin", "B"}
    assert stats.avatars_new == 2


def test_a_picture_that_cannot_be_filed_never_costs_us_the_thread(monkeypatch):
    """This is an addition to what a scan collects, not a new way to lose it."""
    from mediatracker import pipeline as pl

    def boom(conn, **kw):
        raise RuntimeError("no")

    monkeypatch.setattr(pl.db, "record_avatars", boom)
    p = pl.Pipeline(conn=object(), blobs=None, store=None, fetcher=None)
    stats = pl.IngestStats()
    p._write_avatars(1, [_C("N", UPLOADED)], stats)      # must not raise
    assert stats.avatars_new == 0


def test_nothing_is_written_when_running_without_postgres(monkeypatch):
    from mediatracker import pipeline as pl

    called = []
    monkeypatch.setattr(pl.db, "record_avatars",
                        lambda *a, **k: called.append(1) or 0)
    p = pl.Pipeline(conn=None, blobs=None, store=None, fetcher=None)
    p._write_avatars(1, [_C("N", UPLOADED)], pl.IngestStats())
    assert called == []


# --------------------------------------------------------------------------- #
# the write itself
# --------------------------------------------------------------------------- #

class _Cur:
    """Enough of a cursor to see what SQL was sent."""
    description = [("is_new",)]

    def __init__(self, log, answers):
        self.log, self.answers = log, answers

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def fetchone(self): return (self.answers.pop(0),) if self.answers else (False,)


class _Conn:
    def __init__(self, answers=None):
        self.log, self.answers = [], list(answers or [])

    def cursor(self): return _Cur(self.log, self.answers)


def test_an_unchanged_picture_only_moves_the_date_and_counts_as_nothing_new():
    from mediatracker import db
    conn = _Conn(answers=[False])
    new = db.record_avatars(conn, journal_id=2,
                            pairs={"N": ("uploaded", "abc", "u")})
    assert new == 0
    sql, params = conn.log[0]
    assert "ON CONFLICT (journal_id, author_nick, avatar_key)" in sql
    assert "DO UPDATE SET last_seen = now()" in sql
    assert params == (2, "N", "abc", "uploaded", "u")


def test_a_picture_seen_for_the_first_time_is_reported_as_new():
    from mediatracker import db
    conn = _Conn(answers=[True, False])
    assert db.record_avatars(conn, journal_id=1, pairs={
        "A": ("uploaded", "k1", "u1"), "B": ("preset", "k2", "u2")}) == 1


def test_an_empty_thread_touches_the_database_not_at_all():
    from mediatracker import db
    conn = _Conn()
    assert db.record_avatars(conn, journal_id=1, pairs={}) == 0
    assert conn.log == []


def test_the_succession_query_ignores_presets_and_the_placeholder():
    """A preset is a palette choice; it must never read as a rename."""
    import inspect

    from mediatracker import db
    sql = inspect.getsource(db.avatar_successions)
    assert "kind = 'uploaded'" in sql
    assert "count(DISTINCT a.author_nick) > 1" in sql
