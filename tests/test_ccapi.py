"""What central-control is told, and who is allowed to ask."""
import json
import time

import pytest

from mediatracker import api, ccapi

JOURNALS = ["24heures", "lematin", "tdg"]


def _snapshot(**over):
    """A healthy read of Postgres, as the refresh thread would leave it."""
    fresh = ccapi._iso(__import__("datetime").datetime.now().astimezone())
    data = {
        "last_run": [{"slug": s, "status": "done", "requested_at": fresh,
                      "finished_at": fresh, "articles_seen": 300,
                      "article_snapshots": 12, "comments_seen": 900,
                      "comment_snapshots": 40, "images_new": 7, "errors": 0,
                      "note": None} for s in JOURNALS],
        "last_done": {s: {"at": fresh, "status": "done", "articles_seen": 300,
                          "comment_snapshots": 40} for s in JOURNALS},
        "recent_runs": [{"slug": "tdg", "status": "done", "at": fresh,
                         "articles_seen": 300, "comment_snapshots": 40,
                         "images_new": 7, "errors": 0}],
        "newest_article_fetch": fresh, "newest_comment_fetch": fresh,
        "bodies": {"with_text": 49922, "not_attempted": 72196, "total": 124203},
        "profiles": 437, "personas": 46, "read_last_hour": 0,
        "documents": {"article": 453475, "comment": 4256499, "image": 309587},
        "span": {"earliest": "2007-04-11", "latest": "2026-09-26"},
        "coverage": {"articles_read": 310331, "articles_total": 310455,
                     "pct": 99.96, "documents_read": 328535,
                     "entities": 299254, "mentions": 2457742},
    }
    data.update(over)
    snap = ccapi.Snapshot()
    snap.put(data, heavy=True)
    snap.put({}, heavy=False)
    return snap


def _live(**over):
    out = {"degraded": False, "queue": 0, "current": None, "last_stats": {},
           "journals": JOURNALS}
    out.update(over)
    return out


def _checks(payload):
    return {c["id"]: c for c in payload["checks"]}


# -- the report ------------------------------------------------------------ #

def test_every_declared_widget_gets_a_value():
    # A widget with no value renders as "no data"; the two must not drift.
    declared = {w["id"] for w in ccapi.manifest(journals=JOURNALS)["widgets"]}
    assert set(ccapi.status(_snapshot(), _live())["widgets"]) == declared


def test_the_manifest_places_every_widget_in_a_declared_section():
    m = ccapi.manifest(journals=JOURNALS)
    sections = {s["id"] for s in m["tab"]["sections"]}
    for w in m["widgets"] + m["actions"]:
        assert w["placement"], f"{w['id']} would be ignored"
        for place in w["placement"]:
            assert place == "dashboard" or place[4:] in sections, place
    on_dashboard = [w for w in m["widgets"] if "dashboard" in w["placement"]]
    assert len(on_dashboard) <= 4, "the dashboard card must stay glanceable"


def test_the_manifest_names_the_project_as_the_relay_does():
    # central-control refuses a registration whose project.id is not the
    # sender's relay name.
    assert ccapi.manifest()["project"]["id"] == "mediatracker"
    assert ccapi.status(_snapshot(), _live())["manifest_revision"] == \
        ccapi.manifest()["revision"]


def test_health_is_the_worst_of_the_checks_and_says_so():
    stale = ccapi._iso(__import__("datetime").datetime.now().astimezone()
                       - __import__("datetime").timedelta(hours=20))
    snap = _snapshot(last_done={"lematin": {"at": stale, "status": "done",
                                            "articles_seen": 1,
                                            "comment_snapshots": 0}})
    out = ccapi.status(snap, _live(journals=["lematin"]))
    assert _checks(out)["crawl-fresh"]["level"] == "error"
    assert out["health"]["level"] == "error"
    # The summary must not claim calm while a check is red.
    assert "papers scanned recently" in out["health"]["summary"].lower()


def test_a_quiet_spell_is_a_warning_not_an_error():
    warm = ccapi._iso(__import__("datetime").datetime.now().astimezone()
                      - __import__("datetime").timedelta(hours=10))
    snap = _snapshot(last_done={"tdg": {"at": warm, "status": "done",
                                        "articles_seen": 5,
                                        "comment_snapshots": 0}})
    out = ccapi.status(snap, _live(journals=["tdg"]))
    assert _checks(out)["crawl-fresh"]["level"] == "warn"


def test_a_healthy_report_is_green_and_summarised_from_the_figures():
    out = ccapi.status(_snapshot(), _live())
    assert out["health"]["level"] == "ok"
    assert all(c["level"] in ("ok", "info") for c in out["checks"])
    assert "453,475 articles" in out["health"]["summary"]


def test_a_degraded_daemon_is_an_error_not_an_unknown():
    out = ccapi.status(_snapshot(), _live(degraded=True))
    assert _checks(out)["db"]["level"] == "error"
    assert out["health"]["level"] == "error"


def test_postgres_not_answering_is_a_warning_with_the_last_good_figures():
    snap = _snapshot()
    snap.fail(RuntimeError("connection refused"))
    out = ccapi.status(snap, _live())
    assert _checks(out)["db"]["level"] == "warn"
    assert "connection refused" in _checks(out)["db"]["detail"]
    # Still reporting what it last knew, greyed out by central-control.
    assert out["widgets"]["comments"]["value"] == 4256499


def test_a_snapshot_that_never_arrived_is_unknown():
    out = ccapi.status(ccapi.Snapshot(), _live())
    assert _checks(out)["db"]["level"] == "unknown"
    assert _checks(out)["entity-reading"]["level"] == "unknown"


def test_a_scan_stuck_for_hours_is_reported_as_stuck():
    started = ccapi._iso(__import__("datetime").datetime.now().astimezone()
                         - __import__("datetime").timedelta(hours=3))
    out = ccapi.status(_snapshot(), _live(
        current={"slug": "lematin", "started_at": started, "current": 12,
                 "total": 400}, queue=6))
    assert _checks(out)["scan-queue"]["level"] == "warn"
    assert out["actions_state"]["trigger-scan"]["enabled"] is False


def test_finished_reading_is_not_a_fault():
    out = ccapi.status(_snapshot(), _live())
    check = _checks(out)["entity-reading"]
    assert check["level"] == "ok" and "99.96" in check["detail"]
    assert out["widgets"]["entity-reading"]["done"] == 310331


def test_the_hold_on_claude_work_is_reported(monkeypatch):
    monkeypatch.setenv("MT_LLM_PAUSED_UNTIL", "2099-01-01 00:00")
    out = ccapi.status(_snapshot(), _live())
    assert _checks(out)["llm-hold"]["level"] == "info"
    assert "held until" in out["widgets"]["hold"]["markdown"]
    assert out["health"]["level"] == "info", "a hold is not a fault"


def test_the_report_carries_no_secret():
    out = json.dumps(ccapi.status(_snapshot(), _live())) + json.dumps(ccapi.manifest())
    for forbidden in ("password", "PGPASSWORD", "secret", "token", "cookie"):
        assert forbidden not in out.lower()


# -- actions --------------------------------------------------------------- #

class _View:
    def __init__(self):
        self.queued = []

    def live(self):
        return _live()

    def trigger_scan(self, slug):
        self.queued.append(slug)
        return 100 + len(self.queued)


def test_an_action_queues_one_scan_per_paper():
    view = _View()
    status, out = ccapi.Actions(view).run(
        "trigger-scan", {"request_id": "a", "params": {"journal": "lematin"}})
    assert status == 200 and out["state"] == "succeeded"
    assert view.queued == ["lematin"]


def test_the_same_request_id_does_not_queue_twice():
    view, acts = _View(), None
    acts = ccapi.Actions(view)
    body = {"request_id": "same", "params": {"journal": "tdg"}}
    first = acts.run("trigger-scan", body)
    second = acts.run("trigger-scan", body)
    assert first == second
    assert view.queued == ["tdg"], "a retry must not start a second scan"


def test_an_unknown_paper_is_a_bad_param_not_a_crash():
    status, out = ccapi.Actions(_View()).run(
        "trigger-scan", {"params": {"journal": "leparisien"}})
    assert status == 400 and out["error"]["code"] == "bad_param"


def test_an_unknown_action_is_a_404():
    status, out = ccapi.Actions(_View()).run("drop-everything", {})
    assert status == 404 and out["error"]["code"] == "unknown_action"


# -- who may ask ----------------------------------------------------------- #

class _Req:
    """Just enough of BaseHTTPRequestHandler for the loopback test."""

    def __init__(self, peer="127.0.0.1", host="127.0.0.1:55032", **headers):
        self.client_address = (peer, 4242)
        self.headers = {"Host": host, **headers}
        self.server = type("S", (), {"server_address": ("127.0.0.1", 55032)})()
        self.headers = _Headers({"Host": host, **headers})


class _Headers(dict):
    def get(self, key, default=None):
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


def _ok(**kw):
    return api._Handler._loopback_only(_Req(**kw))


def test_a_local_caller_is_let_in():
    assert _ok() and _ok(peer="::1", host="localhost:55032")


def test_a_caller_from_the_lan_is_refused():
    assert not _ok(peer="192.168.1.40")


def test_a_proxied_caller_is_refused_although_it_arrives_from_localhost():
    # Caddy forwards from 127.0.0.1; the forwarding header is the only tell.
    assert not _ok(**{"X-Forwarded-For": "192.168.1.40"})
    assert not _ok(**{"Forwarded": "for=192.168.1.40"})
    assert not _ok(**{"X-Real-IP": "192.168.1.40"})


def test_a_request_for_another_hostname_is_refused():
    # How a proxied request arrives when the proxy strips its own headers.
    assert not _ok(host="eurisko.lan:55032")
    assert not _ok(host="mediatracker.lan")


@pytest.mark.parametrize("path", ["/cc/v1/status", "/cc/v1/manifest"])
def test_the_cc_routes_all_run_the_check(path, monkeypatch):
    """Not one GET may skip it: status alone would reveal the whole report."""
    src = open(api.__file__).read()
    assert src.count("_loopback_only()") >= 2, "GET and POST both check"
    assert "Access-Control-Allow-Origin" not in src.split("_send_cc")[1].split("def ")[1]


def test_the_snapshot_survives_a_failed_read_and_keeps_its_figures():
    snap = _snapshot()
    before, at, _ = snap.read()
    snap.fail(RuntimeError("boom"))
    after, at2, err = snap.read()
    assert after == before and at2 == at and err == "boom"
    snap.put({"profiles": 440}, heavy=False)
    again, _, err = snap.read()
    assert again["profiles"] == 440 and err is None, "a good read clears the error"


def test_the_snapshot_is_called_stale_before_it_is_believed():
    snap = _snapshot()
    snap.cheap_at = time.time() - ccapi.SNAPSHOT_STALE_S - 60
    assert _checks(ccapi.status(snap, _live()))["db"]["level"] == "warn"


# -- a deliberate hold is not a failure ------------------------------------ #

def test_a_fetch_hold_keeps_the_crawl_from_going_red(monkeypatch):
    """A week of deliberate quiet must not read as a week of breakage."""
    stale = ccapi._iso(__import__("datetime").datetime.now().astimezone()
                       - __import__("datetime").timedelta(days=5))
    snap = _snapshot(last_done={s: {"at": stale, "status": "done",
                                    "articles_seen": 2, "comment_snapshots": 0}
                                for s in JOURNALS})
    monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "2099-01-01 08:00")
    out = ccapi.status(snap, _live())
    assert _checks(out)["crawl-fresh"]["level"] == "info"
    assert out["health"]["level"] == "info"
    assert "on hold until" in out["health"]["summary"]
    assert all(i["level"] == "info" for i in out["widgets"]["crawl"]["items"])
    # Without the hold the same figures are an error, or the check says nothing.
    monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "")
    assert _checks(ccapi.status(snap, _live()))["crawl-fresh"]["level"] == "error"


def test_a_fetch_hold_has_a_check_and_disables_the_button(monkeypatch):
    monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "2099-01-01 08:00")
    out = ccapi.status(_snapshot(), _live())
    assert _checks(out)["fetch-hold"]["level"] == "info"
    state = out["actions_state"]["trigger-scan"]
    assert state["enabled"] is False and "on hold until" in state["reason"]
    assert "Nothing goes out" in out["widgets"]["hold"]["markdown"]


def test_both_holds_are_reported_together(monkeypatch):
    monkeypatch.setenv("MT_FETCH_PAUSED_UNTIL", "2099-01-01 08:00")
    monkeypatch.setenv("MT_LLM_PAUSED_UNTIL", "2099-02-01 08:00")
    text = ccapi.status(_snapshot(), _live())["widgets"]["hold"]["markdown"]
    assert "Fetching is" in text and "calls Claude is" in text
