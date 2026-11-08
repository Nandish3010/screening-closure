"""ADR-058: the hosted demo's judge-experience controls: the gate, the fresh-case picker, the reset request and the simulated missed
follow-up. The functional tests mount the real router on a small app behind the real policy table; the gate tests start the real
app in a subprocess under each configuration."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api_helpers import login_headers, make_client
from app import clock as clock_module
from app import demo_controls, policy
from app.config import settings
from app.events import InMemoryEventStore
from app.routers import cases
from app.routers import demo as demo_router

API_DIR = Path(__file__).resolve().parents[1]

mini = FastAPI(dependencies=[Depends(policy.enforce_route_policy)])
mini.include_router(demo_router.router)
demo = TestClient(mini)
main = make_client()  # the real app: only used to create cases and to log in
SUP = login_headers(main, "supervisor", "demo sup")
COORD = login_headers(main, "coordinator", "demo coord")
PATIENT = login_headers(main, "patient", "demo patient", patient_ref="Patient/1")
LAB = login_headers(main, "lab", "demo lab")


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    saved = cases.store
    cases.store = InMemoryEventStore()
    clock_module.clock.reset()
    monkeypatch.setattr(settings, "demo_control_dir", str(tmp_path / "ctl"))
    monkeypatch.setattr(settings, "demo_reset_min_interval_seconds", 600)
    demo_controls.forget()
    yield
    clock_module.clock.reset()
    cases.store = saved
    demo_controls.forget()


def new_case(n=1) -> list[str]:
    out = []
    for i in range(n):
        r = main.post("/cases", json={"patient_ref": f"Patient/{i + 1}"}, headers=COORD)
        assert r.status_code == 201, r.text
        out.append(r.json()["case_id"])
    return out


def write_status(**kw):
    d = Path(settings.demo_control_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "status.json").write_text(json.dumps(kw))


# ---- the gate -------------------------------------------------------------------------------------------------------

def _probe(**extra: str) -> dict:
    code = (
        "import json\n"
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n"
        "c = TestClient(app)\n"
        "print(json.dumps({'paths': sorted(p for p in app.openapi()['paths'] if p.startswith('/demo')),\n"
        "  'clock': sorted(p for p in app.openapi()['paths'] if p.startswith('/clock')),\n"
        "  'guide': c.get('/demo/guide?session=abcdefgh').status_code,\n"
        "  'reset': c.post('/demo/reset').status_code,\n"
        "  'missed': c.post('/demo/missed-followup', json={}).status_code,\n"
        "  'health': c.get('/health').json()}))\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in ("APP_ENV", "DATABASE_URL", "DEV_LOGIN_ENABLED", "CLOCK_MODE", "DEMO_SIMULATED_CLOCK", "DEMO_DEPLOYMENT")}
    env.update(PYTHONPATH=str(API_DIR), DEV_AUTH_SECRET="k" * 40, **extra)
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=API_DIR, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


ALL_ROLES = ["lab", "primary_care_owner", "specialist_owner", "coordinator", "patient", "supervisor"]
ROUTES = ["/demo/guide", "/demo/missed-followup", "/demo/reset", "/demo/sign-in"]


def test_the_controls_exist_only_under_both_flags_with_the_sim_clock_outside_dev():
    for extra in (dict(APP_ENV="dev"),  # a developer's stack: none
                  dict(APP_ENV="dev", DEMO_SIMULATED_CLOCK="1"),  # one flag
                  dict(APP_ENV="dev", DEMO_DEPLOYMENT="1"),
                  dict(APP_ENV="production", CLOCK_MODE="wall", DEMO_DEPLOYMENT="1"),
                  dict(APP_ENV="production", CLOCK_MODE="wall")):
        out = _probe(**extra)
        assert out["paths"] == [] and "demo" not in out["health"], extra
        assert out["guide"] in (401, 403, 404) and out["reset"] in (401, 403, 404) and out["missed"] in (401, 403, 404)


def test_the_hosted_demo_gate_registers_them_and_health_reports_the_status():
    out = _probe(APP_ENV="production", CLOCK_MODE="sim", DEMO_SIMULATED_CLOCK="1", DEMO_DEPLOYMENT="1")
    assert out["paths"] == ROUTES
    assert out["clock"] == ["/clock/advance"]  # still no clock reset
    assert out["guide"] in (401, 403) and out["reset"] in (401, 403) and out["missed"] in (401, 403)  # nothing anonymous
    demo_block = out["health"]["demo"]
    assert demo_block["simulated_clock"] is True and demo_block["deployment"] is True
    assert {"last_reset_at", "last_reset_by", "next_auto_reset_at", "reset_pending"} <= set(demo_block)


def test_a_dev_stack_with_both_flags_still_has_none_of_it():
    out = _probe(APP_ENV="dev", CLOCK_MODE="sim", DEMO_SIMULATED_CLOCK="1", DEMO_DEPLOYMENT="1")
    assert out["paths"] == [] and "demo" not in out["health"]  # the gate is the existing one: APP_ENV is not dev


# ---- roles ----------------------------------------------------------------------------------------------------------

def test_reset_and_missed_followup_are_supervisor_only_and_the_guide_is_for_every_role():
    (cid,) = new_case()
    for headers in (COORD, LAB, PATIENT):
        assert demo.post("/demo/reset", headers=headers).status_code == 403
        assert demo.post("/demo/missed-followup", json={"case_id": cid}, headers=headers).status_code == 403
    for headers in (SUP, COORD, PATIENT):
        assert demo.get("/demo/guide?session=abcdefgh", headers=headers).status_code == 200
    assert demo.get("/demo/guide", headers=SUP).status_code == 422
    assert demo.get("/demo/guide?session=no", headers=SUP).status_code == 422


# ---- reset request --------------------------------------------------------------------------------------------------

def test_reset_is_requested_once_rate_limited_and_names_who_asked():
    r = demo.post("/demo/reset", headers=SUP)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["requested"] is True and "demo sup" in body["requested_by"] and "supervisor" in body["requested_by"]
    req = json.loads((Path(settings.demo_control_dir) / "reset-request.json").read_text())
    assert req["requested_by"] == body["requested_by"]
    again = demo.post("/demo/reset", headers=SUP)
    assert again.status_code == 409 and "already on its way" in again.json()["detail"]
    assert demo_controls.status()["reset_pending"] is True


def test_reset_is_refused_for_a_while_after_a_reset_and_the_message_is_plain():
    write_status(last_reset_at=datetime.now(UTC).isoformat(), last_reset_by="the schedule", last_reset_reason="scheduled")
    r = demo.post("/demo/reset", headers=SUP)
    assert r.status_code == 429
    assert "wait" in r.json()["detail"] and "minute" in r.json()["detail"]
    assert not (Path(settings.demo_control_dir) / "reset-request.json").exists()
    write_status(last_reset_at="2026-01-01T00:00:00+00:00")
    assert demo.post("/demo/reset", headers=SUP).status_code == 202  # long ago: allowed


@pytest.fixture
def judging(monkeypatch):
    monkeypatch.setattr(settings, "sc_judging_mode", True)
    monkeypatch.setattr(settings, "sc_judging_reset_interval_seconds", 1800)


def test_judging_mode_off_leaves_the_ten_minute_reset_limit_alone():
    assert settings.sc_judging_mode is False  # the default: nothing changes unless the host turns it on
    write_status(last_request_at=(datetime.now(UTC) - timedelta(minutes=11)).isoformat())
    assert demo.post("/demo/reset", headers=SUP).status_code == 202


def test_judging_mode_allows_one_manual_reset_per_thirty_minutes_for_everybody(judging):
    """Judging protection: any visitor can sign in as supervisor, so a manual reset is limited globally, not per person."""
    other_sup = login_headers(main, "supervisor", "another visitor")
    first = demo.post("/demo/reset", headers=SUP)
    assert first.status_code == 202
    (Path(settings.demo_control_dir) / "reset-request.json").unlink()  # the host took the request (reset done)
    write_status(last_request_at=(datetime.now(UTC) - timedelta(minutes=12)).isoformat())  # past the 10 min limit, inside the 30
    r = demo.post("/demo/reset", headers=other_sup)  # a different supervisor session: the limit is global
    assert r.status_code == 429
    assert "30 minutes" in r.json()["detail"] and "03:00" in r.json()["detail"] and "18 minute" in r.json()["detail"]
    assert not (Path(settings.demo_control_dir) / "reset-request.json").exists()
    write_status(last_request_at=(datetime.now(UTC) - timedelta(minutes=31)).isoformat())
    assert demo.post("/demo/reset", headers=other_sup).status_code == 202


def test_the_scheduled_reset_is_not_a_request_and_judging_mode_does_not_touch_it(judging):
    """The daily reset is the host timer's (demo-reset-watch.sh --scheduled); it never goes through request_reset, and a scheduled
    reset a minute ago (last_reset_at only, no manual request) does not use up the manual allowance beyond the 10 min rule."""
    write_status(last_reset_at=(datetime.now(UTC) - timedelta(minutes=20)).isoformat(), last_reset_by="the schedule",
                 last_reset_reason="scheduled")
    assert demo.post("/demo/reset", headers=SUP).status_code == 202
    s = (API_DIR.parent / "deploy/gcp/vm/demo-reset-watch.sh").read_text()
    assert "--scheduled" in s and "judging" not in s.lower()  # no judging-mode branch in the host script


def test_supervisor_advance_still_works_in_judging_mode(judging):
    assert demo_controls.guard_advance(3) is None


def test_status_shows_when_who_and_the_next_scheduled_reset(monkeypatch):
    monkeypatch.setattr(demo_controls, "_wall", lambda: datetime(2026, 11, 8, 7, 0, tzinfo=UTC))  # "now" is fixed: the 6 h slot after 06:00 is 12:00
    assert demo_controls.status()["last_reset_at"] is None and demo_controls.status()["reset_id"] == "initial"
    from datetime import timedelta

    last = datetime.now(UTC) - timedelta(hours=1)  # relative to now: a fixed date goes stale the day it passes
    write_status(last_reset_at=last.isoformat(), last_reset_by="Noura (supervisor)", last_reset_reason="manual")
    st = demo_controls.status()
    assert st["last_reset_by"] == "Noura (supervisor)"
    assert datetime.fromisoformat(st["next_auto_reset_at"]) == last + timedelta(hours=6)


def test_status_shows_a_failed_reset_until_the_next_good_one():
    """The host writes last_reset_error when a reset failed (demo-reset-watch.sh); /health's demo block carries it."""
    assert demo_controls.status()["last_reset_error"] is None
    write_status(last_reset_at="2026-11-08T05:00:00+00:00", last_reset_error="manual reset by x failed (exit 3)",
                 last_reset_error_at="2026-11-08T06:00:00+00:00")
    st = demo_controls.status()
    assert st["last_reset_error"].startswith("manual reset by x failed") and st["last_reset_error_at"] == "2026-11-08T06:00:00+00:00"
    assert st["last_reset_at"] == "2026-11-08T05:00:00+00:00"  # the failed reset did not become the last reset
    write_status(last_reset_at="2026-11-08T07:00:00+00:00", last_reset_error="", last_reset_error_at="")  # the host clears it
    assert demo_controls.status()["last_reset_error"] is None


def test_next_scheduled_reset_follows_the_timer_not_only_the_last_reset():
    """The host records the timer's last pass (even a skipped one) and the seed's snapshot as the first reset point."""
    from datetime import timedelta

    now = datetime.now(UTC)
    seed = (now - timedelta(hours=1)).isoformat()
    write_status(last_reset_at=seed, last_reset_by="the seed", last_reset_reason="seed")
    st = demo_controls.status()
    assert st["last_reset_reason"] == "seed" and datetime.fromisoformat(st["next_auto_reset_at"]) == datetime.fromisoformat(seed) + timedelta(hours=6)
    # the timer fired 2 h ago and skipped (a manual reset was fresh): the next slot is 6 h after THAT pass
    fired = (now - timedelta(hours=2)).isoformat()
    write_status(last_reset_at=seed, last_scheduled_at=fired)
    assert datetime.fromisoformat(demo_controls.status()["next_auto_reset_at"]) == datetime.fromisoformat(fired) + timedelta(hours=6)
    # a late timer never yields a time in the past: it rolls to the next slot
    old = (now - timedelta(hours=20)).isoformat()
    write_status(last_scheduled_at=old)
    nxt = datetime.fromisoformat(demo_controls.status()["next_auto_reset_at"])
    assert nxt > now and nxt - now <= timedelta(hours=6)
    assert (nxt - datetime.fromisoformat(old)) % timedelta(hours=6) == timedelta(0)


def test_next_scheduled_reset_at_a_daily_clock_time(monkeypatch):
    """Judging window (S3): the hosted demo resets daily at 23:00 UTC (03:00 Asia/Dubai); /health shows the next such time."""
    from datetime import timedelta

    monkeypatch.setattr(settings, "demo_auto_reset_hours", 24)
    monkeypatch.setattr(settings, "demo_auto_reset_at_utc", "23:00")
    write_status(last_reset_at="2026-11-20T08:00:00+00:00")
    for now, want in (
        (datetime(2026, 11, 20, 10, 0, tzinfo=UTC), "2026-11-20T23:00:00+00:00"),  # 14:00 Dubai: tonight
        (datetime(2026, 11, 20, 23, 0, tzinfo=UTC), "2026-11-21T23:00:00+00:00"),  # exactly on the slot: tomorrow's
        (datetime(2026, 11, 20, 23, 30, tzinfo=UTC), "2026-11-21T23:00:00+00:00"),
    ):
        assert demo_controls._next_scheduled({"last_reset_at": "2026-11-20T08:00:00+00:00"}, 24, now, "23:00") == want
    assert datetime.fromisoformat(demo_controls.status()["next_auto_reset_at"]).strftime("%H:%M") == "23:00"
    assert datetime.fromisoformat(demo_controls.status()["next_auto_reset_at"]) - datetime.now(UTC) <= timedelta(days=1)
    # still off when scheduled resets are off
    assert demo_controls._next_scheduled({"last_reset_at": "2026-11-20T08:00:00+00:00"}, 0, datetime.now(UTC), "23:00") is None


def test_no_scheduled_reset_means_no_next_time(monkeypatch):
    monkeypatch.setattr(settings, "demo_auto_reset_hours", 0)
    write_status(last_reset_at="2026-11-08T06:00:00+00:00")
    assert demo_controls.status()["next_auto_reset_at"] is None


def test_status_shows_the_last_gated_api_restart_and_treats_the_file_as_data():
    st = demo_controls.status()
    assert st["last_api_restart_at"] is None and st["last_api_restart_reason"] is None
    d = Path(settings.demo_control_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "api-restart.json").write_text(json.dumps({"at": "2026-11-08T03:00:00+00:00", "mode": "gate", "reason": "memory 74% >= gate 70%" + "x" * 500}))
    st = demo_controls.status()
    assert st["last_api_restart_at"] == "2026-11-08T03:00:00+00:00"
    assert st["last_api_restart_reason"].startswith("memory 74%") and len(st["last_api_restart_reason"]) <= 200
    (d / "api-restart.json").write_text(json.dumps({"at": ["not", "a", "time"], "reason": {"x": 1}}))  # wrong shapes: ignored
    assert demo_controls.status()["last_api_restart_at"] is None
    (d / "api-restart.json").write_text("not json")
    assert demo_controls.status()["last_api_restart_at"] is None


def test_the_restart_fields_are_only_in_the_demo_block_of_health():
    out = _probe(APP_ENV="production", CLOCK_MODE="sim", DEMO_SIMULATED_CLOCK="1", DEMO_DEPLOYMENT="1")
    assert {"last_api_restart_at", "last_api_restart_reason"} <= set(out["health"]["demo"])
    plain = _probe(APP_ENV="production", CLOCK_MODE="wall")
    assert "demo" not in plain["health"] and "last_api_restart_at" not in json.dumps(plain["health"])


def test_interactive_docs_and_schema_are_served_in_dev_and_gone_in_production():
    code = ("import json\nfrom fastapi.testclient import TestClient\nfrom app.main import app\nc = TestClient(app)\n"
            "print(json.dumps([c.get(p).status_code for p in ('/docs', '/redoc', '/openapi.json')] + [len(app.openapi()['paths']) > 10]))\n")

    def run(**extra):
        env = {k: v for k, v in os.environ.items() if k not in ("APP_ENV", "DATABASE_URL", "DEV_LOGIN_ENABLED", "CLOCK_MODE", "DEMO_SIMULATED_CLOCK", "DEMO_DEPLOYMENT")}
        env.update(PYTHONPATH=str(API_DIR), DEV_AUTH_SECRET="k" * 40, **extra)
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=API_DIR, timeout=120)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout.strip().splitlines()[-1])

    assert run(APP_ENV="dev") == [200, 200, 200, True]
    assert run(APP_ENV="production", CLOCK_MODE="sim", DEMO_SIMULATED_CLOCK="1", DEMO_DEPLOYMENT="1") == [404, 404, 404, True]  # in-process schema still works


# ---- fresh cases ----------------------------------------------------------------------------------------------------

def test_each_session_gets_its_own_untouched_case_and_a_touched_one_is_replaced():
    ids = new_case(4)
    a = demo.get("/demo/guide?session=session-aaaa", headers=COORD).json()["steps"]
    b = demo.get("/demo/guide?session=session-bbbb", headers=COORD).json()["steps"]
    a_cases = {s["case_id"] for k, s in a.items() if s and k in ("record", "remind", "safety")}
    b_cases = {s["case_id"] for k, s in b.items() if s and k in ("record", "remind", "safety")}
    assert a["record"] and b["record"]
    assert a["record"]["case_id"] != b["record"]["case_id"], "two judges must not be given the same case"
    assert a_cases.isdisjoint(b_cases)
    # the same session asking again is given the same case (a page reload keeps its place)
    assert demo.get("/demo/guide?session=session-aaaa", headers=COORD).json()["steps"]["record"] == a["record"]
    # someone acts on session A's case: it is no longer fresh, so A is moved to another
    picked = a["record"]["case_id"]
    assert main.post(f"/cases/{picked}/events", json={"type": "acknowledge", "payload": {}}, headers=COORD).status_code in (200, 201)
    again = demo.get("/demo/guide?session=session-aaaa", headers=COORD).json()["steps"]["record"]
    assert again is None or again["case_id"] != picked
    assert set(a_cases) | {picked} <= set(ids)


def test_a_step_with_no_fresh_case_says_so_instead_of_inventing_one():
    steps = demo.get("/demo/guide?session=session-aaaa", headers=COORD).json()["steps"]  # no cases at all
    assert set(steps) == {"record", "prioritise", "refer", "remind", "outcome", "safety", "privacy"}
    assert all(v is None for v in steps.values())


def test_a_case_touched_before_the_first_look_is_taken_as_the_seed_and_a_later_touch_is_noticed(tmp_path):
    (cid,) = new_case()
    first = demo.get("/demo/guide?session=session-aaaa", headers=COORD).json()["steps"]["record"]
    assert first and first["case_id"] == cid
    assert (Path(settings.demo_control_dir) / "baseline.json").exists()  # survives a restart of the API
    demo_controls.forget()  # "restart"
    assert demo.get("/demo/guide?session=session-aaaa", headers=COORD).json()["steps"]["record"]["case_id"] == cid
    main.post(f"/cases/{cid}/events", json={"type": "acknowledge", "payload": {}}, headers=COORD)
    demo_controls.forget()
    # touched since the seed: not fresh any more, but the step still fits it, so it is offered and says so (never an empty step)
    again = demo.get("/demo/guide?session=session-cccc", headers=COORD).json()["steps"]["record"]
    assert again["case_id"] == cid and again["fresh"] is False


def test_when_every_case_has_been_used_each_step_still_gets_a_fitting_case_and_judges_are_spread_over_them():
    ids = new_case(6)
    demo.get("/demo/guide?session=session-seed1", headers=COORD)  # the first look takes the baseline
    for cid in ids:  # other judges have used every case
        assert main.post(f"/cases/{cid}/events", json={"type": "acknowledge", "payload": {}}, headers=COORD).status_code in (200, 201)
    picks = {}
    for n in range(6):
        steps = demo.get(f"/demo/guide?session=judge-session-{n}", headers=COORD).json()["steps"]
        assert steps["record"] and steps["record"]["fresh"] is False, "a touched case, offered honestly"
        assert steps["privacy"] and steps["privacy"]["fresh"] is False
        picks[n] = steps["record"]["case_id"]
    assert len(set(picks.values())) > 1, "the judges are not all sent to the same case"
    # a step whose state test no case meets is still empty: nothing is invented
    assert demo.get("/demo/guide?session=judge-session-0", headers=COORD).json()["steps"]["outcome"] is None


# ---- simulate a missed follow-up ------------------------------------------------------------------------------------

def test_a_missed_followup_is_caught_by_the_real_engine_and_a_human_accepts_the_escalation():
    (cid,) = new_case()
    start = clock_module.clock.now()
    r = demo.post("/demo/missed-followup", json={"case_id": cid}, headers=SUP)
    assert r.status_code == 200, r.text
    out = r.json()
    assert clock_module.clock.now() > start and out["days_skipped"] > 0
    assert out["overdue"] is True and out["escalation_level"] >= 2
    types = [e["type"] for e in out["new_events"]]
    assert "mark_overdue" in types and "escalate" in types
    assert all(e["actor_kind"] == "system" for e in out["new_events"]), "the engine wrote them, not this route"
    # every one of those events is in the case's own log, under a system actor
    log = main.get(f"/cases/{cid}/timeline", headers=SUP).json()
    assert {"mark_overdue", "escalate"} <= {e.get("type") for e in log["items"] if e["kind"] == "event"}
    # a human accepts the supervisor rung
    acc = main.post(f"/cases/{cid}/events", json={"type": "escalation_accepted", "payload": {"rung": "supervisor"}}, headers=SUP)
    assert acc.status_code in (200, 201), acc.text


def test_a_missed_followup_refuses_with_plain_words():
    assert demo.post("/demo/missed-followup", json={"case_id": "00000000-0000-4000-8000-000000000000"}, headers=SUP).status_code == 404
    assert demo.post("/demo/missed-followup", json={"case_id": "x"}, headers=SUP).status_code == 422
    (cid,) = new_case()
    assert demo.post("/demo/missed-followup", json={"case_id": cid, "extra": 1}, headers=SUP).status_code == 422


def test_an_unwritable_control_folder_is_a_plain_503_not_a_stack_trace(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(settings, "demo_control_dir", str(blocker / "sub"))  # a path under a file cannot be created
    r = demo.post("/demo/reset", headers=SUP)
    assert r.status_code == 503 and "operator" in r.json()["detail"] and "Traceback" not in r.text


# ---- security review (coordinator): every /demo/* path, every role ------------------------------------------------------------

def _demo_rows():
    return sorted((m, p) for (m, p) in policy.ROUTE_POLICY if p.startswith("/demo/"))


def test_every_demo_path_is_404_without_the_gate_for_every_method_and_caller():
    """The policy table lists the /demo/* paths; a process without the gate must not serve any of them, to anyone."""
    rows = _demo_rows()
    assert [p for _, p in rows] == ["/demo/guide", "/demo/missed-followup", "/demo/reset", "/demo/sign-in"]
    for extra in (dict(APP_ENV="dev"), dict(APP_ENV="dev", DEMO_SIMULATED_CLOCK="1", DEMO_DEPLOYMENT="1", CLOCK_MODE="sim"),
                  dict(APP_ENV="production", CLOCK_MODE="sim", DEMO_SIMULATED_CLOCK="1"),
                  dict(APP_ENV="production", CLOCK_MODE="wall", DEMO_DEPLOYMENT="1"), dict(APP_ENV="production", CLOCK_MODE="wall")):
        code = (
            "import json\nfrom fastapi.testclient import TestClient\nfrom app.main import app\nfrom app import auth\n"
            "c = TestClient(app)\n"
            "tok = {r: c.post('/auth/dev-login', json={'role': r, 'name': 'x'}).json().get('token') for r in "
            "['coordinator','supervisor','patient']} if app.openapi() else {}\n"
            "out = {}\n"
            "for m, p in [('GET','/demo/guide?session=abcdefgh'),('POST','/demo/reset'),('POST','/demo/missed-followup'),('POST','/demo/sign-in'),('GET','/demo/credentials')]:\n"
            "    out[m+p] = [c.request(m, p, json={} if m == 'POST' else None).status_code] + [c.request(m, p, json={} if m == 'POST' else None, headers={'Authorization': 'Bearer ' + t}).status_code for t in tok.values() if t]\n"
            "print(json.dumps({'out': out, 'paths': [p for p in app.openapi()['paths'] if p.startswith('/demo')]}))\n")
        env = {k: v for k, v in os.environ.items() if k not in ("APP_ENV", "DATABASE_URL", "DEV_LOGIN_ENABLED", "CLOCK_MODE", "DEMO_SIMULATED_CLOCK", "DEMO_DEPLOYMENT")}
        env.update(PYTHONPATH=str(API_DIR), DEV_AUTH_SECRET="k" * 40, **extra)
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=API_DIR, timeout=120)
        got = json.loads(done.stdout.strip().splitlines()[-1]) if done.returncode == 0 else {"stderr": done.stderr[-400:]}
        assert got.get("paths") == [], (extra, got)
        for route, codes in got["out"].items():
            # an authenticated caller gets 404 (no such route); an anonymous one may get 401/403 from the policy gate first, never data
            assert all(c in (404, 401, 403) for c in codes), (extra, route, codes)
            assert 200 not in codes and 202 not in codes


def test_the_demo_routes_are_probed_for_every_role_in_the_rbac_matrix_sense():
    """The matrix tool leaves /demo/* out (they are not served in its process); this is their matrix: each row of the policy table,
    every role and anonymous, against the real router behind the real policy dependency."""
    (cid,) = new_case()
    bodies = {("GET", "/demo/guide"): (None, "?session=abcdefgh"), ("POST", "/demo/reset"): (None, ""),
              ("POST", "/demo/sign-in"): ({"role": "nobody"}, ""),  # public by design; ADR-060 (tests/test_demo_signin.py holds its behaviour)
              ("POST", "/demo/missed-followup"): ({"case_id": cid}, "")}
    for (method, path), allowed in sorted((k, v) for k, v in policy.ROUTE_POLICY.items() if k[1].startswith("/demo/")):
        body, qs = bodies[(method, path)]
        anon = demo.request(method, path + qs, json=body)
        if allowed == policy.PUBLIC:  # the one deliberately public demo route: it answers anyone, and an unknown role gets 422, never a token
            assert anon.status_code == 422 and "access_token" not in anon.text, (path, anon.status_code)
            continue
        assert anon.status_code == 401, (path, anon.status_code)  # nothing anonymous
        for role in ALL_ROLES:
            demo_controls.forget()
            headers = login_headers(main, role, f"matrix {role}", patient_ref="Patient/1" if role == "patient" else None)
            r = demo.request(method, path + qs, json=body, headers=headers)
            if role in allowed:
                assert r.status_code not in (401, 403), (path, role, r.status_code, r.text)
            else:
                assert r.status_code == 403, (path, role, r.status_code)
            clock_module.clock.reset()
    assert policy.ROUTE_POLICY[("POST", "/demo/reset")] == frozenset({"supervisor"})
    assert policy.ROUTE_POLICY[("POST", "/demo/missed-followup")] == frozenset({"supervisor"})


def test_missed_followup_is_rate_limited_and_names_who_asked(caplog):
    import logging

    (cid,) = new_case()
    with caplog.at_level(logging.INFO, logger="app.demo"):
        first = demo.post("/demo/missed-followup", json={"case_id": cid}, headers=SUP)
    assert first.status_code == 200 and "demo sup" in first.json()["requested_by"]
    assert any("demo sup" in r.getMessage() for r in caplog.records)  # who, in the log with the clock move
    second = demo.post("/demo/missed-followup", json={"case_id": cid}, headers=SUP)
    assert second.status_code == 429 and "few seconds" in second.json()["detail"]


def test_a_missed_followup_spends_the_same_clock_budget_as_advance():
    """S10: Advance and "Simulate a missed follow-up" draw on ONE rolling budget (30 days / 6 h), so a visitor cannot add 45 days
    every 10 s through the second."""
    a, b = new_case(2)
    demo_controls.charge_clock(29)  # as if Advance had moved the clock 29 days
    before = clock_module.clock.now()
    r = demo.post("/demo/missed-followup", json={"case_id": a}, headers=SUP)  # needs more than the 1 day that is left
    assert r.status_code == 409 and "29 of the 30 days" in r.json()["detail"] and "more" in r.json()["detail"]
    assert clock_module.clock.now() == before  # refused before anything moved


def test_a_missed_followup_charges_the_days_it_skipped():
    (cid,) = new_case()
    r = demo.post("/demo/missed-followup", json={"case_id": cid}, headers=SUP)
    assert r.status_code == 200, r.text
    spent = json.loads((Path(settings.demo_control_dir) / "clock-budget.json").read_text())["entries"]
    assert len(spent) == 1 and spent[0][1] == pytest.approx((clock_module.clock.now() - datetime.fromisoformat(r.json()["clock_from"])).total_seconds() / 86400, abs=0.5)
    with pytest.raises(demo_controls.DemoError) as exc:  # and Advance now sees it
        demo_controls.charge_clock(31 - spent[0][1] + 1)
    assert exc.value.status == 409


def test_a_skip_larger_than_the_whole_budget_says_so_instead_of_asking_to_wait():
    with pytest.raises(demo_controls.DemoError) as exc:
        demo_controls.charge_clock(demo_controls.ADVANCE_MAX_DAYS_PER_WINDOW + 5)
    assert exc.value.status == 409 and "due sooner" in exc.value.message and "Try again" not in exc.value.message


def test_no_public_credentials_listing_exists_and_no_config_turns_one_on():
    """ADR-060 reverses the old "no credential shortcut" rule in ONE way only: a gated sign-in that returns tokens. A route that LISTS or
    returns passwords, and the opt-in switches an earlier draft had for it, still do not exist (the password never leaves the API)."""
    repo = API_DIR.parent
    assert "/demo/credentials" not in policy.ROUTE_POLICY and not hasattr(demo_controls, "judge_logins")
    assert "/demo/credentials" not in [r.path for r in demo_router.router.routes]
    offenders = []
    for root in ("deploy", "docker-compose.yml", "docker-compose.offline.yml", "docker", "config", "tools/k8s", "k8s", "helm"):
        base = repo / root
        files = [base] if base.is_file() else [f for f in base.rglob("*") if f.is_file()] if base.exists() else []
        for f in files:
            try:
                text = f.read_text(errors="ignore")
            except OSError:
                continue
            if "DEMO_SHOW_CREDENTIALS" in text or "DEMO_JUDGE_LOGINS" in text:
                offenders.append(str(f.relative_to(repo)))
    assert offenders == []


# ---- the shared clock's advance cap (ADR-060 addendum) -----------------------------------------------------------------

from app import auth  # noqa: E402
from app.routers import clock as clock_router  # noqa: E402

clock_app = FastAPI(dependencies=[Depends(policy.enforce_route_policy)])
clock_app.include_router(clock_router.demo_router)
clock_client = TestClient(clock_app)


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.setattr(auth, "demo_controls_active", lambda: True)


def advance(**body):
    return clock_client.post("/clock/advance", json=body, headers=SUP)


def test_advance_over_the_per_call_cap_is_refused_and_does_not_move_the_clock(gate):
    before = clock_module.clock.now()
    r = advance(days=demo_controls.ADVANCE_MAX_DAYS_PER_CALL + 1)
    assert r.status_code == 422 and "at most 7 days" in r.json()["detail"]
    assert advance(hours=24 * 8).status_code == 422  # hours count too
    assert clock_module.clock.now() == before


def test_advances_within_the_cap_work_until_the_window_budget_is_spent(gate):
    for _ in range(4):
        assert advance(days=7).status_code == 200  # 28 of 30
    r = advance(days=3)  # 31 > 30
    assert r.status_code == 409 and "28 of the 30 days" in r.json()["detail"] and "minute" in r.json()["detail"]
    assert advance(days=2).status_code == 200  # exactly 30
    assert advance(hours=1).status_code == 409


def test_the_window_rolls(gate, monkeypatch):
    t = [datetime(2026, 1, 1, tzinfo=UTC)]
    monkeypatch.setattr(demo_controls, "_wall", lambda: t[0])
    for _ in range(4):
        assert advance(days=7).status_code == 200
    assert advance(days=7).status_code == 409
    t[0] = datetime(2026, 1, 1, 6, 1, tzinfo=UTC)  # six hours later the old advances no longer count
    assert advance(days=7).status_code == 200


def test_a_reset_gives_a_full_budget_again(gate):
    write_status(last_reset_at="2026-01-01T00:00:00+00:00")
    for _ in range(4):
        advance(days=7)
    assert advance(days=7).status_code == 409
    write_status(last_reset_at="2026-01-01T06:00:00+00:00")  # the host reset: a new reset id (the host also deletes the file)
    assert advance(days=7).status_code == 200
    (Path(settings.demo_control_dir) / "clock-budget.json").unlink()
    demo_controls.forget()  # the API restarts during a reset
    assert advance(days=7).status_code == 200


def test_the_budget_survives_an_api_restart(gate):
    for _ in range(4):
        advance(days=7)
    demo_controls.forget()
    assert advance(days=7).status_code == 409


def test_outside_the_demo_gate_advance_is_unchanged(monkeypatch):
    monkeypatch.setattr(auth, "demo_controls_active", lambda: False)
    assert advance(days=400).status_code == 200
    assert advance(days=400).status_code == 200
    assert not (Path(settings.demo_control_dir) / "clock-budget.json").exists()


def test_the_reset_script_deletes_the_budget_file():
    s = (API_DIR.parent / "deploy/gcp/vm/demo-reset-watch.sh").read_text()
    assert s.count("clock-budget.json") >= 3  # the header note and both places that clear the baseline
