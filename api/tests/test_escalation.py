"""T-A18 and T-A19 (unit/API level): the escalation ladder and its human acceptance.

Timeline used throughout (UAE calendar, Mon-Fri): a case created Monday 5 Jan 08:00Z has its
`acknowledge` SLA (2 working days) due Wednesday 7 Jan 08:00Z. The ladder is reminder at breach+0,
supervisor at +3 working days (Monday 12 Jan 08:00Z) and coordinator at +7 (Friday 16 Jan 08:00Z).
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from api_helpers import login_headers, make_client
from app.calendar import WorkingCalendar
from app.clock import clock
from app.config import settings as app_settings
from app.events import CASE_CREATED, Event, InMemoryEventStore
from app.pathway import load_pathway
from app.routers import cases
from app.state_machine import InvalidTransition
from app.sweep import run_sweep

PATHWAY = load_pathway(app_settings.pathway_path)
UAE = WorkingCalendar()


def utc(m, d, hh=8, mm=0):
    return datetime(2026, m, d, hh, mm, tzinfo=UTC)


def new_store_case(created=utc(1, 5), calendar=UAE):
    store = InMemoryEventStore()
    cid = uuid4()
    store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, created, "system:intake"), PATHWAY.initial_state)
    return store, cid


def sweep(store, cid, now, calendar=UAE):
    return run_sweep(store, PATHWAY, calendar, now, only=[cid])


def escalations(store, cid):
    return [e for e in store.events_for(cid) if e.type == "escalate"]


def rungs(store, cid):
    return [e.payload["rung"] for e in escalations(store, cid)]


def test_rungs_fire_in_order_at_the_configured_working_day_offsets():
    store, cid = new_store_case()
    schedule = [
        (utc(1, 7, 8, 0), []),                      # exactly due: not late
        (utc(1, 7, 9, 0), ["reminder"]),            # breach+0
        (utc(1, 11, 12), ["reminder"]),             # Sunday: nothing new
        (utc(1, 12, 7, 59), ["reminder"]),          # Monday, one minute short of +3 working days
        (utc(1, 12, 8, 0), ["reminder", "supervisor"]),
        (utc(1, 15, 12), ["reminder", "supervisor"]),
        (utc(1, 16, 7, 59), ["reminder", "supervisor"]),
        (utc(1, 16, 8, 0), ["reminder", "supervisor", "coordinator"]),
        (utc(3, 1), ["reminder", "supervisor", "coordinator"]),  # nothing after the last rung
    ]
    for now, expected in schedule:
        sweep(store, cid, now)
        assert rungs(store, cid) == expected, now
    types = [e.type for e in store.events_for(cid)]
    assert types == ["case_created", "mark_overdue", "escalate", "escalate", "escalate"]
    first, second, third = escalations(store, cid)
    assert [(e.payload["rung"], e.payload["level"], e.payload["target"]) for e in (first, second, third)] == [
        ("reminder", 1, "owner"), ("supervisor", 2, "supervisor"), ("coordinator", 3, "coordinator"),
    ]
    assert [e.at for e in (first, second, third)] == [utc(1, 7, 9), utc(1, 12, 8), utc(1, 16, 8)]
    assert first.payload["rung_due_at"] == utc(1, 7, 8).isoformat()
    assert second.payload["rung_due_at"] == utc(1, 12, 8).isoformat()
    assert third.payload["rung_due_at"] == utc(1, 16, 8).isoformat()
    assert all(e.actor == "system:scheduler" and e.payload["state"] == "result_received" for e in (first, second, third))


def test_a_long_outage_catches_up_in_order_without_skipping():
    store, cid = new_store_case()
    sweep(store, cid, utc(3, 1))  # the scheduler was down for weeks
    assert rungs(store, cid) == ["reminder", "supervisor", "coordinator"]
    assert [e.seq for e in store.events_for(cid)] == [1, 2, 3, 4, 5]
    for _ in range(3):
        sweep(store, cid, utc(3, 2))
    assert len(store.events_for(cid)) == 5  # each rung once, whatever the number of sweeps


def test_a_holiday_pushes_the_rung_dates():
    holiday = WorkingCalendar(holidays=frozenset({utc(1, 12).date()}))  # the Monday
    store, cid = new_store_case()
    sweep(store, cid, utc(1, 12, 8), calendar=holiday)
    assert rungs(store, cid) == ["reminder"]
    sweep(store, cid, utc(1, 13, 8), calendar=holiday)  # +3 working days is now Tuesday
    assert rungs(store, cid) == ["reminder", "supervisor"]


def test_escalation_changes_no_ownership_and_no_state():
    store, cid = new_store_case()
    store.append(Event(cid, "acknowledge", utc(1, 6), "lab:l"), PATHWAY)
    store.append(Event(cid, "accept_ownership", utc(1, 6, 9), "primary_care_owner:k"), PATHWAY)
    before = store.read_model(cid, PATHWAY)
    sweep(store, cid, utc(3, 1))
    after = store.read_model(cid, PATHWAY)
    assert rungs(store, cid) == ["reminder", "supervisor", "coordinator"]
    assert (after.state, after.owner) == (before.state, before.owner) == ("owner_accepted", "primary_care_owner:k")
    assert after.overdue and after.escalation_level == 3 and not after.escalation_accepted


def reach_supervisor_rung():
    store, cid = new_store_case()
    sweep(store, cid, utc(1, 12, 8))
    assert rungs(store, cid) == ["reminder", "supervisor"]
    return store, cid


def accept(store, cid, actor, day=12, hour=9, **payload):
    return store.append(Event(cid, "escalation_accepted", utc(1, day, hour), actor, payload), PATHWAY)


@pytest.mark.parametrize(
    "actor",
    ["system:scheduler", "watchdog", "model:drafter", "scheduler", "coordinator:co", "lab:l",
     "primary_care_owner:k", "specialist_owner:s", "patient:p"],
)
def test_only_a_human_in_the_rung_target_role_can_accept(actor):
    store, cid = reach_supervisor_rung()
    count = len(store.events_for(cid))
    with pytest.raises(InvalidTransition):
        accept(store, cid, actor)
    assert len(store.events_for(cid)) == count
    assert not store.read_model(cid, PATHWAY).escalation_accepted


def test_a_supervisor_accepting_the_rung_stops_the_ladder_for_this_breach():
    store, cid = reach_supervisor_rung()
    accept(store, cid, "supervisor:sam")
    model = store.read_model(cid, PATHWAY)
    assert model.escalation_accepted and model.escalation_level == 2
    assert model.owner is None  # accepting an escalation does not assign anyone
    sweep(store, cid, utc(3, 1))
    assert rungs(store, cid) == ["reminder", "supervisor"]  # no coordinator rung
    with pytest.raises(InvalidTransition):
        accept(store, cid, "supervisor:sam2", day=13)  # not twice
    with pytest.raises(InvalidTransition):  # not even by hand
        store.append(Event(cid, "escalate", utc(1, 20), "coordinator:co",
                           {"rung": "coordinator", "state": "result_received"}), PATHWAY)


def test_the_owner_accepting_the_reminder_stops_everything_after_it():
    store, cid = new_store_case()
    sweep(store, cid, utc(1, 7, 9))
    assert rungs(store, cid) == ["reminder"]
    accept(store, cid, "primary_care_owner:k", day=7, hour=10, rung="reminder")
    sweep(store, cid, utc(3, 1))
    assert rungs(store, cid) == ["reminder"]


def test_acceptance_must_name_the_latest_rung():
    store, cid = reach_supervisor_rung()
    with pytest.raises(InvalidTransition):
        accept(store, cid, "supervisor:sam", rung="reminder")
    accept(store, cid, "supervisor:sam", rung="supervisor")


def test_accepting_with_nothing_to_accept_is_refused():
    store, cid = new_store_case()
    with pytest.raises(InvalidTransition):
        accept(store, cid, "supervisor:sam", day=6)
    sweep(store, cid, utc(1, 7, 9))  # only the reminder exists: a supervisor is not its target
    with pytest.raises(InvalidTransition):
        accept(store, cid, "supervisor:sam", day=7, hour=10)


def test_progress_resolves_the_breach_and_a_new_breach_gets_a_fresh_ladder():
    store, cid = reach_supervisor_rung()
    accept(store, cid, "supervisor:sam")
    store.append(Event(cid, "acknowledge", utc(1, 13), "lab:l"), PATHWAY)
    model = store.read_model(cid, PATHWAY)
    assert (model.overdue, model.escalation_level, model.escalation_accepted) == (False, 0, False)
    # accept_ownership is due 2 working days after Tue 13th: Thu 15th 08:00Z
    sweep(store, cid, utc(1, 15, 9))
    assert rungs(store, cid) == ["reminder", "supervisor", "reminder"]  # a new reminder, new breach
    reminders = [e for e in escalations(store, cid) if e.payload["rung"] == "reminder"]
    assert reminders[0].event_id != reminders[1].event_id
    assert reminders[1].payload["state"] == "acknowledged"


def test_a_manual_escalation_is_ordered_too_and_the_sweep_does_not_repeat_it():
    store, cid = new_store_case()
    sweep(store, cid, utc(1, 7, 9))
    with pytest.raises(InvalidTransition):  # skipping the supervisor rung
        store.append(Event(cid, "escalate", utc(1, 8), "coordinator:co",
                           {"rung": "coordinator", "state": "result_received"}), PATHWAY)
    store.append(Event(cid, "escalate", utc(1, 8), "coordinator:co",
                       {"rung": "supervisor", "state": "result_received"}), PATHWAY)
    sweep(store, cid, utc(1, 12, 9))
    assert rungs(store, cid) == ["reminder", "supervisor"]  # the scheduler saw level 2 and moved on
    sweep(store, cid, utc(1, 16, 9))
    assert rungs(store, cid) == ["reminder", "supervisor", "coordinator"]


def test_the_regulator_rung_is_not_built():
    assert [r.rung for r in PATHWAY.escalation_ladder] == ["reminder", "supervisor", "coordinator"]
    store, cid = new_store_case()
    sweep(store, cid, utc(1, 7, 9))
    with pytest.raises(InvalidTransition):
        store.append(Event(cid, "escalate", utc(1, 8), "supervisor:sam",
                           {"rung": "regulator", "state": "result_received"}), PATHWAY)


def test_a_consent_excursion_keeps_the_ladder_position():
    store, cid = reach_supervisor_rung()
    store.append(Event(cid, "consent_updated", utc(1, 12, 10), "patient:p", {"withdrawn": True}), PATHWAY)
    sweep(store, cid, utc(1, 16, 9))  # consent_withdrawn has no SLA: the scheduler leaves it alone
    assert rungs(store, cid) == ["reminder", "supervisor"]
    store.append(Event(cid, "consent_updated", utc(1, 16, 10), "patient:p", {"withdrawn": False}), PATHWAY)
    sweep(store, cid, utc(1, 16, 11))  # back in result_received, still overdue: the next rung is due
    assert rungs(store, cid) == ["reminder", "supervisor", "coordinator"]


# ---- over HTTP ----------------------------------------------------------------------------------------

client = make_client()
SUP = login_headers(client, "supervisor", "sam")
COORD = login_headers(client, "coordinator", "bea")
OWNER = login_headers(client, "primary_care_owner", "dr k")
SCHED = {"Authorization": f"Bearer {app_settings.scheduler_service_token}"}


@pytest.fixture
def http_case(monkeypatch):
    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    clock.reset()
    case_id = client.post("/cases", json={"patient_ref": "Patient/1"}, headers=COORD).json()["case_id"]
    yield case_id
    clock.reset()


def post(case_id, type_, headers, payload=None):
    return client.post(f"/cases/{case_id}/events", json={"type": type_, "payload": payload or {}}, headers=headers)


def test_escalation_and_acceptance_over_http(http_case):
    case_id = http_case
    client.post("/clock/advance", json={"days": 7}, headers=SUP)  # Monday 12 Jan 08:00Z
    out = client.post("/scheduler/sweep", headers=SCHED).json()
    assert (out["mark_overdue"], out["escalate"]) == (1, 2)  # breach, reminder and supervisor rung
    model = client.get(f"/cases/{case_id}", headers=COORD).json()["read_model"]
    assert model["overdue"] and model["escalation_level"] == 2 and model["owner"] is None

    # the service token cannot accept anything; the wrong humans cannot; the right one can
    assert post(case_id, "escalation_accepted", SCHED).status_code == 403
    assert post(case_id, "escalation_accepted", COORD).status_code == 409
    assert post(case_id, "escalation_accepted", OWNER).status_code == 409
    accepted = post(case_id, "escalation_accepted", SUP)
    assert accepted.status_code == 201
    assert accepted.json()["events"][-1]["actor"] == "supervisor:sam"
    assert accepted.json()["read_model"]["escalation_accepted"] is True

    client.post("/clock/advance", json={"days": 14}, headers=SUP)
    assert client.post("/scheduler/sweep", headers=SCHED).json()["escalate"] == 0
    case = client.get(f"/cases/{case_id}", headers=COORD).json()
    assert [e["payload"]["rung"] for e in case["events"] if e["type"] == "escalate"] == ["reminder", "supervisor"]


def test_humans_cannot_impersonate_the_scheduler(http_case):
    client.post("/clock/advance", json={"days": 3}, headers=SUP)
    assert post(http_case, "mark_overdue", SUP, {"state": "result_received", "due_at": "2026-01-07T08:00:00+00:00"}).status_code == 403
    assert post(http_case, "watchdog_flagged", COORD).status_code == 403
    assert client.post("/scheduler/sweep", headers=SCHED).json()["mark_overdue"] == 1
