from __future__ import annotations

import json

from mediatracker import protocol
from mediatracker.config import load_config
from mediatracker.store import JsonlStore


def test_config_defaults_and_derived_paths(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIATRACKER_PORT", raising=False)
    cfg = load_config(tmp_path / "does-not-exist.toml")  # falls back to defaults
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8830
    assert cfg.blob_path == cfg.data_path / "blobs"
    assert cfg.jsonl_path == cfg.data_path / "jsonl"


def test_config_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIATRACKER_PORT", "9999")
    monkeypatch.setenv("MEDIATRACKER_USER_AGENT", "test-agent/1.0")
    cfg = load_config(tmp_path / "none.toml")
    assert cfg.port == 9999
    assert cfg.user_agent == "test-agent/1.0"


def test_jsonl_store_appends_partitioned(tmp_path):
    store = JsonlStore(tmp_path)
    store.append("comment_snapshot", {"comment_id": "c1", "body_text": "hi"})
    store.append("comment_snapshot", {"comment_id": "c2", "body_text": "yo"})
    files = list((tmp_path / "comment_snapshot").glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text().strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["_kind"] == "comment_snapshot"
    assert rec["comment_id"] == "c1"


def test_protocol_roundtrip():
    raw = protocol.ok("ping", pong=True)
    msg = protocol.parse_request(raw)
    assert msg["cmd"] == "ping"
    assert msg["pong"] is True


def test_protocol_rejects_bad_request():
    for bad in ("not json", "[]", '{"no":"cmd"}'):
        try:
            protocol.parse_request(bad)
        except protocol.ProtocolError:
            continue
        raise AssertionError(f"expected ProtocolError for {bad!r}")
