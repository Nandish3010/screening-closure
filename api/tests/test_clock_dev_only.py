"""SEC-1 fix 3: the demo clock routes exist only in APP_ENV=dev, and reset never rewinds past events."""

import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from api_helpers import login_headers, make_client

from app import clock as clock_module
from app.events import CASE_CREATED, Event, InMemoryEventStore
from app.routers import cases

API_DIR = Path(__file__).resolve().parents[1]
client = make_client()
SUP = login_headers(client, "supervisor", "clock sup")
COORD = login_headers(client, "coordinator", "clock coord")


@pytest.fixture(autouse=True)
def fresh_state():
    saved = cases.store
    cases.store = InMemoryEventStore()
    clock_module.clock.reset()
    yield
    clock_module.clock.reset()
    cases.store = saved


def _open_case_at(at) -> str:
    case_id = client.post("/cases", json={"patient_ref": "Patient/1"}, headers=COORD)
    assert case_id.status_code == 201, case_id.text
    return case_id.json()["case_id"]


def test_reset_is_allowed_while_nothing_is_dated_after_the_start():
    assert client.post("/clock/reset", headers=SUP).status_code == 200  # nothing at all
    _open_case_at(clock_module.clock.now())  # case_created at exactly the start time
    assert client.post("/clock/advance", json={"days": 2}, headers=SUP).status_code == 200
    out = client.post("/clock/reset", headers=SUP)  # advanced, but no event after the start
    assert out.status_code == 200
    assert out.json()["sim_time"] == clock_module.clock.start.isoformat()


def test_reset_is_409_once_an_event_is_dated_after_the_start():
    case_id = _open_case_at(clock_module.clock.now())
    client.post("/clock/advance", json={"days": 2}, headers=SUP)
    r = client.post(f"/cases/{case_id}/events", json={"type": "acknowledge", "payload": {}}, headers=login_headers(client, "lab", "clock lab"))
    assert r.status_code in (200, 201), r.text
    before = clock_module.clock.now()
    resp = client.post("/clock/reset", headers=SUP)
    assert resp.status_code == 409
    assert "events exist after" in resp.json()["detail"]
    assert clock_module.clock.now() == before  # nothing moved


def test_a_case_created_after_the_start_also_blocks_reset():
    client.post("/clock/advance", json={"hours": 1}, headers=SUP)
    _open_case_at(clock_module.clock.now())
    assert client.post("/clock/reset", headers=SUP).status_code == 409


def test_has_events_after_boundary_is_strict():
    store = InMemoryEventStore()
    case_id = uuid4()
    at = clock_module.clock.start
    store.create_case(case_id, "p", Event(case_id, CASE_CREATED, at, "system:intake", {}), "s")
    assert store.has_events_after(at) is False
    assert store.has_events_after(at - timedelta(seconds=1)) is True
    assert store.has_events_after(at + timedelta(seconds=1)) is False


def _probe(env_value: str | None) -> dict:
    """Import the app in a fresh interpreter with APP_ENV set (the routers are chosen at import time)
    and report the status of every clock route as a supervisor would see it."""
    code = (
        "import json\n"
        "from fastapi.testclient import TestClient\n"
        "from app import auth\n"
        "from app.main import app\n"
        "c = TestClient(app)\n"
        "login = c.post('/auth/dev-login', json={'role': 'supervisor', 'name': 'x'})\n"
        "body = auth._b64(json.dumps({'v': 1, 'role': 'supervisor', 'name': 'x', 'exp': 4102444800}, sort_keys=True, separators=(',', ':')).encode())\n"
        "from app.config import settings\n"
        "h = {'Authorization': 'Bearer ' + body + '.' + auth._sign(body, settings.dev_auth_secret)}\n"
        "assert c.get('/me', headers=h).status_code == 200  # the supervisor token itself is valid\n"
        "print(json.dumps({'login': login.status_code,\n"
        "  'paths': sorted(p for p in app.openapi()['paths'] if p.startswith('/clock')),\n"
        "  'advance': c.post('/clock/advance', json={'days': 1}, headers=h).status_code,\n"
        "  'reset': c.post('/clock/reset', headers=h).status_code}))\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in ("APP_ENV", "DATABASE_URL", "DEV_LOGIN_ENABLED")}
    env["PYTHONPATH"] = str(API_DIR)
    env["DEV_AUTH_SECRET"] = "k" * 40  # a valid, non-default secret, so a supervisor token verifies in every environment
    if env_value is not None:
        env["APP_ENV"] = env_value
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=API_DIR, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_clock_routes_exist_in_dev():
    out = _probe("dev")
    assert out["paths"] == ["/clock/advance", "/clock/reset"]
    assert out["advance"] == 200 and out["reset"] in (200, 409)


@pytest.mark.parametrize("env", ["prod", "production", "staging", "test", "Dev"])
def test_clock_routes_are_404_outside_dev(env):
    out = _probe(env)
    assert out["paths"] == []
    assert out["advance"] == 404 and out["reset"] == 404
    assert out["login"] == 404  # the passwordless login is dev only as well
