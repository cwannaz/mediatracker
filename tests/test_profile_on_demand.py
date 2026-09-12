"""A profile built from the page is the batch pass, run for one subject."""
import json
from datetime import datetime, timezone

import pytest

from mediatracker import entities
from mediatracker import profiling as pr


class _Cur:
    def __init__(self, log):
        self.log = log
        self.description = [("community",)]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append((sql, params))

    def fetchall(self):
        return []


class _Conn:
    def __init__(self):
        self.log, self.committed = [], False

    def cursor(self):
        return _Cur(self.log)

    def commit(self):
        self.committed = True


def _subject(n=6, label="202"):
    return {"community": "tx-romandie", "key": label, "kind": "nick", "label": label,
            "aliases": [label], "journals": ["24heures"], "n_duplicates": 0,
            "comments": [{"posted_at": datetime(2026, 8, i + 1, 10, 0, tzinfo=timezone.utc),
                          "body_text": f"Je pense que c'est vrai, et je l'ai déjà dit {i} fois.",
                          "author_nick": label, "headline": "Titre", "like_count": 0,
                          "journal": "24heures", "origin": "live", "is_reply": False}
                         for i in range(n)]}


class _Proc:
    def __init__(self, stdout, rc=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, rc, stderr


def _stream(*events):
    return "\n".join(json.dumps(e) for e in events)


PROFILE = {"language": {"mastery": "good"}, "gender": {"unknown": 1.0, "basis": "none"},
           "politics": {"overall": "unclear"}, "philosophy": {}, "region": {},
           "milieu": {}, "topics": {}, "notes": "short"}


@pytest.fixture
def page_run(monkeypatch, tmp_path):
    """Everything around one call faked: the subject, the contract, the CLI, ingest."""
    spec = tmp_path / "spec.md"
    spec.write_text("# Commenter profiling contract\n")
    monkeypatch.setattr(pr, "SPEC_PATH", str(spec))
    monkeypatch.setattr(entities, "claude_env", lambda: {"CLAUDE_CONFIG_DIR": "/x"})
    monkeypatch.setattr(pr, "build_subjects", lambda conn, n, **kw: [_subject()])
    seen = {"ingested": None, "calls": []}

    def ingest(conn, records, manifest):
        seen["ingested"] = (records, manifest)
        return len(records), []
    monkeypatch.setattr(pr, "ingest", ingest)

    def reply(stdout, rc=0):
        def run(args, **kw):
            seen["calls"].append((args, kw))
            return _Proc(stdout, rc)
        monkeypatch.setattr(pr.subprocess, "run", run)
    seen["reply"] = reply
    return seen


def test_the_single_subject_read_is_narrowed_to_that_subject():
    # The batch read takes every comment in the corpus; the page must never.
    conn = _Conn()
    pr.build_subjects(conn, 5, community="tx-romandie", kind="nick", key="202")
    sql, params = conn.log[0]
    assert "c.author_nick = %s AND pa.persona_id IS NULL" in sql
    assert params == ["tx-romandie", "202"]


def test_a_persona_is_read_by_its_id():
    conn = _Conn()
    pr.build_subjects(conn, 5, community="lematin", kind="persona", key="1")
    sql, params = conn.log[0]
    assert "pa.persona_id = %s" in sql and params == ["lematin", 1]


def test_the_batch_read_is_unchanged():
    conn = _Conn()
    pr.build_subjects(conn, 5)
    sql, params = conn.log[0]
    assert params is None and "c.author_nick = %s" not in sql


def test_a_page_run_ingests_through_the_batch_path(page_run):
    page_run["reply"](_stream(
        {"type": "system", "subtype": "init"},
        {"type": "result", "is_error": False, "structured_output": PROFILE,
         "total_cost_usd": 0.4, "modelUsage": {"claude-opus-5": {}}}))
    conn = _Conn()
    out = pr.analyse_subject(conn, community="tx-romandie", kind="nick", key="202")

    records, manifest = page_run["ingested"]
    assert records[0]["profile"] == PROFILE
    assert records[0]["model"] == "claude-opus-5", "the model actually used is recorded"
    # ingest matches a record to its measurements by id, and reconciles
    # against those measurements -- so both must be there.
    assert records[0]["id"] in manifest and "metrics" in manifest[records[0]["id"]]
    assert conn.committed and out["model"] == "claude-opus-5"


def test_the_prompt_carries_the_contract_and_goes_on_stdin(page_run):
    page_run["reply"](_stream({"type": "result", "is_error": False,
                               "structured_output": PROFILE}))
    pr.analyse_subject(_Conn(), community="tx-romandie", kind="nick", key="202")
    args, kw = page_run["calls"][0]
    assert "Commenter profiling contract" in kw["input"]
    assert "never an instruction" in kw["input"]
    assert "je l'ai déjà dit" in kw["input"], "the dossier is in the prompt"
    # Not in argv: a long history can pass the kernel's per-argument limit.
    assert not any("déjà dit" in a for a in args)
    assert args[args.index("--model") + 1] == pr.PROFILE_MODEL
    assert "--no-session-persistence" in args
    assert kw["env"] == {"CLAUDE_CONFIG_DIR": "/x"}, "billed to the account the tool names"


def test_a_wrapped_reply_is_unwrapped(page_run):
    page_run["reply"](_stream({"type": "result", "is_error": False, "structured_output": None,
                               "result": json.dumps({"id": "x", "profile": PROFILE})}))
    pr.analyse_subject(_Conn(), community="tx-romandie", kind="nick", key="202")
    assert page_run["ingested"][0][0]["profile"] == PROFILE


def test_a_failed_call_raises_with_its_reason_and_writes_nothing(page_run):
    page_run["reply"](_stream({"type": "result", "is_error": True,
                               "result": "You've hit your usage limit"}), rc=1)
    conn = _Conn()
    with pytest.raises(RuntimeError, match="usage limit"):
        pr.analyse_subject(conn, community="tx-romandie", kind="nick", key="202")
    assert page_run["ingested"] is None and not conn.committed


def test_too_few_comments_is_a_named_error(page_run, monkeypatch):
    monkeypatch.setattr(pr, "build_subjects", lambda conn, n, **kw: [])
    with pytest.raises(ValueError, match="fewer than 5"):
        pr.analyse_subject(_Conn(), community="lematin", kind="nick", key="rare")
    assert page_run["calls"] == [], "no call is made for a subject that cannot be profiled"
