"""A30 (ADR-041): wall-clock mode. Unit tests; the Postgres half is test_clock_modes_pg.py."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app import auth, main
from app.calendar import WorkingCalendar
from app.clock import SimClock, WallClock, make_clock
from app.config import settings
from app.events import CASE_CREATED, Event, InMemoryEventStore
from app.pathway import load_pathway
from app.routers import cases
from app.sweep import run_sweep

API_DIR = Path(__file__).resolve().parents[1]
PATHWAY = load_pathway(settings.pathway_path)
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)


def test_wall_clock_reads_real_utc_time_and_cannot_be_moved():
    c = WallClock()
    before = datetime.now(UTC)
    now = c.now()
    assert before <= now <= datetime.now(UTC) and now.tzinfo is not None and now.utcoffset() == timedelta(0)
    for call in (lambda: c.advance(days=1), lambda: c.advance_to(now), c.reset):
        with pytest.raises(RuntimeError):
            call()
    assert c.mode == "wall" and SimClock(AT).mode == "sim"


def test_the_wall_clock_source_is_injectable_and_bind_is_a_no_op():
    t = datetime(2027, 3, 1, 12, tzinfo=UTC)
    c = WallClock(source=lambda: t)
    assert c.now() == t and c.bind(object()) == t


def test_unknown_clock_mode_is_refused():
    with pytest.raises(ValueError):
        make_clock("fast")


def _probe(clock_mode: str | None, app_env: str = "dev") -> dict:
    code = (
        "import json, time\n"
        "from fastapi.testclient import TestClient\n"
        "from app import auth\n"
        "from app.main import app\n"
        "from app.config import settings\n"
        "c = TestClient(app)\n"
        "body = auth._b64(json.dumps({'v': 1, 'role': 'supervisor', 'name': 'x', 'iat': int(time.time()), 'exp': int(time.time()) + 3600}, sort_keys=True, separators=(',', ':')).encode())\n"
        "h = {'Authorization': 'Bearer ' + body + '.' + auth._sign(body, settings.dev_auth_secret)}\n"
        "print(json.dumps({'paths': sorted(p for p in app.openapi()['paths'] if p.startswith('/clock')),\n"
        "  'advance': c.post('/clock/advance', json={'days': 1}, headers=h).status_code,\n"
        "  'reset': c.post('/clock/reset', headers=h).status_code,\n"
        "  'health': c.get('/health').json()}))\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in ("APP_ENV", "DATABASE_URL", "DEV_LOGIN_ENABLED", "CLOCK_MODE")}
    env.update(PYTHONPATH=str(API_DIR), DEV_AUTH_SECRET="k" * 40, APP_ENV=app_env)
    if clock_mode is not None:
        env["CLOCK_MODE"] = clock_mode
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=API_DIR, timeout=120)
    if done.returncode != 0:
        raise RuntimeError(done.stderr)
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_wall_mode_has_no_clock_routes_even_in_dev():
    out = _probe("wall")
    assert out["paths"] == [] and out["advance"] == 404 and out["reset"] == 404
    assert out["health"]["clock_mode"] == "wall"
    # health reports real time, not the sim start
    assert abs(datetime.fromisoformat(out["health"]["sim_time"]) - datetime.now(UTC)) < timedelta(minutes=5)


def test_sim_mode_is_the_default_and_keeps_the_dev_clock_routes():
    out = _probe(None)
    assert out["paths"] == ["/clock/advance", "/clock/reset"] and out["advance"] == 200
    assert out["health"]["clock_mode"] == "sim"
    assert out["health"]["sim_time"].startswith("2026-01-0")


def test_an_invalid_clock_mode_does_not_start():
    with pytest.raises(RuntimeError, match="clock_mode"):
        _probe("turbo")


def test_outside_dev_the_clock_mode_must_be_wall(monkeypatch):
    base = {"x": "a" * 32}
    for name, value in (("dev_login_enabled", False), ("dev_auth_secret", "s" * 40), ("scheduler_service_token", "y" * 32),
                        ("watchdog_service_token", "z" * 32), ("automation_service_token", "w" * 32),
                        ("agency_service_token", "v" * 32), ("outreach_demo_approval", False), ("app_env", "prod"),
                        ("fhir_verify_evidence", True),
                        ("auth_mode", "oidc"), ("oidc_issuer", "https://id.example.test/realms/sc")):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(settings, "clock_mode", "sim")
    assert any("CLOCK_MODE" in p for p in auth.secure_config_problems())
    with pytest.raises(RuntimeError, match="CLOCK_MODE"):
        auth.assert_secure_config()
    monkeypatch.setattr(settings, "clock_mode", "wall")
    assert auth.secure_config_problems() == []
    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "clock_mode", "sim")
    auth.assert_secure_config()  # dev may run the simulation clock


def _seed(store, created=AT, n=3):
    ids = []
    for i in range(n):
        cid = uuid4()
        store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, created, "system:intake", {}, uuid4()), PATHWAY.initial_state)
        ids.append(cid)
    return ids


def test_sweep_due_computations_are_identical_given_the_same_instant():
    """The sweep takes `now` as an argument; what differs between the modes is only where `now` comes from. Pin both clocks
    to one instant and the same log produces the same events: same types, ids, times, payloads."""
    instant = AT + timedelta(days=4)
    cases_ids = [uuid4() for _ in range(5)]

    def run(clock) -> list:
        store = InMemoryEventStore()
        for cid in cases_ids:
            store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, AT, "system:intake", {}, cid), PATHWAY.initial_state)
        result = run_sweep(store, PATHWAY, WorkingCalendar(), clock.now())
        events = [(e.case_id, e.type, e.event_id, e.at, json.dumps(e.payload, sort_keys=True))
                  for cid in cases_ids for e in store.events_for(cid)]
        return [result.to_json(), sorted(events, key=str)]

    sim = SimClock(instant)
    wall = WallClock(source=lambda: instant)
    a, b = run(sim), run(wall)
    assert a == b
    assert a[0]["mark_overdue"] == 5  # not vacuous: the instant is past every first SLA


def test_start_clock_in_wall_mode_refuses_a_log_dated_in_the_future(monkeypatch):
    store = InMemoryEventStore()
    _seed(store, created=datetime.now(UTC) + timedelta(days=30), n=1)
    monkeypatch.setattr(cases, "store", store)
    monkeypatch.setattr(main, "clock", WallClock())
    with pytest.raises(RuntimeError, match="CLOCK_MODE=wall"):
        main._start_clock(None)


def test_start_clock_in_wall_mode_accepts_a_log_in_the_past_and_touches_no_clock_store(monkeypatch):
    class Boom:
        def load(self): raise AssertionError("wall mode must not read the sim clock row")
        def save(self, *a): raise AssertionError("wall mode must not write the sim clock row")

    store = InMemoryEventStore()
    _seed(store)
    monkeypatch.setattr(cases, "store", store)
    monkeypatch.setattr(main, "clock", WallClock())
    main._start_clock(Boom())


def test_start_clock_in_sim_mode_catches_up_with_events_written_in_wall_mode(monkeypatch):
    class Mem:
        def __init__(self): self.row = None
        def load(self): return self.row[1] if self.row else None
        def save(self, start, now): self.row = (start, now)

    clock_store = Mem()
    clock_store.save(AT, AT + timedelta(days=1))
    store = InMemoryEventStore()
    _seed(store, created=AT + timedelta(days=40), n=1)
    monkeypatch.setattr(cases, "store", store)
    sim = SimClock(AT)
    monkeypatch.setattr(main, "clock", sim)
    main._start_clock(clock_store)
    assert sim.now() == AT + timedelta(days=40) and clock_store.row[1] == AT + timedelta(days=40)
    # and a log older than the saved time moves nothing
    clock_store.save(AT, AT + timedelta(days=90))
    sim2 = SimClock(AT)
    monkeypatch.setattr(main, "clock", sim2)
    main._start_clock(clock_store)
    assert sim2.now() == AT + timedelta(days=90)


class SharedRow:
    """A ClockStore two SimClocks (two API replicas) share."""

    def __init__(self): self.row = None
    def load(self): return self.row[1] if self.row else None
    def save(self, start, now): self.row = (start, now)


def test_two_replicas_share_the_sim_clock_through_the_store():
    """A31: a move made through one replica reaches the other within its refresh period; moves add up."""
    store = SharedRow()
    a, b = SimClock(AT, store, refresh_seconds=0.0), SimClock(AT, store, refresh_seconds=0.0)
    a.bind(store)
    b.bind(store)
    a.advance(days=2)
    assert b.now() == AT + timedelta(days=2)
    b.advance(days=1)  # adds to a's move, not on top of its own stale view
    assert a.now() == b.now() == AT + timedelta(days=3)
    slow = SimClock(AT, store, refresh_seconds=3600)
    slow.bind(store)
    a.advance(days=1)
    assert slow.now() == AT + timedelta(days=3)  # inside its refresh window it keeps its cached time
