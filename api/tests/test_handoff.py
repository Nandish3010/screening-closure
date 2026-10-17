"""T-A20, T-A21, T-A22 (in-memory store; the Postgres versions are in test_handoff_pg.py):
the referral handoff contract, per-owner scoping, and the scheduler's bounce."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from api_helpers import actor_of, login_headers, make_client
from app.calendar import WorkingCalendar
from app.clock import clock
from app.config import settings as app_settings
from app.events import CASE_CREATED, Event, InMemoryEventStore
from app.pathway import load_pathway
from app.routers import cases
from app.state_machine import InvalidTransition, PayloadError, is_automated, step
from app.sweep import SCHEDULER_ACTOR, handoff_due_at, plan_bounce, plan_case, run_sweep
from test_state_machine import OWNER, TO_CONTACTED, drive, make_event

PATHWAY = load_pathway(app_settings.pathway_path)
UAE = WorkingCalendar()
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # Monday
REASONS = ["capacity", "wrong_service", "missing_info", "patient_declined"]


def at(day=0, hours=0):
    return AT + timedelta(days=day, hours=hours)


def requested(to_provider="endo-1", on_day=0):
    """A model with a pending handoff, requested on `on_day` (days after Monday 5 Jan 08:00)."""
    m = drive(*TO_CONTACTED, ("patient_acknowledge", "patient:p", {}))
    return step(m, make_event("handoff_requested", OWNER, {"to_provider": to_provider}, at(on_day)), PATHWAY)


# ---- the state machine: payloads, preconditions, effects ---------------------------------------


def test_the_taxonomy_and_the_timeout_come_from_the_yaml():
    assert PATHWAY.handoff_response_working_days == 3
    assert set(PATHWAY.handoff_reject_reasons) == set(REASONS)
    assert PATHWAY.handoff_bounce_reason == "timeout" and "timeout" not in REASONS


@pytest.mark.parametrize("payload", [{}, {"to_provider": ""}, {"to_provider": "  "}, {"to_provider": 3}])
def test_a_request_must_name_a_provider(payload):
    m = drive(*TO_CONTACTED)
    with pytest.raises(PayloadError):
        step(m, make_event("handoff_requested", OWNER, payload), PATHWAY)


def test_a_request_needs_an_owner_and_only_one_may_be_pending():
    with pytest.raises(InvalidTransition, match="no owner"):
        step(drive(("acknowledge", "lab:l", {})), make_event("handoff_requested", "supervisor:su", {"to_provider": "x"}), PATHWAY)
    m = requested()
    with pytest.raises(InvalidTransition, match="already pending"):
        step(m, make_event("handoff_requested", OWNER, {"to_provider": "endo-2"}), PATHWAY)


def test_ownership_changes_only_on_acceptance():
    m = requested("endo-1")
    assert m.owner == OWNER and m.provider is None  # asking changes nothing
    assert m.pending_handoff == {
        "to_provider": "endo-1", "requested_by": OWNER, "from_owner": OWNER,
        "requested_at": AT.isoformat(),
    }
    acc = step(m, make_event("handoff_accepted", "specialist_owner:dr_s", {}, at(1)), PATHWAY)
    assert acc.owner == "specialist_owner:dr_s" and acc.provider == "endo-1"
    assert acc.pending_handoff is None and acc.state == m.state
    assert acc.last_handoff == {"outcome": "accepted", "to_provider": "endo-1",
                                "by": "specialist_owner:dr_s", "at": at(1).isoformat()}
    # the acceptance may name the provider explicitly
    named = step(m, make_event("handoff_accepted", "specialist_owner:dr_s", {"provider": "endo-1b"}, at(1)), PATHWAY)
    assert named.provider == "endo-1b"


def test_nothing_to_answer_without_a_request_and_the_requester_cannot_accept():
    m = drive(*TO_CONTACTED, ("patient_acknowledge", "patient:p", {}))
    for kind, payload in [("handoff_accepted", {}), ("handoff_rejected", {"reason": "capacity"})]:
        with pytest.raises(InvalidTransition, match="no pending handoff"):
            step(m, make_event(kind, "specialist_owner:s", payload), PATHWAY)
    pending = requested()
    with pytest.raises(InvalidTransition, match="requested it"):
        step(pending, make_event("handoff_accepted", OWNER, {}), PATHWAY)
    with pytest.raises(InvalidTransition, match="requested it"):  # nor the owner it came from
        step(pending, make_event("handoff_accepted", "primary_care_owner:dr_k", {}), PATHWAY)
    with pytest.raises(InvalidTransition):  # automation never accepts
        step(pending, make_event("handoff_accepted", "system:scheduler", {}), PATHWAY)


@pytest.mark.parametrize("reason", REASONS)
def test_each_taxonomy_reason_rejects_and_the_case_stays_with_the_referrer(reason):
    m = requested()
    rej = step(m, make_event("handoff_rejected", "specialist_owner:s", {"reason": reason}, at(1)), PATHWAY)
    assert rej.state == "referral_rejected" and rej.owner == OWNER and rej.pending_handoff is None
    assert rej.last_handoff["reason"] == reason and rej.last_handoff["bounced"] is False


@pytest.mark.parametrize(
    "payload,actor",
    [
        ({}, "specialist_owner:s"),
        ({"reason": ""}, "specialist_owner:s"),
        ({"reason": "busy"}, "specialist_owner:s"),
        ({"reason": "Capacity"}, "specialist_owner:s"),
        ({"reason": "timeout"}, "specialist_owner:s"),  # reserved for the scheduler
        ({"reason": "timeout", "bounced": True}, "specialist_owner:s"),
        ({"reason": "timeout", "bounced": True}, "supervisor:su"),
        ({"reason": "timeout"}, SCHEDULER_ACTOR),  # not flagged as a bounce
        ({"reason": "capacity", "bounced": True}, SCHEDULER_ACTOR),  # the scheduler only bounces
        ({"reason": ["capacity"]}, "specialist_owner:s"),
    ],
)
def test_bad_rejections_are_payload_errors(payload, actor):
    with pytest.raises(PayloadError):
        step(requested(), make_event("handoff_rejected", actor, payload), PATHWAY)


def test_the_scheduler_bounce_is_the_one_automated_rejection():
    bounce = make_event("handoff_rejected", SCHEDULER_ACTOR, {"reason": "timeout", "bounced": True}, at(5))
    assert is_automated(SCHEDULER_ACTOR)
    rej = step(requested(), bounce, PATHWAY)
    assert rej.state == "referral_rejected" and rej.owner == OWNER
    assert rej.last_handoff["bounced"] is True and rej.last_handoff["reason"] == "timeout"


def test_a_rejection_outside_the_referral_states_clears_the_request_without_a_state_change():
    m = step(drive(*TO_CONTACTED), make_event("handoff_requested", OWNER, {"to_provider": "endo-1"}), PATHWAY)
    assert m.state == "patient_contacted"
    rej = step(m, make_event("handoff_rejected", "specialist_owner:s", {"reason": "wrong_service"}), PATHWAY)
    assert rej.state == "patient_contacted" and rej.pending_handoff is None and rej.owner == OWNER


def test_reassign_clears_a_pending_request_and_names_the_provider():
    rej = step(requested(), make_event("handoff_rejected", "specialist_owner:s", {"reason": "capacity"}), PATHWAY)
    again = step(rej, make_event("handoff_requested", OWNER, {"to_provider": "endo-2"}), PATHWAY)
    out = step(again, make_event("reassign", "supervisor:su", {"owner": "specialist_owner:z", "provider": "endo-3"}), PATHWAY)
    assert out.owner == "specialist_owner:z" and out.provider == "endo-3" and out.pending_handoff is None
    assert out.state == "owner_accepted"


def test_the_read_model_json_carries_owner_provider_and_the_pending_request():
    js = requested("endo-1").to_json()
    assert js["owner"] == OWNER and js["provider"] is None
    assert js["pending_handoff"]["to_provider"] == "endo-1" and js["last_handoff"] is None


# ---- the planner ---------------------------------------------------------------------------------


def test_the_bounce_is_due_after_the_configured_working_days():
    m = requested(on_day=0)  # Monday 08:00
    assert handoff_due_at(m, PATHWAY, UAE) == at(3)  # Thursday 08:00
    assert plan_bounce(uuid4(), m, PATHWAY, UAE, at(3)) is None  # exactly due: not yet late
    ev = plan_bounce(uuid4(), m, PATHWAY, UAE, at(3, 1))
    assert ev.type == "handoff_rejected" and ev.actor == SCHEDULER_ACTOR
    assert ev.payload["reason"] == "timeout" and ev.payload["bounced"] is True
    assert ev.payload["returned_to"] == OWNER and ev.payload["to_provider"] == "endo-1"
    friday = requested(on_day=4)  # the weekend does not count
    assert handoff_due_at(friday, PATHWAY, UAE) == at(9)  # Wednesday 14 Jan


def test_the_bounce_comes_alone_and_has_a_deterministic_id():
    m = requested()
    cid = uuid4()
    far = at(60)  # also long past the state's own SLA
    planned = plan_case(cid, m, PATHWAY, UAE, far)
    assert [e.type for e in planned] == ["handoff_rejected"]  # overdue/escalation re-planned after it
    assert planned[0].event_id == plan_case(cid, m, PATHWAY, UAE, far + timedelta(days=1))[0].event_id
    rejected = step(m, planned[0], PATHWAY)  # the bounce starts the re-route clock at `far`
    assert plan_case(cid, rejected, PATHWAY, UAE, far) == []
    assert plan_case(cid, rejected, PATHWAY, UAE, far + timedelta(days=10))[0].type == "mark_overdue"


# ---- the sweep (in memory) -------------------------------------------------------------------------


def make_store_case(*steps):
    store = InMemoryEventStore()
    cid = uuid4()
    store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, AT, "system:intake", {"patient_ref": "Patient/1"}),
                      PATHWAY.initial_state)
    for i, (type_, actor, payload) in enumerate(steps, start=1):
        store.append(Event(cid, type_, at(0, i), actor, payload), PATHWAY)
    return store, cid


REQUESTED = [
    ("acknowledge", "lab:l", {}),
    ("accept_ownership", OWNER, {}),
    ("contact_patient", OWNER, {}),
    ("patient_acknowledge", "patient:p", {}),
    ("handoff_requested", OWNER, {"to_provider": "endo-1"}),
]


def bounces(store, cid):
    return [e for e in store.events_for(cid) if e.type == "handoff_rejected"]


def test_an_unanswered_request_bounces_once_and_repeat_sweeps_add_nothing():
    store, cid = make_store_case(*REQUESTED)  # requested Monday 5 Jan 13:00; due Thursday 13:00
    assert run_sweep(store, PATHWAY, UAE, at(3, 5)).handoff_bounced == 0  # exactly due: not late yet
    assert store.read_model(cid, PATHWAY).pending_handoff is not None
    res = run_sweep(store, PATHWAY, UAE, at(3, 6))
    assert res.handoff_bounced == 1 and res.errors == []
    [b] = bounces(store, cid)
    assert b.actor == SCHEDULER_ACTOR and b.payload["bounced"] is True and b.payload["reason"] == "timeout"
    m = store.read_model(cid, PATHWAY)
    assert m.state == "referral_rejected" and m.owner == OWNER and m.pending_handoff is None
    for k in range(1, 4):
        assert run_sweep(store, PATHWAY, UAE, at(3, 6 + k)).handoff_bounced == 0
    assert len(bounces(store, cid)) == 1


def test_a_bounced_case_needs_a_reroute_and_escalates_when_that_is_late():
    store, cid = make_store_case(*REQUESTED)
    run_sweep(store, PATHWAY, UAE, at(3, 6))  # bounce at Thu 14:00; re-route due Mon 12 Jan 14:00
    m = store.read_model(cid, PATHWAY)
    assert m.state == "referral_rejected" and not m.overdue
    res = run_sweep(store, PATHWAY, UAE, at(7, 7))
    assert (res.mark_overdue, res.escalate) == (1, 1)  # overdue, reminder to the owner
    res = run_sweep(store, PATHWAY, UAE, at(10, 7))  # supervisor rung 3 working days after the breach
    assert res.escalate == 1
    assert store.read_model(cid, PATHWAY).escalation_level == 2
    assert OWNER == store.read_model(cid, PATHWAY).owner  # nobody took it: still the referrer's


def test_an_answered_request_is_not_bounced():
    store, cid = make_store_case(*REQUESTED)
    store.append(Event(cid, "handoff_accepted", at(1), "specialist_owner:s"), PATHWAY)
    assert run_sweep(store, PATHWAY, UAE, at(30)).handoff_bounced == 0
    assert bounces(store, cid) == []


def test_a_bounce_outside_the_referral_states_leaves_the_state_alone():
    store, cid = make_store_case(
        ("acknowledge", "lab:l", {}), ("accept_ownership", OWNER, {}),
        ("handoff_requested", OWNER, {"to_provider": "endo-1"}),
    )
    run_sweep(store, PATHWAY, UAE, at(3, 6))
    m = store.read_model(cid, PATHWAY)
    assert len(bounces(store, cid)) == 1 and m.state == "owner_accepted" and m.pending_handoff is None
    assert m.last_handoff["bounced"] is True


# ---- over HTTP: T-A20 / T-A21, scoping ---------------------------------------------------------------

client = make_client()
LAB = login_headers(client, "lab", "lab one")
COORD = login_headers(client, "coordinator", "bea")
SUP = login_headers(client, "supervisor", "sam")
PATIENT = login_headers(client, "patient", "pat", "Patient/1")
PCO_A = login_headers(client, "primary_care_owner", "dr a")
PCO_B = login_headers(client, "primary_care_owner", "dr b")
SPEC_1 = {"Authorization": "Bearer " + client.post(
    "/auth/dev-login", json={"role": "specialist_owner", "name": "dr s1", "provider": "endo-1"}).json()["token"]}
SPEC_2 = login_headers(client, "specialist_owner", "dr s2")  # provider defaults to the name
SCHED = {"Authorization": f"Bearer {app_settings.scheduler_service_token}"}


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch):
    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    clock.reset()
    yield
    clock.reset()


def post(case_id, type_, headers, payload=None):
    return client.post(f"/cases/{case_id}/events", json={"type": type_, "payload": payload or {}}, headers=headers)


def case_at(stage):
    """A case driven over HTTP: 'acknowledged', 'owned' (owner dr a), 'acked' (patient acknowledged)."""
    cid = client.post("/cases", json={"patient_ref": "Patient/1"}, headers=COORD).json()["case_id"]
    plan = [("acknowledge", LAB, "acknowledged"), ("accept_ownership", PCO_A, "owned"),
            ("contact_patient", PCO_A, None), ("patient_acknowledge", PATIENT, "acked")]
    for type_, headers, label in plan:
        assert post(cid, type_, headers).status_code == 201, type_
        if label == stage:
            break
    return cid


def model(cid):
    return client.get(f"/cases/{cid}", headers=COORD).json()["read_model"]


def test_t_a20_handoff_accept_over_http():
    cid = case_at("acked")
    assert model(cid)["owner"] == actor_of("primary_care_owner", "dr a")
    r = post(cid, "handoff_requested", PCO_A, {"to_provider": "endo-1"})
    assert r.status_code == 201
    assert model(cid)["owner"] == actor_of("primary_care_owner", "dr a")  # still A while pending
    assert model(cid)["pending_handoff"]["to_provider"] == "endo-1"
    # not the provider it was sent to, not the owner: refused, nothing written
    before = len(client.get(f"/cases/{cid}", headers=COORD).json()["events"])
    assert post(cid, "handoff_accepted", SPEC_2).status_code == 403
    assert post(cid, "handoff_accepted", PCO_B).status_code == 403
    assert post(cid, "handoff_accepted", PCO_A).status_code == 409  # the owner, but not the receiver
    assert len(client.get(f"/cases/{cid}", headers=COORD).json()["events"]) == before
    r = post(cid, "handoff_accepted", SPEC_1)
    assert r.status_code == 201
    rm = r.json()["read_model"]
    assert rm["owner"] == actor_of("specialist_owner", "dr s1") and rm["provider"] == "endo-1"
    assert rm["pending_handoff"] is None and rm["state"] == "patient_acknowledged"
    events = r.json()["events"]
    assert [(e["type"], e["actor"]) for e in events[-2:]] == [
        ("handoff_requested", actor_of("primary_care_owner", "dr a")), ("handoff_accepted", actor_of("specialist_owner", "dr s1"))]
    # the new owner can carry on; the old one is now out of scope
    assert post(cid, "book_confirmatory", PCO_A).status_code == 403
    assert post(cid, "book_confirmatory", SPEC_1, {"appointment_ref": "x"}).status_code == 201


@pytest.mark.parametrize("reason", REASONS)
def test_t_a21_each_reason_is_accepted(reason):
    cid = case_at("acked")
    assert post(cid, "handoff_requested", PCO_A, {"to_provider": "endo-1"}).status_code == 201
    r = post(cid, "handoff_rejected", SPEC_1, {"reason": reason})
    assert r.status_code == 201
    assert r.json()["state"] == "referral_rejected"
    assert r.json()["read_model"]["owner"] == actor_of("primary_care_owner", "dr a")  # ownership stays
    assert r.json()["events"][-1]["payload"]["reason"] == reason


@pytest.mark.parametrize("payload", [{}, {"reason": "busy"}, {"reason": ""}, {"reason": "timeout"},
                                     {"reason": "timeout", "bounced": True}, {"reason": None}])
def test_t_a21_any_other_reason_is_422_and_writes_nothing(payload):
    cid = case_at("acked")
    post(cid, "handoff_requested", PCO_A, {"to_provider": "endo-1"})
    before = len(client.get(f"/cases/{cid}", headers=COORD).json()["events"])
    assert post(cid, "handoff_rejected", SPEC_1, payload).status_code == 422
    assert len(client.get(f"/cases/{cid}", headers=COORD).json()["events"]) == before


def test_t_a21_a_rejected_referral_can_be_rerouted():
    cid = case_at("acked")
    post(cid, "handoff_requested", PCO_A, {"to_provider": "endo-1"})
    post(cid, "handoff_rejected", SPEC_1, {"reason": "capacity"})
    assert post(cid, "handoff_requested", PCO_A, {"to_provider": "dr s2"}).status_code == 201
    r = post(cid, "handoff_accepted", SPEC_2)
    assert r.status_code == 201 and r.json()["state"] == "owner_accepted"
    assert r.json()["read_model"]["owner"] == actor_of("specialist_owner", "dr s2")
    # or a supervisor reassigns
    cid = case_at("acked")
    post(cid, "handoff_requested", PCO_A, {"to_provider": "endo-1"})
    post(cid, "handoff_rejected", SPEC_1, {"reason": "capacity"})
    r = post(cid, "reassign", SUP, {"owner": actor_of("primary_care_owner", "dr b")})
    assert r.status_code == 201 and r.json()["read_model"]["owner"] == actor_of("primary_care_owner", "dr b")


def test_a_bad_payload_is_a_422_and_a_bad_transition_a_409():
    cid = case_at("acked")
    assert post(cid, "handoff_requested", PCO_A, {}).status_code == 422
    assert post(cid, "handoff_requested", PCO_A, {"to_provider": "endo-1"}).status_code == 201
    assert post(cid, "handoff_requested", PCO_A, {"to_provider": "endo-2"}).status_code == 409


def test_owner_scoping_clinicians_act_only_on_their_own_cases():
    cid = case_at("acknowledged")
    # an unowned case: anyone in an owner role may accept it, nothing else
    assert post(cid, "contact_patient", PCO_B).status_code == 403
    assert post(cid, "accept_ownership", PCO_A).status_code == 201
    # now owned by dr a
    assert post(cid, "contact_patient", PCO_B).status_code == 403
    assert post(cid, "handoff_requested", PCO_B, {"to_provider": "x"}).status_code == 403
    assert post(cid, "escalation_accepted", PCO_B).status_code == 403
    assert post(cid, "accept_ownership", PCO_B).status_code == 403
    assert post(cid, "contact_patient", PCO_A).status_code == 201
    # supervisors and coordinators are not owner-scoped; neither are patients (own case only) or lab
    assert post(cid, "mark_lost_contact", COORD, {"failed_attempts": 3}).status_code == 201
    assert post(cid, "contact_patient", SUP).status_code == 201
    assert post(cid, "handoff_requested", SUP, {"to_provider": "endo-1"}).status_code == 201


def test_the_scheduler_may_post_the_bounce_and_nothing_else_rejecting():
    cid = case_at("acked")
    post(cid, "handoff_requested", PCO_A, {"to_provider": "endo-1"})
    assert post(cid, "handoff_rejected", SCHED, {"reason": "capacity"}).status_code == 422
    assert post(cid, "handoff_rejected", SCHED, {"reason": "timeout"}).status_code == 422
    r = post(cid, "handoff_rejected", SCHED, {"reason": "timeout", "bounced": True})
    assert r.status_code == 201 and r.json()["events"][-1]["actor"] == "system:scheduler"


def test_the_sweep_route_bounces_an_unanswered_request():
    cid = case_at("acked")
    post(cid, "handoff_requested", PCO_A, {"to_provider": "endo-1"})
    client.post("/clock/advance", json={"days": 4}, headers=SUP)
    out = client.post("/scheduler/sweep", headers=SCHED).json()
    assert out["handoff_bounced"] == 1 and out["errors"] == []
    case = client.get(f"/cases/{cid}", headers=COORD).json()
    assert case["state"] == "referral_rejected"
    [b] = [e for e in case["events"] if e["type"] == "handoff_rejected"]
    assert b["actor"] == "system:scheduler" and b["payload"]["reason"] == "timeout"
    assert client.post("/scheduler/sweep", headers=SCHED).json()["handoff_bounced"] == 0


def test_dev_login_carries_a_provider_claim():
    me = client.get("/me", headers=SPEC_1).json()
    assert me["provider"] == "endo-1" and me["role"] == "specialist_owner"
    assert "provider" not in client.get("/me", headers=SPEC_2).json()
    for role in ("coordinator", "patient", "lab"):
        r = client.post("/auth/dev-login", json={"role": role, "name": "x", "provider": "endo"})
        assert r.status_code == 422
