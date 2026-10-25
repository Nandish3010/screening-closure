"""T-A30 (A11): idle-case detection. An open case with no events for WATCHDOG_IDLE_DAYS of
simulation time is flagged exactly once per idle episode; closed cases are never flagged; any new
event clears the flag. Run on the in-memory store (`make test`) and on Postgres (`make test-db`)."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from api_helpers import login_headers, make_client
from app import ops
from app.clock import clock
from app.config import settings as app_settings
from app.events import CASE_CREATED, Event, InMemoryEventStore
from app.pathway import load_pathway
from app.routers import cases
from app.watchdog import WATCHDOG_ACTOR, idle_flag_event_id, plan_idle_flag, run_idle_scan

PATHWAY = load_pathway(app_settings.pathway_path)
T0 = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
LAB, OWNER, PAT, SPEC = "lab:l", "primary_care_owner:k", "patient:p", "specialist_owner:s"
HAS_DB = bool(os.environ.get("DATABASE_URL"))
BACKENDS = ["memory", pytest.param("postgres", marks=[pytest.mark.integration,
                                                      pytest.mark.skipif(not HAS_DB, reason="DATABASE_URL not set")])]


@pytest.fixture(params=BACKENDS)
def store(request):
    if request.param == "memory":
        yield InMemoryEventStore()
    else:
        from app.db import create_pool, run_migrations
        from app.store_pg import PostgresEventStore

        pool = create_pool(os.environ["DATABASE_URL"], max_size=4)
        run_migrations(pool)
        yield PostgresEventStore(pool)
        pool.close()


def day(n):
    return T0 + timedelta(days=n)


def new_case(store, events=(), created=0):
    """A case with `events` = [(type, actor, day, payload)]."""
    cid = uuid4()
    store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, day(created), "system:intake", {}), PATHWAY.initial_state)
    for type_, actor, n, payload in events:
        store.append(Event(cid, type_, day(n), actor, payload or {}), PATHWAY)
    return cid


def flags(store, cid):
    return [e for e in store.events_for(cid) if e.type == "watchdog_flagged"]


def scan(store, cid, n, idle_days=7):
    return run_idle_scan(store, PATHWAY, day(n), idle_days, only=[cid])


def test_t_a30_a_case_idle_for_exactly_n_days_is_flagged_and_one_day_less_is_not(store):
    cid = new_case(store, [("acknowledge", LAB, 1, None)])
    assert scan(store, cid, 7.99 + 0.0).flagged == 0 and flags(store, cid) == []  # 6.99 days after day 1
    assert scan(store, cid, 8).flagged == 1  # exactly 7 days after the last event
    (flag,) = flags(store, cid)
    assert flag.actor == WATCHDOG_ACTOR and flag.at == day(8)
    assert flag.payload == {"reason": "idle", "idle_days": 7, "last_activity_at": day(1).isoformat()}
    last_event = store.events_for(cid)[-2]  # the acknowledge the idle stretch runs from
    assert flag.event_id == idle_flag_event_id(cid, last_event.event_id)


def test_t_a30_a_flag_is_written_once_per_idle_episode(store):
    cid = new_case(store, [("acknowledge", LAB, 1, None)])
    for n in (8, 8, 9, 15, 40):  # many scans, including repeats at the same instant
        scan(store, cid, n)
    assert len(flags(store, cid)) == 1
    model = store.read_model(cid, PATHWAY)
    assert model.watchdog_flag["reason"] == "idle" and model.watchdog_flag["idle_days"] == 7
    # the flag is not activity: the idle clock still runs from the last real event
    assert model.last_activity_at == day(1) and model.last_event_at == day(8)


def test_t_a30_new_activity_clears_the_flag_and_a_later_quiet_spell_is_a_new_episode(store):
    cid = new_case(store, [("acknowledge", LAB, 1, None)])
    scan(store, cid, 8)
    assert store.read_model(cid, PATHWAY).watchdog_flag is not None
    store.append(Event(cid, "accept_ownership", day(9), OWNER, {}), PATHWAY)  # any event clears it
    model = store.read_model(cid, PATHWAY)
    assert model.watchdog_flag is None and model.last_activity_at == day(9)
    assert scan(store, cid, 15).flagged == 0 and len(flags(store, cid)) == 1  # six days quiet: fine
    assert scan(store, cid, 16).flagged == 1  # seven days quiet since day 9: a second episode
    assert len(flags(store, cid)) == 2
    assert flags(store, cid)[0].event_id != flags(store, cid)[1].event_id
    assert scan(store, cid, 50).flagged == 0 and len(flags(store, cid)) == 2


def test_t_a30_a_scheduler_event_is_not_activity_and_does_not_clear_the_flag(store):
    """SEC-2: only a person looking at the case counts. The sweep's mark_overdue used to restart the idle
    clock and clear the flag, so a case the scheduler kept escalating could never be flagged idle."""
    cid = new_case(store, [("acknowledge", LAB, 1, None)])
    scan(store, cid, 8)
    assert store.read_model(cid, PATHWAY).watchdog_flag is not None
    due = day(1) + timedelta(days=4)  # accept_ownership is 2 working days; the sweep marks it overdue on day 8
    store.append(Event(cid, "mark_overdue", day(8.5), "system:scheduler",
                       {"state": "acknowledged", "due_at": due.isoformat()}), PATHWAY)
    m = store.read_model(cid, PATHWAY)
    assert m.watchdog_flag is not None  # still flagged: nobody has looked
    assert m.last_activity_at == day(8.5) and m.last_human_activity_at == day(1)
    assert scan(store, cid, 9).flagged == 0  # and not flagged twice
    store.append(Event(cid, "callback_requested", day(9.5), "coordinator:co", {}), PATHWAY)  # a person acts
    m = store.read_model(cid, PATHWAY)
    assert m.watchdog_flag is None and m.last_human_activity_at == day(9.5)


def test_t_a30_a_case_the_sweep_keeps_escalating_is_still_idle(store):
    """The review's scenario: acknowledged on day 1, then only automated events. The idle clock runs from
    day 1, so the flag appears at day 8 whatever the scheduler wrote on days 5-7."""
    cid = new_case(store, [("acknowledge", LAB, 1, None)])
    due = day(1) + timedelta(days=4)
    store.append(Event(cid, "mark_overdue", day(5.5), "system:scheduler",
                       {"state": "acknowledged", "due_at": due.isoformat()}), PATHWAY)
    store.append(Event(cid, "escalate", day(6), "system:scheduler",
                       {"rung": "reminder", "state": "acknowledged", "level": 1, "target": "owner"}), PATHWAY)
    assert scan(store, cid, 7.9).flagged == 0  # 6.9 days since a person acted
    assert scan(store, cid, 8).flagged == 1
    (flag,) = flags(store, cid)
    assert flag.payload["last_activity_at"] == day(1).isoformat() and flag.payload["idle_days"] == 7


def test_t_a30_the_notifier_a_drafting_model_and_the_consent_service_are_not_activity(store):
    cid = new_case(store, [("acknowledge", LAB, 1, None), ("accept_ownership", OWNER, 2, None)])
    store.append(Event(cid, "message_drafted", day(5), "model:template",
                       {"draft_id": "5b6c1f3e-8d2a-4c47-9e10-2a7f4b9d3c11", "provider": "template", "fallback_used": False,
                        "prompt_hash": "0" * 64, "language": "en"}), PATHWAY)
    assert store.read_model(cid, PATHWAY).last_human_activity_at == day(2)
    assert scan(store, cid, 9).flagged == 1  # 7 days after the owner accepted


def test_t_a30_the_idle_flag_id_comes_from_the_event_not_the_time(store):
    """Two different episodes that start at the same instant must not share a flag id."""
    cid = new_case(store, [("acknowledge", LAB, 1, None)])
    scan(store, cid, 8)
    (first,) = flags(store, cid)
    store.append(Event(cid, "callback_requested", day(9), "coordinator:co", {}), PATHWAY)  # clears it
    store.append(Event(cid, "callback_requested", day(9), "coordinator:co", {}), PATHWAY)  # same instant again
    assert scan(store, cid, 16).flagged == 1
    second = flags(store, cid)[-1]
    assert second.event_id != first.event_id
    last_human = [e for e in store.events_for(cid) if e.type == "callback_requested"][-1]
    assert second.event_id == idle_flag_event_id(cid, last_human.event_id)


def test_t_a30_closed_cases_are_never_flagged(store):
    steps = [("acknowledge", LAB, 1, None), ("accept_ownership", OWNER, 2, None), ("contact_patient", OWNER, 3, None),
             ("patient_acknowledge", PAT, 4, None), ("book_confirmatory", SPEC, 5, None),
             ("complete_confirmatory", SPEC, 6, None), ("clinician_review", SPEC, 7, None),
             ("close", SPEC, 8, {"evidence_ref": "DiagnosticReport/1", "signed_by": SPEC})]
    cid = new_case(store, steps)
    assert store.current_state(cid) == "closed"
    assert scan(store, cid, 500).flagged == 0 and flags(store, cid) == []


@pytest.mark.parametrize("withdrawn", [False, True])
def test_t_a30_side_states_are_open_and_flagged_too(store, withdrawn):
    events = [("acknowledge", LAB, 1, None)]
    if withdrawn:
        events.append(("consent_updated", PAT, 2, {"withdrawn": True}))
    cid = new_case(store, events)
    assert scan(store, cid, 30).flagged == 1


def test_t_a30_the_threshold_is_configurable_and_active_cases_are_not_flagged(store):
    busy = new_case(store, [("acknowledge", LAB, 1, None), ("accept_ownership", OWNER, 6, None)])
    quiet = new_case(store, [("acknowledge", LAB, 1, None)])
    res = run_idle_scan(store, PATHWAY, day(9), 7, only=[busy, quiet])
    assert res.flagged == 1 and flags(store, busy) == [] and len(flags(store, quiet)) == 1
    res = run_idle_scan(store, PATHWAY, day(9), 3, only=[busy, quiet])
    assert res.flagged == 1 and len(flags(store, busy)) == 1  # 3 days idle at day 9: now busy qualifies


def test_t_a30_planning_is_pure_and_reads_no_clock():
    from app.state_machine import replay_model

    cid = uuid4()
    events = [Event(cid, CASE_CREATED, day(0), "system:intake", {}), Event(cid, "acknowledge", day(1), LAB, {})]
    model = replay_model(events, PATHWAY)
    assert plan_idle_flag(cid, model, PATHWAY, day(7.99), 7) is None
    a, b = plan_idle_flag(cid, model, PATHWAY, day(8), 7), plan_idle_flag(cid, model, PATHWAY, day(12), 7)
    assert a.event_id == b.event_id and a.type == "watchdog_flagged" and a.payload["idle_days"] == 7
    assert b.payload["idle_days"] == 11


# ---- over HTTP: the service token, the route, the event policy ----------------------------------------------

client = make_client()
WD = {"Authorization": f"Bearer {app_settings.watchdog_service_token}"}
SCHED = {"Authorization": f"Bearer {app_settings.scheduler_service_token}"}


@pytest.fixture
def served(monkeypatch):
    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    monkeypatch.setattr(ops, "store", ops.InMemoryOpsStore(wall=lambda: 1000.0))
    clock.reset()
    yield cases.store
    clock.reset()


def H(role, name=None):
    return login_headers(client, role, name or f"wd {role}")


def http_case():
    cid = client.post("/cases", json={"patient_ref": "Patient/wd"}, headers=H("coordinator")).json()["case_id"]
    assert client.post(f"/cases/{cid}/events", json={"type": "acknowledge"}, headers=H("lab")).status_code == 201
    return cid


def test_t_a30_the_scan_route_flags_on_the_sim_clock_not_the_wall_clock(served):
    cid = http_case()
    body = client.post("/watchdog/scan", json={"uptime_seconds": 5}, headers=WD).json()
    assert body["scan"]["flagged"] == 0  # nothing has advanced the sim clock: no matter how long the wall clock runs
    clock.advance(days=7)
    body = client.post("/watchdog/scan", json={"uptime_seconds": 5}, headers=WD).json()
    assert body["scan"]["flagged"] == 1 and body["scan"]["idle_days"] == 7 and body["scan"]["errors"] == []
    assert client.post("/watchdog/scan", json={"uptime_seconds": 5}, headers=WD).json()["scan"]["flagged"] == 0
    row = next(r for r in client.get("/cases", params={"flagged": "true"}, headers=H("coordinator")).json()
               if r["case_id"] == cid)
    assert row["watchdog_flagged"]["reason"] == "idle" and row["watchdog_flagged"]["idle_days"] == 7
    d = client.get(f"/cases/{cid}", headers=H("coordinator")).json()
    assert d["events"][-1]["type"] == "watchdog_flagged" and d["events"][-1]["actor"] == "system:watchdog"
    # any new event clears it
    assert client.post(f"/cases/{cid}/events", json={"type": "accept_ownership"}, headers=H("primary_care_owner")).status_code == 201
    assert client.get("/cases", params={"flagged": "true"}, headers=H("coordinator")).json() == []


def test_t_a30_the_scan_takes_the_threshold_from_the_watchdog(served):
    http_case()
    clock.advance(days=3)
    assert client.post("/watchdog/scan", json={}, headers=WD).json()["scan"]["flagged"] == 0  # default 7
    assert client.post("/watchdog/scan", json={"idle_days": 3}, headers=WD).json()["scan"]["flagged"] == 1
    assert client.post("/watchdog/scan", json={"idle_days": 0}, headers=WD).status_code == 422


def test_t_a30_only_the_watchdog_token_may_scan(served):
    for headers in (SCHED, H("supervisor"), H("coordinator"), H("lab")):
        assert client.post("/watchdog/scan", json={}, headers=headers).status_code == 403
    assert client.post("/watchdog/scan", json={}).status_code == 401


def test_t_a30_the_watchdog_token_may_emit_watchdog_flagged_and_nothing_else(served):
    cid = http_case()
    payload = {"reason": "idle", "idle_days": 7}
    ok = client.post(f"/cases/{cid}/events", json={"type": "watchdog_flagged", "payload": payload}, headers=WD)
    assert ok.status_code == 201 and ok.json()["events"][-1]["actor"] == "system:watchdog"
    n = len(client.get(f"/cases/{cid}", headers=H("coordinator")).json()["events"])
    for type_ in ("acknowledge", "accept_ownership", "close", "mark_overdue", "escalate", "consent_updated",
                  "handoff_rejected", "message_sent", "reopen"):
        r = client.post(f"/cases/{cid}/events", json={"type": type_, "payload": {}}, headers=WD)
        assert r.status_code == 403, (type_, r.text)
    assert len(client.get(f"/cases/{cid}", headers=H("coordinator")).json()["events"]) == n
    # ... and the scheduler no longer may
    assert client.post(f"/cases/{cid}/events", json={"type": "watchdog_flagged", "payload": payload}, headers=SCHED).status_code == 403
    # a user cannot either
    assert client.post(f"/cases/{cid}/events", json={"type": "watchdog_flagged", "payload": payload}, headers=H("supervisor")).status_code == 403
    # the watchdog cannot read cases or create them
    assert client.get("/cases", headers=WD).status_code == 403
    assert client.post("/cases", json={}, headers=WD).status_code == 403


def test_t_a30_a_closed_case_refuses_a_flag_even_from_the_watchdog(served):
    cid = http_case()
    # walk to closed by direct appends
    for type_, actor, payload in [("accept_ownership", OWNER, {}), ("contact_patient", OWNER, {}), ("patient_acknowledge", PAT, {}),
                                  ("book_confirmatory", SPEC, {}), ("complete_confirmatory", SPEC, {}), ("clinician_review", SPEC, {}),
                                  ("close", SPEC, {"evidence_ref": "DiagnosticReport/1", "signed_by": SPEC})]:
        cases.store.append(Event(UUID(cid), type_, clock.now(), actor, payload), PATHWAY)
    r = client.post(f"/cases/{cid}/events", json={"type": "watchdog_flagged", "payload": {"reason": "idle"}}, headers=WD)
    assert r.status_code == 409
