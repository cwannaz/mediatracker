"""Out-group epithets as a signal, and the two ways of misreading them.

The idea came from Cedric: a writer who answers every opponent with "les
fachos" has placed themselves, and "les gauchistes" is the same referent with
the derogation turned down. Measuring it on the corpus showed the obvious
version is wrong -- users of `fachos` are 3x MORE likely than chance to also
use `gauchos` -- so what these tests mostly guard is the honesty of what the
module claims: aim never travels without its counts, and rarity is never
answered from a guess.
"""
import pytest

from mediatracker import markers


# --------------------------------------------------------------------------- #
# the lexicon's shape
# --------------------------------------------------------------------------- #

def test_aim_records_the_target_not_the_speaker():
    """Building the speaker in would assume the thing to be inferred."""
    assert markers.BY_NAME["fachos"].aim == "right"
    assert markers.BY_NAME["gauchos"].aim == "left"


def test_slurs_and_plain_labels_are_kept_apart():
    """'la gauche' names a side; 'les gauchos' insults one."""
    assert markers.BY_NAME["gauchistes"].heat == "slur"
    assert markers.BY_NAME["la-gauche"].heat == "label"
    assert markers.BY_NAME["extreme-droite"].heat == "label"


def test_every_marker_declares_a_known_aim_and_heat():
    for m in markers.MARKERS:
        assert m.aim in ("left", "right", "other"), m.name
        assert m.heat in ("slur", "label"), m.name
        assert m.forms, m.name


def test_names_are_unique():
    names = [m.name for m in markers.MARKERS]
    assert len(names) == len(set(names))


# --------------------------------------------------------------------------- #
# matching
# --------------------------------------------------------------------------- #

def test_it_finds_the_words_that_started_this():
    got = markers.scan("Les fachos n'aiment pas la lumière.")
    assert got == {"fachos": 1}


def test_singular_and_plural_both_count():
    assert markers.scan("un facho, des fachos")["fachos"] == 2


def test_accents_as_written_are_matched():
    assert "ecolos" in markers.scan("les écolos décident tout")
    assert "reacs" in markers.scan("ces réacs")


def test_a_marker_inside_a_longer_word_is_not_a_match():
    """The whole-word rule: 'fafiot' is not 'faf'."""
    assert markers.scan("un fafiot et des gauchissements") == {}


def test_hyphen_and_space_variants_are_one_marker():
    a = markers.scan("islamo-gauchistes")
    b = markers.scan("islamo gauchistes")
    assert "islamo-gauchistes" in a and "islamo-gauchistes" in b


def test_a_label_is_matched_without_being_counted_as_a_slur():
    counts = markers.scan("la gauche a voté avec la droite")
    assert set(counts) == {"la-gauche", "la-droite"}
    assert markers.aim(counts)["slurs"] == 0


def test_nothing_in_nothing_out():
    assert markers.scan(None) == {} and markers.scan("") == {}


# --------------------------------------------------------------------------- #
# aim: a number that must never travel alone
# --------------------------------------------------------------------------- #

def test_aiming_only_at_the_left_reads_positive():
    got = markers.aim({"gauchos": 3, "gauchistes": 2})
    assert got["index"] == 1.0 and got["aimed_left"] == 5


def test_aiming_only_at_the_right_reads_negative():
    assert markers.aim({"fachos": 4})["index"] == -1.0


def test_a_writer_who_slings_both_lands_in_the_middle():
    """The commonest real case: 307 writers use both, 3x more than chance."""
    got = markers.aim({"fachos": 5, "gauchos": 5})
    assert got["index"] == 0.0 and got["slurs"] == 10


def test_no_slurs_means_no_reading_rather_than_a_neutral_one():
    """Zero is a position; None is the absence of evidence for one."""
    got = markers.aim({"la-gauche": 9})
    assert got["index"] is None and got["slurs"] == 0


def test_the_counts_always_come_back_with_the_index():
    """At n=1 the index is +-1.0, which looks like certainty and is a coin toss."""
    got = markers.aim({"faf": 1})
    assert got["index"] == -1.0
    assert got["slurs"] == 1          # the caller can see it is one word


def test_unaimed_slurs_do_not_tilt_the_index():
    """'moutons' is thrown at whoever the writer thinks is credulous."""
    assert markers.aim({"moutons": 7})["index"] is None


# --------------------------------------------------------------------------- #
# rarity is measured, never guessed
# --------------------------------------------------------------------------- #

class _Cur:
    def __init__(self, row): self.row = row
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None): self.sql = sql
    def fetchone(self): return self.row


class _Conn:
    def __init__(self, row=None): self.row = row
    def cursor(self): return _Cur(self.row)


def test_an_unmeasured_marker_raises_instead_of_estimating():
    """mistakes.py: a narrow regex once made 27 handles look like a signature
    where the real base rate was 1,136. No default may paper over that."""
    with pytest.raises(markers.NotMeasured, match="measure"):
        markers.rarity(_Conn(None), "faf")


def test_a_measured_marker_returns_the_share_it_was_measured_against():
    import datetime as dt
    got = markers.rarity(_Conn((52, 106, 183_494, dt.datetime(2026, 11, 20))), "faf")
    assert got["writers"] == 52
    assert got["share"] == pytest.approx(52 / 183_494)


def test_the_fingerprint_cut_is_on_the_measured_count(monkeypatch):
    """A marker 3,372 people use places nobody; it must not reach the output."""
    monkeypatch.setattr(markers, "writer_markers",
                        lambda conn, nick: {"faf": 4, "gauchistes": 30})

    def fake_rarity(conn, name, *, scope="all"):
        n = {"faf": 52, "gauchistes": 3372}[name]
        return {"marker": name, "scope": scope, "writers": n, "comments": n,
                "writers_tot": 183_494, "measured_at": None,
                "share": n / 183_494}

    monkeypatch.setattr(markers, "rarity", fake_rarity)
    out = markers.fingerprint(None, "JeanMoulin", max_writers=500)
    assert [r["marker"] for r in out] == ["faf"]


def test_the_fingerprint_is_ordered_rarest_first(monkeypatch):
    monkeypatch.setattr(markers, "writer_markers",
                        lambda conn, nick: {"faf": 1, "gauchiasse": 1, "droitards": 1})
    counts = {"faf": 52, "gauchiasse": 3, "droitards": 152}
    monkeypatch.setattr(markers, "rarity", lambda conn, name, *, scope="all": {
        "marker": name, "scope": scope, "writers": counts[name], "comments": 1,
        "writers_tot": 100, "measured_at": None, "share": 0.0})
    out = markers.fingerprint(None, "x", max_writers=500)
    assert [r["marker"] for r in out] == ["gauchiasse", "faf", "droitards"]


# --------------------------------------------------------------------------- #
# the SQL and the Python agree
# --------------------------------------------------------------------------- #

def test_postgres_and_python_use_the_same_word_forms():
    """Two engines, one lexicon: a marker that matched here and not there
    would make every measured base rate wrong for that marker."""
    for m in markers.MARKERS:
        sql = markers.sql_pattern(m)
        assert sql.startswith(r"\m(?:") and sql.endswith(r")\M")
        for form in m.forms:
            assert form in sql


def test_the_sql_literal_quoter_doubles_any_apostrophe():
    """'droits-de-l'hommistes' carries one, and it reaches hand-built SQL."""
    assert markers._q("l'homme") == "'l''homme'"
    assert "''" in markers._q(markers.sql_pattern(
        markers.BY_NAME["droits-de-lhommistes"]))
