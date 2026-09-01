"""Two papers, two sitemap layouts, one walk.

Le Matin names one file per day. 24 heures and the Tribune name a few hundred
sub-sitemaps by opaque hash, each spanning weeks or years, and only the
<lastmod> on each record says when.
"""
import pytest

from mediatracker import sitemap_backfill as sb

HASHED = """<?xml version="1.0"?><urlset>
<url><loc>https://www.24heures.ch/story-a-635616243933</loc>
     <lastmod>2024-03-02T20:00:00.000Z</lastmod></url>
<url><loc>https://www.24heures.ch/story-b-521125933728</loc>
     <lastmod>2011-08-30T20:00:00.000Z</lastmod></url>
<url><loc>https://www.24heures.ch/story-c-111111111111</loc></url>
</urlset>"""

DATED = """<?xml version="1.0"?><urlset>
<url><loc>https://www.lematin.ch/story/x-1</loc></url>
<url><loc>https://www.lematin.ch/story/x-2</loc></url>
</urlset>"""


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "SITEMAP_DIR", tmp_path)
    return tmp_path


def test_a_record_carries_its_own_date(mirror):
    d = mirror / "24heures"; d.mkdir()
    (d / "3jg8rm45ksm4q.xml").write_text(HASHED)
    got = sb.entries_in(d / "3jg8rm45ksm4q.xml")
    assert got[0] == ("https://www.24heures.ch/story-a-635616243933", "2024-03-02")
    assert got[1][1] == "2011-08-30"


def test_an_undated_record_is_kept_as_undated(mirror):
    # Not dropped, and not filed under a guessed year either.
    d = mirror / "24heures"; d.mkdir()
    (d / "h.xml").write_text(HASHED)
    assert sb.entries_in(d / "h.xml")[2] == (
        "https://www.24heures.ch/story-c-111111111111", None)


def test_the_hashed_mirror_is_walked_newest_first(mirror):
    d = mirror / "24heures"; d.mkdir()
    (d / "h1.xml").write_text(HASHED)
    got = list(sb.iter_entries("24heures"))
    assert [u.rsplit("-", 1)[0][-7:] for u, _ in got] == ["story-a", "story-b", "story-c"]


def test_a_year_filter_reads_the_record_not_the_filename(mirror):
    d = mirror / "24heures"; d.mkdir()
    (d / "h1.xml").write_text(HASHED)
    got = list(sb.iter_entries("24heures", years=(2024,)))
    assert [u for u, _ in got] == ["https://www.24heures.ch/story-a-635616243933"]


def test_our_own_index_files_are_not_read_as_sitemaps(mirror):
    d = mirror / "24heures"; d.mkdir()
    (d / "h1.xml").write_text(HASHED)
    (d / "_index.xml").write_text("<sitemapindex><loc>nope</loc></sitemapindex>")
    assert [p.name for p in sb.hashed_files("24heures")] == ["h1.xml"]


def test_the_dated_layout_is_still_walked_by_filename(mirror):
    # Le Matin's day files carry no <lastmod>; the name is the date, and the
    # leg that has been running for hours depends on that path being untouched.
    d = mirror / "lematin"; d.mkdir()
    (d / "2026-08-30.xml").write_text(DATED)
    (d / "2026-08-29.xml").write_text(DATED)
    got = list(sb.iter_entries("lematin", years=(2026,)))
    assert len(got) == 4
    assert got[0][1] == "2026-08-30"     # newest day first
    assert got[2][1] == "2026-08-29"


def test_the_index_is_rebuilt_when_a_sub_sitemap_is_newer(mirror):
    import os, time
    d = mirror / "24heures"; d.mkdir()
    f = d / "h1.xml"
    f.write_text(HASHED)
    idx = sb.day_index("24heures")
    assert idx.read_text().count("\n") == 3
    f.write_text(HASHED.replace("</urlset>",
        "<url><loc>https://www.24heures.ch/story-d-999</loc>"
        "<lastmod>2025-01-01</lastmod></url></urlset>"))
    os.utime(f, (time.time() + 10, time.time() + 10))
    assert sb.day_index("24heures").read_text().count("\n") == 4
