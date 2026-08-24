"""A nickname identifies someone only inside one comment community.

Two rules are being pinned here, and they pull in opposite directions:

  * Different platforms, different people. "Marie03" on Le Matin and "Marie03"
    on 24 heures are two subjects — separate registrations, nothing linking the
    accounts — so they must never be pooled into one.

  * One backend, one person. 24 heures and the Tribune de Genève serve the same
    article id with the same comment thread, UUIDs and all. A comment reached
    through either title is one comment, and counting it twice would double the
    volume of everyone who writes on syndicated stories.
"""
from __future__ import annotations

from mediatracker import ids, sources


def test_titles_sharing_a_backend_share_a_community():
    tdg = sources.get("tdg")()
    vqh = sources.get("24heures")()
    lematin = sources.get("lematin")()

    assert tdg.community_key == vqh.community_key == "tx-romandie"
    # Le Matin is a different platform with its own comment backend.
    assert lematin.community_key == "lematin"
    assert lematin.community_key != tdg.community_key


def test_a_title_defaults_to_its_own_community():
    """Adding a journal must not accidentally merge its public into another's."""
    class Solo(sources.Source):
        slug = "solo"

    assert Solo().community_key == "solo"
    assert Solo().comment_ids_global is False


def test_shared_comment_id_ignores_which_title_carried_the_article():
    """The same thread is served by both sites, so the id may not depend on the
    article row we happened to reach it through."""
    uuid = "50cf0f66-1111-2222-3333-444455556666"
    via_24h = ids.shared_comment_id("tx-romandie", uuid)
    via_tdg = ids.shared_comment_id("tx-romandie", uuid)
    assert via_24h == via_tdg

    # The old per-article scheme would have produced two different ids.
    art_a = ids.article_id("24heures", "https://www.24heures.ch/x-633967257619")
    art_b = ids.article_id("tdg", "https://www.tdg.ch/x-633967257619")
    assert art_a != art_b
    assert ids.comment_id("24heures", art_a, uuid) != ids.comment_id("tdg", art_b, uuid)


def test_communities_do_not_collide_on_the_same_source_id():
    """Numeric comment ids from different backends can coincide; the community
    is what keeps them apart."""
    assert ids.shared_comment_id("tx-romandie", "12345") != ids.shared_comment_id("lematin", "12345")


def test_shared_and_per_article_schemes_never_collide():
    """Le Matin keeps the per-article scheme, so the two must stay disjoint or a
    migration would silently merge unrelated comments."""
    art = ids.article_id("lematin", "https://www.lematin.ch/story/x-1")
    assert ids.shared_comment_id("lematin", "9") != ids.comment_id("lematin", art, "9")
