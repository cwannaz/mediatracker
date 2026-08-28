"""Hand-written records on a subject: notes, and the same writer elsewhere.

These test the judgement in the module, not the SQL — what a persona is allowed
to inherit from its handles, what is refused before it ever reaches the
database, and how a pasted link is read.
"""
import pytest

from mediatracker import db


class _NoDb:
    """A connection that fails loudly. Anything refused on the way in must be
    refused before a cursor is opened, or a bad row is a round trip away."""
    def cursor(self):
        raise AssertionError("validation should have happened before the database")


def test_a_persona_inherits_what_was_recorded_against_its_handles():
    # Linking two nicknames says they are one writer. If the link hid what was
    # already written about either of them, the link would destroy evidence.
    pairs = db.note_subjects("persona", 7, ["Colargol", "Barbatruk"])
    assert ("persona", "7") in pairs
    assert ("nick", "Colargol") in pairs and ("nick", "Barbatruk") in pairs


def test_a_handle_does_not_inherit_its_personas_notes():
    # The reverse does not hold: a note on the person is about every handle,
    # and showing it under one of them would claim more than the note does.
    assert db.note_subjects("nick", "Colargol", ["Colargol", "Barbatruk"]) \
        == [("nick", "Colargol")]


def test_an_empty_note_never_reaches_the_database():
    for body in ("", "   ", "\n\t ", None):
        with pytest.raises(ValueError):
            db.add_note(_NoDb(), kind="nick", key="x", body=body, community="lematin")


def test_an_account_needs_somewhere_to_look():
    # A row with neither a link nor a handle records a belief, not an
    # observation, and cannot be checked by anyone later.
    with pytest.raises(ValueError):
        db.add_account(_NoDb(), kind="nick", key="x", community="lematin")
    with pytest.raises(ValueError):
        db.add_account(_NoDb(), kind="nick", key="x", community="lematin",
                       url="  ", handle="")


def test_the_platform_is_read_off_the_link():
    # Asked for separately, the two eventually disagree.
    assert db.account_platform("https://www.facebook.com/randall.dibiaski") == "facebook"
    assert db.account_platform("https://m.facebook.com/someone") == "facebook"
    assert db.account_platform("https://twitter.com/x") == "x"
    assert db.account_platform("https://youtu.be/abc") == "youtube"


def test_an_unknown_host_becomes_itself_rather_than_other():
    # A personal blog is a real answer; "other" would throw away the only
    # informative part of the URL.
    assert db.account_platform("https://blog.example.ch/moi") == "blog.example.ch"
    assert db.account_platform("") == "other"
    assert db.account_platform(None) == "other"


def test_an_explicit_platform_wins_over_the_link():
    # Some links say nothing useful — an archive, a shortener, a screenshot.
    assert db.account_platform("https://archive.org/x", "Facebook") == "facebook"
