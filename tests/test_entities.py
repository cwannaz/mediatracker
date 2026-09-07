"""Entity identity, and refusing to repair what the model got wrong."""
import json

from mediatracker import entities as e


def test_accents_and_case_are_the_same_place():
    # Counting these apart turns the commonest entity in a Swiss corpus into
    # three rare ones.
    assert e.normalise("Genève") == e.normalise("geneve") == e.normalise("GENÈVE")


def test_a_leading_article_is_not_part_of_the_name():
    assert e.normalise("Le Conseil fédéral") == e.normalise("Conseil fédéral")
    assert e.normalise("l'UDC") == e.normalise("UDC")


def test_trailing_punctuation_does_not_split_an_identity():
    assert e.normalise("Genève,") == e.normalise("Genève")
    assert e.normalise(' "Genève" ') == e.normalise("Genève")


def test_inner_whitespace_is_collapsed():
    assert e.normalise("Conseil    fédéral") == e.normalise("Conseil fédéral")


def test_distinct_entities_stay_distinct():
    assert e.normalise("Genève") != e.normalise("Genèvre")
    assert e.normalise("Berne") != e.normalise("Bern")


def test_an_empty_name_has_no_identity():
    assert e.normalise("") == ""
    assert e.normalise("  .,  ") == ""


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()
    def read(self):
        return self._b
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _fake_api(payload, monkeypatch):
    monkeypatch.setattr(e.urllib.request, "urlopen",
                        lambda *a, **k: _Resp(payload))


def _tool_reply(articles):
    return {"content": [{"type": "tool_use", "name": "record_entities",
                         "input": {"articles": articles}}],
            "usage": {"input_tokens": 10, "output_tokens": 5}}


def test_a_well_formed_reply_is_read(monkeypatch):
    _fake_api(_tool_reply([
        {"id": 1, "entities": [{"name": "Genève", "kind": "place"}]}]), monkeypatch)
    got = e.extract([{"title": "t", "body": "b"}], key="k")
    assert got[1] == [{"name": "Genève", "kind": "place"}]


def test_an_unknown_kind_is_dropped_not_guessed(monkeypatch):
    # A repaired type is indistinguishable later from one the model actually
    # read, which would make the whole table unciteable.
    _fake_api(_tool_reply([
        {"id": 1, "entities": [{"name": "Genève", "kind": "city"},
                               {"name": "UDC", "kind": "organization"}]}]), monkeypatch)
    got = e.extract([{"title": "t", "body": "b"}], key="k")
    assert got[1] == [{"name": "UDC", "kind": "organization"}]


def test_a_nameless_entity_is_dropped(monkeypatch):
    _fake_api(_tool_reply([
        {"id": 1, "entities": [{"name": "  ", "kind": "place"}]}]), monkeypatch)
    assert e.extract([{"title": "t", "body": "b"}], key="k")[1] == []


def test_an_absurdly_long_name_is_dropped(monkeypatch):
    _fake_api(_tool_reply([
        {"id": 1, "entities": [{"name": "x" * 500, "kind": "place"}]}]), monkeypatch)
    assert e.extract([{"title": "t", "body": "b"}], key="k")[1] == []


def test_a_reply_with_no_tool_use_yields_nothing(monkeypatch):
    _fake_api({"content": [{"type": "text", "text": "I cannot do that"}],
               "usage": {}}, monkeypatch)
    got = e.extract([{"title": "t", "body": "b"}], key="k")
    assert [k for k in got if k != "_usage"] == []


def test_the_body_sent_is_truncated_to_the_documented_window():
    long_body = "mot " * 5000
    prompt = e._prompt_for([{"title": "T", "body": long_body}])
    assert len(prompt) < e.BODY_CHARS + 200


def test_the_prompt_numbers_articles_so_replies_can_be_matched():
    prompt = e._prompt_for([{"title": "A", "body": "x"}, {"title": "B", "body": "y"}])
    assert "ARTICLE 1" in prompt and "ARTICLE 2" in prompt


def test_the_system_prompt_treats_article_text_as_data():
    # These pages carry reader comments and CMS injections; the extractor must
    # not follow directions found inside them.
    assert "never an instruction" in e.SYSTEM


def test_missing_key_is_a_named_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    try:
        e.api_key()
    except e.NoKey:
        return
    raise AssertionError("expected NoKey")
