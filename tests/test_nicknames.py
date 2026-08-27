"""Reading a handle: what it must catch, and what it must refuse to say."""
from mediatracker import nicknames as nn


def test_one_handle_spelled_several_ways_reads_the_same():
    # A commenter's own variants differ in case, spacing and punctuation, and
    # they are one handle. Accents fold too: the corpus holds both spellings.
    for v in ("oscar_the_grouch", "oscarthegrouch", "Oscar The Grouch",
              "OSCAR-THE-GROUCH"):
        assert nn.read(v)["refers_to"] == "Oscar the Grouch"
    assert nn.read("Mélenchon")["refers_to"] == nn.read("melenchon")["refers_to"]


def test_a_year_bolted_on_does_not_hide_the_reference():
    # Decoration goes on either side: the lexicon may hold the bare form and
    # the handle the decorated one, or the reverse.
    assert nn.read("icare4")["matched"] == "exact"
    assert nn.read("icare9")["matched"] == "stem"
    assert nn.read("Mandragore")["refers_to"] == "mandrake"      # lexicon has the digits
    assert nn.read("Sinalco12")["refers_to"] == "Sinalco"


def test_an_ordinary_looking_personal_name_gets_no_reading():
    # Deliberate silence. Some of these are invented and some may be the
    # writer's own, the string cannot tell them apart, and a guess would either
    # assert an identity this study refuses to record or simply be wrong.
    for n in ("Georges Alexandre", "Amandine Clerc", "Yann Burmann", "Gilles M"):
        assert nn.read(n) is None


def test_nothing_recognised_returns_nothing_rather_than_a_guess():
    for n in ("", None, "GB1204", "zzzz-unlikely-handle-zzzz", "42"):
        assert nn.read(n) is None


def test_the_device_says_how_the_reference_is_used():
    # Wordplay is the interesting case: it asks the reader to complete a
    # reference rather than just recognise one.
    assert nn.read("AlainDeloin")["device"] == "pun"
    assert nn.read("Alain@Térieur")["device"] == "pun"
    assert nn.read("Chacureuil")["device"] == "blend"
    assert nn.read("WilhelmTell007")["device"] == "combined"
    assert nn.read("Colargol")["device"] == "borrowed"
    assert {v["device"] for v in nn.LEXICON.values()} <= set(nn.DEVICES)
    assert {v["domain"] for v in nn.LEXICON.values()} <= set(nn.DOMAINS)


def test_a_person_inherits_the_culture_of_whichever_handle_carries_it():
    rows = [{"label": "Someone Plain", "aliases": ["a-plain-handle", "Colargol"]},
            {"label": "Colargol", "aliases": ["Colargol"]},
            {"label": "Nobody", "aliases": ["nothing-here"]}]
    nn.annotate(rows, field="label", aliases="aliases")
    assert rows[0]["reference"]["refers_to"] == "Colargol"
    assert rows[0]["reference"]["via"] == "Colargol"      # and says which one
    assert "via" not in rows[1]["reference"]             # its own label sufficed
    assert rows[2]["reference"] is None


def test_coverage_reports_the_fraction_not_just_the_hits():
    c = nn.coverage(["Colargol", "GB1204", "Peter861", "Rabelais"])
    assert c == {"total": 4, "matched": 2,
                 "domains": {"television": 1, "literature": 1}}
