"""Profile runs started from the page: one per subject, and never on the loop."""
import asyncio
import json

import pytest

from mediatracker import db, profiling
from mediatracker import server as srv

MSG = {"community": "tx-romandie", "kind": "nick", "key": "202"}


class _C:
    def close(self):
        pass


def _server():
    # Only what the profile commands touch; the real constructor starts a fetcher
    # and opens stores.
    s = object.__new__(srv.Server)
    s.cfg, s.conn = None, object()
    s.profile_jobs, s._job_tasks = {}, set()
    return s


def _drive(analyse, monkeypatch, clicks=1):
    monkeypatch.setattr(profiling, "analyse_subject", analyse)
    monkeypatch.setattr(db, "connect", lambda cfg: _C())

    async def go():
        s = _server()
        started = [json.loads(s._profile_run("build_profile", MSG)) for _ in range(clicks)]
        await asyncio.gather(*list(s._job_tasks))
        return started, json.loads(s._profile_run("profile_job", MSG))
    return asyncio.run(go())


def test_a_second_click_reports_the_run_already_going(monkeypatch):
    calls = []

    def analyse(conn, **kw):
        calls.append(kw)
        return {"model": "claude-opus-5", "corrections": []}
    started, final = _drive(analyse, monkeypatch, clicks=3)
    assert [s["job"]["state"] for s in started] == ["running"] * 3
    assert len(calls) == 1, "one subject, one call, however often the button is pressed"
    assert calls[0] == {"community": "tx-romandie", "kind": "nick", "key": "202"}
    assert final["job"]["state"] == "done"
    assert final["job"]["result"]["model"] == "claude-opus-5"


def test_a_failed_run_reports_its_reason(monkeypatch):
    def analyse(conn, **kw):
        raise ValueError("'202' has fewer than 5 comments with text in tx-romandie")
    _, final = _drive(analyse, monkeypatch)
    assert final["job"]["state"] == "failed"
    assert "fewer than 5" in final["job"]["error"]


def test_a_finished_run_can_be_run_again(monkeypatch):
    calls = []
    monkeypatch.setattr(profiling, "analyse_subject",
                        lambda conn, **kw: calls.append(kw) or {"corrections": []})
    monkeypatch.setattr(db, "connect", lambda cfg: _C())

    async def go():
        s = _server()
        s._profile_run("build_profile", MSG)
        await asyncio.gather(*list(s._job_tasks))
        s._profile_run("build_profile", MSG)
        await asyncio.gather(*list(s._job_tasks))
    asyncio.run(go())
    assert len(calls) == 2


def test_an_unknown_subject_kind_is_refused():
    with pytest.raises(ValueError):
        _server()._profile_run("build_profile", {**MSG, "kind": "table"})
