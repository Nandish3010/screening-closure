"""ADR-057: the hosted demo's judge-experience controls: the gate, the fresh-case picker, the reset request and the simulated missed
follow-up. The functional tests mount the real router on a small app behind the real policy table; the gate tests start the real
app in a subprocess under each configuration."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
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


ROUTES = ["/demo/guide", "/demo/missed-followup", "/demo/reset"]


def test_the_controls_exist_only_under_both_flags_with_the_sim_clock():
    for extra in (dict(APP_ENV="dev"),  # a developer's stack: none
                  dict(APP_ENV="dev", DEMO_SIMULATED_CLOCK="1"),  # one flag
                  dict(APP_ENV="dev", DEMO_DEPLOYMENT="1"),
                  dict(APP_ENV="production", CLOCK_MODE="wall", DEMO_DEPLOYMENT="1"),
                  dict(APP_ENV="production", CLOCK_MODE="wall")):
        out = _probe(**extra)
        assert out["paths"] == [] and "demo" not in out["health"], extra
        assert out["guide"] == out["reset"] == out["missed"] == 404 or out["guide"] in (401, 403, 404)


def test_the_hosted_demo_gate_registers_them_and_health_reports_the_status():
    out = _probe(APP_ENV="production", CLOCK_MODE="sim", DEMO_SIMULATED_CLOCK="1", DEMO_DEPLOYMENT="1")
    assert out["paths"] == ROUTES
    assert out["clock"] == ["/clock/advance"]  # still no clock reset
    assert out["guide"] in (401, 403) and out["reset"] in (401, 403) and out["missed"] in (401, 403)  # nothing anonymous
    demo_block = out["health"]["demo"]
    assert demo_block["simulated_clock"] is True and demo_block["deployment"] is True
    assert {"last_reset_at", "last_reset_by", "next_auto_reset_at", "reset_pending"} <= set(demo_block)


def test_a_dev_stack_with_both_flags_previews_the_demo():
    out = _probe(APP_ENV="dev", CLOCK_MODE="sim", DEMO_SIMULATED_CLOCK="1", DEMO_DEPLOYMENT="1")
    assert out["paths"] == ROUTES and out["health"]["demo"]["deployment"] is True


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


def test_status_shows_when_who_and_the_next_scheduled_reset():
    assert demo_controls.status()["last_reset_at"] is None and demo_controls.status()["reset_id"] == "initial"
    write_status(last_reset_at="2026-11-08T06:00:00+00:00", last_reset_by="Noura (supervisor)", last_reset_reason="manual")
    st = demo_controls.status()
    assert st["last_reset_by"] == "Noura (supervisor)" and st["next_auto_reset_at"].startswith("2026-11-08T12:00:00")


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
    assert demo.get("/demo/guide?session=session-cccc", headers=COORD).json()["steps"]["record"] is None


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
