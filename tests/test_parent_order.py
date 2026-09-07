"""Writing a reply before the comment it answers destroyed whole threads.

`comment.parent_id` is a foreign key onto `comment.id`, the TX Astro endpoint
is asked for newestFirst, and an article's comments share one transaction. So a
single reply that arrived ahead of its parent took every comment on that
article down with it -- 169 articles across 24 heures and the Tribune before
this was caught.
"""
from mediatracker.pipeline import _parents_first


class C:
    def __init__(self, key, parent=None):
        self.source_key = key
        self.parent_source_key = parent

    def __repr__(self):
        return f"C({self.source_key}<-{self.parent_source_key})"


def order(cs):
    return [c.source_key for c in _parents_first(cs)]


def test_a_reply_listed_first_is_written_after_its_parent():
    # Exactly the newestFirst case, and exactly what broke.
    reply, parent = C("r", "p"), C("p")
    assert order([reply, parent]) == ["p", "r"]


def test_an_already_correct_order_is_left_alone():
    parent, reply = C("p"), C("r", "p")
    assert order([parent, reply]) == ["p", "r"]


def test_every_comment_is_emitted_exactly_once():
    cs = [C("c", "b"), C("b", "a"), C("a"), C("d")]
    got = order(cs)
    assert sorted(got) == ["a", "b", "c", "d"]
    assert len(got) == len(set(got))


def test_a_chain_comes_out_deepest_last():
    cs = [C("c", "b"), C("b", "a"), C("a")]
    got = order(cs)
    assert got.index("a") < got.index("b") < got.index("c")


def test_a_parent_outside_the_batch_does_not_stall_the_reply():
    # The reply is real and must still be written; only its link is in doubt,
    # and that is decided later against the database.
    cs = [C("r", "absent")]
    assert order(cs) == ["r"]


def test_a_cycle_terminates_and_keeps_everything():
    # The platform should never produce one; unbounded recursion on bad input
    # would take the daemon down, which is a worse failure than a wrong order.
    a, b = C("a", "b"), C("b", "a")
    got = order([a, b])
    assert sorted(got) == ["a", "b"]


def test_comments_without_a_key_are_kept():
    # Older eras have no per-comment id and get a synthetic one downstream.
    cs = [C(None), C("a"), C("b", "a")]
    assert len(_parents_first(cs)) == 3


def test_the_order_is_stable_for_unrelated_comments():
    cs = [C("a"), C("b"), C("c")]
    assert order(cs) == ["a", "b", "c"]
