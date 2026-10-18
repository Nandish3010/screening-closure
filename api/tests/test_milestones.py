"""A18, ADR-028: the milestones (referral within 15 working days of the result, colonoscopy within 31 working days
of referral) in the pathway, the state machine, the sweep, the read model, the timeline and the routes.

Calendar used throughout (UAE, Mon-Fri): a case created Monday 5 Jan 2026 08:00Z has its referral milestone due
15 working days later, Monday 26 Jan 08:00Z. "Day 14" and "day 16" below are the 14th and 16th working day after
the result, Friday 23 Jan and Tuesday 27 Jan.
"""

import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from api_helpers import login_headers, make_client
from app import clock as clock_module
from app import payloads, readmodel
from app.calendar import WorkingCalendar
from app.config import settings as app_settings
from app.events import CASE_CREATED, Event, InMemoryEventStore, replay_model
from app.pathway import PathwayError, load_pathway
from app.routers import cases
from app.state_machine import InvalidTransition, MilestoneState, step
from app.sweep import (
    SCHEDULER_ACTOR,
    SWEEP_NAMESPACE,
    milestone_due_at,
    plan_case,
    plan_milestones,
    run_sweep,
    sweep_event_id,
)

PATHWAY = load_pathway(app_settings.pathway_path)
UAE = WorkingCalendar()
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # Monday
PCO = "primary_care_owner:dr_k"
SPEC = "specialist_owner:dr_s"
LAB = "lab:l"
REFERRAL = "referral_requested"
COLONOSCOPY = "confirmatory_completed"


def wd(n, hours=0):
    """The n-th working day after the result (08:00Z), plus hours."""
    return UAE.add_working_days(AT, n) + timedelta(hours=hours)


def new_case():
    store = InMemoryEventStore()
    cid = uuid4()
    store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, AT, "system:intake"), PATHWAY.initial_state)
    return store, cid


def post(store, cid, type_, actor, when, payload=None):
    store.append(Event(cid, type_, when, actor, payload or {}), PATHWAY)


def to_patient_acknowledged(store, cid):
    """Days 1-4 (Tue 6 to Fri 9 Jan): the pre-referral steps, each within its tightened SLA."""
    post(store, cid, "acknowledge", LAB, AT + timedelta(days=1))
    post(store, cid, "accept_ownership", PCO, AT + timedelta(days=2))
    post(store, cid, "contact_patient", PCO, AT + timedelta(days=3))
    post(store, cid, "patient_acknowledge", "patient:p", AT + timedelta(days=4))


def sweep(store, cid, now):
    return run_sweep(store, PATHWAY, UAE, now, only=[cid])


def of(store, cid, type_, milestone=REFERRAL):
    return [e for e in store.events_for(cid) if e.type == type_ and e.payload.get("milestone") == milestone]


def ms_rungs(store, cid, milestone=REFERRAL):
    """The rungs of a milestone's ladder in the log, in order: its own `escalate` events and, since A19, the entries
    other ladders' escalations carry for it in `also_for` (a rung to the same role in the same sweep rides on one)."""
    out = []
    for e in store.events_for(cid):
        if e.type != "escalate":
            continue
        if e.payload.get("milestone") == milestone:
            out.append(e.payload["rung"])
        out += [x["rung"] for x in e.payload.get("also_for", ()) if x.get("milestone") == milestone]
    return out


def model(store, cid):
    return store.read_model(cid, PATHWAY)


# ---- the pathway ------------------------------------------------------------------------------------


def test_the_yaml_declares_both_milestones_with_their_sources():
    ref, col = PATHWAY.milestones[REFERRAL], PATHWAY.milestones[COLONOSCOPY]
    assert (ref.from_event, ref.until_event, ref.working_days) == ("case_created", "handoff_requested", 15)
    assert (col.from_event, col.until_event, col.working_days) == ("handoff_requested", "complete_confirmatory", 31)
    assert "IFHAS" in ref.source and "evidence.md section 8" in ref.source
    # the 31 comes from the National Guideline's KPI table (Appendix 2), not the IFHAS standard
    assert "National Guideline Appendix 2" in col.source and "IFHAS" not in col.source
    assert list(PATHWAY.milestones) == [REFERRAL, COLONOSCOPY]


def test_the_pre_referral_state_slas_fit_inside_the_referral_milestone():
    slas = PATHWAY.slas_working_days
    pre = [slas[e] for e in ("acknowledge", "accept_ownership", "contact_patient", "patient_acknowledge")]
    assert pre == [2, 1, 3, 5] and sum(pre) == 11 <= PATHWAY.milestones[REFERRAL].working_days
    assert {k: v for k, v in slas.items() if k not in ("acknowledge", "accept_ownership", "contact_patient",
                                                       "patient_acknowledge")} == {
        "book_confirmatory": 5, "clinician_review": 5, "close": 3, "reassign": 2}
    assert "complete_confirmatory" not in slas  # the colonoscopy clock is the milestone


def write_variant(tmp_path, old, new):
    raw = Path(app_settings.pathway_path).read_text()
    assert old in raw
    p = tmp_path / "variant.yaml"
    p.write_text(raw.replace(old, new))
    return p


@pytest.mark.parametrize(
    "old,new,match",
    [
        ("until_event: handoff_requested", "until_event: no_such_event", "until_event"),
        ("from_event: handoff_requested", "from_event: no_such_event", "from_event"),
        ("working_days: 15", "working_days: 0", "working_days"),
        ("working_days: 15", "working_days: fifteen", "working_days"),
        ("source: \"DoH IFHAS standard July 2025 via docs/evidence.md section 8\"", "source: \"\"", "source"),
        ("until_event: handoff_requested", "until_event: case_created", "differ|until_event"),
    ],
)
def test_the_loader_refuses_a_bad_milestone(tmp_path, old, new, match):
    with pytest.raises(PathwayError, match=match):
        load_pathway(write_variant(tmp_path, old, new))


def test_the_loader_refuses_an_unknown_milestone_key(tmp_path):
    with pytest.raises(PathwayError, match="unknown keys"):
        load_pathway(write_variant(tmp_path, "    working_days: 15\n", "    working_days: 15\n    colour: red\n"))


# ---- the sweep: the referral milestone ---------------------------------------------------------------


def test_referral_requested_on_day_14_is_no_breach():
    store, cid = new_case()
    to_patient_acknowledged(store, cid)
    assert milestone_due_at(model(store, cid), REFERRAL, PATHWAY, UAE) == wd(15) == datetime(2026, 1, 26, 8, tzinfo=UTC)
    sweep(store, cid, wd(14, 1))
    assert of(store, cid, "mark_overdue") == []
    post(store, cid, "handoff_requested", PCO, wd(14, 2), {"to_provider": "endo-1"})
    m = model(store, cid)
    assert m.milestones[REFERRAL].met_at == wd(14, 2) and not m.milestones[REFERRAL].overdue
    for later in (wd(16), wd(40)):  # however late the sweeps run
        sweep(store, cid, later)
    assert of(store, cid, "mark_overdue") == [] and of(store, cid, "escalate") == []


def test_referral_requested_on_day_16_is_one_breach_and_one_reminder():
    store, cid = new_case()
    to_patient_acknowledged(store, cid)
    sweep(store, cid, wd(15))  # exactly due: not late
    assert of(store, cid, "mark_overdue") == []
    for _ in range(4):  # however often the sweep runs
        sweep(store, cid, wd(16, 1))
    [breach] = of(store, cid, "mark_overdue")
    [reminder] = of(store, cid, "escalate")
    assert breach.actor == reminder.actor == SCHEDULER_ACTOR
    assert breach.payload == {"milestone": REFERRAL, "due_at": wd(15).isoformat(), "sla_working_days": 15}
    assert reminder.payload["rung"] == "reminder" and reminder.payload["level"] == 1
    assert reminder.payload["milestone"] == REFERRAL and "state" not in reminder.payload
    assert reminder.payload["due_at"] == wd(15).isoformat()
    m = model(store, cid)
    ms = m.milestones[REFERRAL]
    assert ms.overdue and ms.breach_due_at == wd(15) and ms.escalation_level == 1 and ms.met_at is None
    # the referral is requested late: the milestone is met and its breach and ladder clear (the log keeps both events)
    post(store, cid, "handoff_requested", PCO, wd(16, 2), {"to_provider": "endo-1"})
    ms = model(store, cid).milestones[REFERRAL]
    assert not ms.overdue and ms.met_at == wd(16, 2) and ms.escalation_level == 0 and not ms.escalation_accepted
    assert len(of(store, cid, "mark_overdue")) == 1 and len(of(store, cid, "escalate")) == 1


def test_handoff_requested_after_the_breach_clears_overdue_for_that_milestone_only():
    store, cid = new_case()
    to_patient_acknowledged(store, cid)
    sweep(store, cid, wd(16, 1))
    m = model(store, cid)
    assert m.milestones[REFERRAL].overdue and m.overdue  # patient_acknowledged (book_confirmatory, 5) also breached
    assert m.any_overdue and m.milestone_overdue
    post(store, cid, "handoff_requested", PCO, wd(16, 2), {"to_provider": "endo-1"})
    m = model(store, cid)
    assert not m.milestones[REFERRAL].overdue and not m.milestone_overdue
    assert m.overdue and m.any_overdue  # the state-level breach is a different obligation and stays
    # and a later sweep does not plan the milestone again
    assert [e for e in plan_case(cid, m, PATHWAY, UAE, wd(60)) if e.payload.get("milestone") == REFERRAL] == []


def test_the_milestone_ladder_runs_like_a_state_breach_and_is_independent_of_it():
    store, cid = new_case()
    to_patient_acknowledged(store, cid)
    due = wd(15)
    expected = [("reminder", due), ("supervisor", UAE.add_working_days(due, 3)), ("coordinator", UAE.add_working_days(due, 7))]
    for n, (rung, rung_due) in enumerate(expected):
        sweep(store, cid, rung_due - timedelta(minutes=1))
        assert len(of(store, cid, "escalate")) == n  # a minute short of the rung's working-day offset
        sweep(store, cid, rung_due + timedelta(minutes=1))
        assert [e.payload["rung"] for e in of(store, cid, "escalate")][-1] == rung and len(of(store, cid, "escalate")) == n + 1
    assert [e.payload["level"] for e in of(store, cid, "escalate")] == [1, 2, 3]
    sweep(store, cid, wd(80))
    assert len(of(store, cid, "escalate")) == 3 and len(of(store, cid, "mark_overdue")) == 1  # nothing after the last rung
    m = model(store, cid)
    assert m.milestones[REFERRAL].escalation_level == 3
    # the state's own ladder (patient_acknowledged -> book_confirmatory) ran separately, to its own level
    state_rungs = [e for e in store.events_for(cid) if e.type == "escalate" and "state" in e.payload]
    assert [e.payload["state"] for e in state_rungs] == ["patient_acknowledged"] * 3 and m.escalation_level == 3


def test_a_human_accepting_a_milestone_rung_stops_that_ladder_only():
    store, cid = new_case()
    to_patient_acknowledged(store, cid)
    sweep(store, cid, UAE.add_working_days(wd(15), 3) + timedelta(minutes=1))  # reminder and supervisor rungs
    # the state ladder has reached the coordinator (its breach is older); accept the milestone's rung by naming it
    post(store, cid, "escalation_accepted", "supervisor:sam", wd(19, 2), {"milestone": REFERRAL})
    m = model(store, cid)
    assert m.milestones[REFERRAL].escalation_accepted and m.milestones[REFERRAL].escalation_level == 2
    assert not m.escalation_accepted
    sweep(store, cid, wd(80))
    assert ms_rungs(store, cid) == ["reminder", "supervisor"]  # no coordinator rung
    with pytest.raises(InvalidTransition):
        post(store, cid, "escalation_accepted", "supervisor:sam2", wd(80), {"milestone": REFERRAL})  # not twice


def test_acceptance_without_a_milestone_finds_the_one_ladder_that_has_a_rung():
    store, cid = new_case()
    post(store, cid, "acknowledge", LAB, AT + timedelta(days=1))
    post(store, cid, "accept_ownership", PCO, AT + timedelta(days=2))
    # case then sits in owner_accepted; the state breach and the milestone breach both happen
    sweep(store, cid, wd(15, 1))
    m = model(store, cid)
    assert m.escalation_level >= 1 and m.milestones[REFERRAL].escalation_level == 1
    post(store, cid, "escalation_accepted", "coordinator:co", wd(15, 2))  # the state's ladder first (existing behaviour)
    assert model(store, cid).escalation_accepted and not model(store, cid).milestones[REFERRAL].escalation_accepted
    # a client that does not name the milestone, once the state ladder is empty, reaches the single milestone ladder
    post(store, cid, "contact_patient", PCO, wd(15, 3))  # new state: state ladder cleared
    assert model(store, cid).escalation_level == 0
    post(store, cid, "escalation_accepted", PCO, wd(15, 4))
    assert model(store, cid).milestones[REFERRAL].escalation_accepted


def test_milestone_event_ids_derive_from_the_event_that_started_the_milestone():
    """SEC-2 + A18: never a timestamp. referral_requested runs from the case_created marker, confirmatory_completed
    from the first handoff_requested (A25); a milestone started by the same event as a state clock never shares its ids."""
    case, start = uuid4(), uuid4()
    got = sweep_event_id(case, start, f"milestone:{REFERRAL}:mark_overdue")
    assert got == sweep_event_id(case, str(start), f"milestone:{REFERRAL}:mark_overdue")
    assert got != sweep_event_id(case, uuid4(), f"milestone:{REFERRAL}:mark_overdue")  # a restart, even at the same instant
    assert got != sweep_event_id(case, start, f"milestone:{COLONOSCOPY}:mark_overdue")
    assert got != sweep_event_id(uuid4(), start, f"milestone:{REFERRAL}:mark_overdue")
    assert got != sweep_event_id(case, start, "mark_overdue")  # the state breach of a clock started by the same event
    assert got != sweep_event_id(case, start, f"milestone:{REFERRAL}:escalate:reminder")

    store, cid = new_case()
    created = store.events_for(cid)[0]
    assert created.type == CASE_CREATED and model(store, cid).milestones[REFERRAL].started_event_id == created.event_id
    planned = plan_milestones(cid, model(store, cid), PATHWAY, UAE, wd(16))
    assert [e.event_id for e in planned] == [
        sweep_event_id(cid, created.event_id, f"milestone:{REFERRAL}:mark_overdue"),
        sweep_event_id(cid, created.event_id, f"milestone:{REFERRAL}:escalate:reminder"),
    ]
    # the state's own breach (accept_ownership never happened: the first state's clock is also case_created's)
    state_ids = {e.event_id for e in plan_case(cid, model(store, cid), PATHWAY, UAE, wd(16)) if "state" in e.payload}
    assert state_ids and not state_ids & {e.event_id for e in planned}

    store, cid = new_case()
    referred_and_booked(store, cid)
    [requested] = [e for e in store.events_for(cid) if e.type == "handoff_requested"]
    ms = model(store, cid).milestones[COLONOSCOPY]
    assert ms.started_event_id == requested.event_id
    due = UAE.add_working_days(ms.started_at, 31)
    [breach, *_] = plan_milestones(cid, model(store, cid), PATHWAY, UAE, due + timedelta(hours=1))
    assert breach.event_id == sweep_event_id(cid, requested.event_id, f"milestone:{COLONOSCOPY}:mark_overdue")


def test_a_milestone_restarted_at_the_same_instant_is_a_new_breach():
    """Two clocks that start at the same `at` from different events plan different ids, so the second breach is not
    swallowed as a repeat of the first (the timestamp-keyed ids of A18 did exactly that)."""
    store, cid = new_case()
    m = model(store, cid)
    ms = m.milestones[REFERRAL]
    restarted = dataclasses.replace(m, milestones={**m.milestones, REFERRAL: dataclasses.replace(ms, started_event_id=uuid4())})
    one = plan_milestones(cid, m, PATHWAY, UAE, wd(16))
    two = plan_milestones(cid, restarted, PATHWAY, UAE, wd(16))
    assert [e.payload["due_at"] for e in one] == [e.payload["due_at"] for e in two]  # same instant, same due date
    assert not {e.event_id for e in one} & {e.event_id for e in two}


def test_two_sweeps_planning_the_same_breach_insert_it_once():
    """The planner is pure and the ids deterministic, so a second writer's identical event is a no-op."""
    store, cid = new_case()
    stale = model(store, cid)
    plan_a = plan_case(cid, stale, PATHWAY, UAE, wd(16, 1))
    plan_b = plan_case(cid, stale, PATHWAY, UAE, wd(16, 2))
    # the milestone's breach is its own event; its reminder rides on the state's reminder (A19, also_for)
    ms_a = [e for e in plan_a if e.payload.get("milestone")]
    ms_b = [e for e in plan_b if e.payload.get("milestone")]
    assert [e.event_id for e in ms_a] == [e.event_id for e in ms_b] and len(ms_a) == 1
    assert [e.event_id for e in plan_a] == [e.event_id for e in plan_b] and len(plan_a) == 5  # two breaches, the state's three rungs (the milestone's reminder rides on the first)
    for e in (*plan_a, *plan_b):
        store.append(e, PATHWAY)  # the second copy of each id is a no-op, not an error
    assert len(of(store, cid, "mark_overdue")) == 1 and ms_rungs(store, cid) == ["reminder"]
    assert len([e for e in store.events_for(cid) if e.type == "escalate"]) == 3


# ---- the colonoscopy milestone ----------------------------------------------------------------------


REQUESTED_AT = AT + timedelta(days=5)  # the referral request in `referred_and_booked`: where the 31 working days start (A25)


def referred_and_booked(store, cid, accepted=None, booked=None):
    to_patient_acknowledged(store, cid)
    accepted = accepted or AT + timedelta(days=6)
    post(store, cid, "handoff_requested", PCO, REQUESTED_AT, {"to_provider": "endo-1"})
    post(store, cid, "handoff_accepted", SPEC, accepted)
    post(store, cid, "book_confirmatory", SPEC, booked or accepted + timedelta(days=1))
    return accepted


def test_the_colonoscopy_milestone_breaches_31_working_days_after_the_first_referral_request_and_is_met_by_the_procedure():
    store, cid = new_case()
    referred_and_booked(store, cid)
    due = UAE.add_working_days(REQUESTED_AT, 31)
    assert model(store, cid).milestones[COLONOSCOPY].started_at == REQUESTED_AT
    sweep(store, cid, due)
    assert of(store, cid, "mark_overdue", COLONOSCOPY) == []
    sweep(store, cid, due + timedelta(hours=1))
    [breach] = of(store, cid, "mark_overdue", COLONOSCOPY)
    assert breach.payload["due_at"] == due.isoformat() and breach.payload["sla_working_days"] == 31
    assert len(of(store, cid, "escalate", COLONOSCOPY)) == 1
    assert model(store, cid).milestones[COLONOSCOPY].overdue and not model(store, cid).milestones[REFERRAL].overdue
    # the procedure happens late: the milestone is met and cleared; the case moves on
    post(store, cid, "complete_confirmatory", SPEC, due + timedelta(days=2), {})
    ms = model(store, cid).milestones[COLONOSCOPY]
    assert not ms.overdue and ms.met_at == due + timedelta(days=2) and ms.escalation_level == 0
    sweep(store, cid, due + timedelta(days=60))
    assert len(of(store, cid, "mark_overdue", COLONOSCOPY)) == 1


def test_a_met_milestone_never_changes_again():
    store, cid = new_case()
    accepted = referred_and_booked(store, cid)
    post(store, cid, "complete_confirmatory", SPEC, accepted + timedelta(days=10))
    met = model(store, cid).milestones[COLONOSCOPY]
    # a later referral (a transfer, a re-route) does not restart a met colonoscopy clock (nor a running one, A25)
    post(store, cid, "handoff_requested", SPEC, accepted + timedelta(days=11), {"to_provider": "endo-2"})
    post(store, cid, "handoff_accepted", PCO, accepted + timedelta(days=12))
    assert model(store, cid).milestones[COLONOSCOPY] == met
    # nor does a second request undo the first referral's met_at
    first = model(store, cid).milestones[REFERRAL].met_at
    assert first == AT + timedelta(days=5)


def test_closing_a_case_that_never_met_a_milestone_voids_it():
    store, cid = new_case()
    to_patient_acknowledged(store, cid)
    post(store, cid, "book_confirmatory", PCO, AT + timedelta(days=5))
    post(store, cid, "complete_confirmatory", SPEC, AT + timedelta(days=6))
    post(store, cid, "clinician_review", PCO, AT + timedelta(days=7))
    post(store, cid, "close", PCO, AT + timedelta(days=8), {"evidence_ref": "DiagnosticReport/1", "signed_by": PCO})
    m = model(store, cid)
    assert m.state == "closed" and m.milestones[REFERRAL].void and m.milestones[REFERRAL].met_at is None
    assert not m.milestones[REFERRAL].overdue and m.milestones[COLONOSCOPY].met_at is not None
    assert plan_case(cid, m, PATHWAY, UAE, wd(100)) == []
    # reopening does not bring a voided referral clock back
    post(store, cid, "reopen", "coordinator:co", AT + timedelta(days=9), {"reason": "new_finding"})
    assert not [e for e in plan_case(cid, model(store, cid), PATHWAY, UAE, wd(100)) if e.payload.get("milestone")]


def test_no_milestone_event_is_planned_while_consent_is_withdrawn_and_the_breach_follows_on_resume():
    store, cid = new_case()
    to_patient_acknowledged(store, cid)
    post(store, cid, "consent_updated", "patient:p", AT + timedelta(days=5), {"withdrawn": True})
    assert model(store, cid).state == "consent_withdrawn"
    assert plan_milestones(cid, model(store, cid), PATHWAY, UAE, wd(40)) == []
    post(store, cid, "consent_updated", "patient:p", wd(40), {"withdrawn": False})
    planned = plan_milestones(cid, model(store, cid), PATHWAY, UAE, wd(40, 1))
    assert [e.type for e in planned][:1] == ["mark_overdue"] and planned[0].payload["milestone"] == REFERRAL


# ---- the state machine ------------------------------------------------------------------------------


def started():
    store, cid = new_case()
    to_patient_acknowledged(store, cid)
    return store, cid


@pytest.mark.parametrize(
    "event_payload,why",
    [
        ({"milestone": "no_such", "due_at": "2026-01-26T08:00:00+00:00"}, "unknown milestone"),
        ({"milestone": COLONOSCOPY, "due_at": "2026-01-26T08:00:00+00:00"}, "not started"),
        ({"milestone": REFERRAL, "due_at": "2026-02-26T08:00:00+00:00"}, "before the due date"),
        ({"milestone": REFERRAL, "due_at": "tomorrow"}, "bad due_at"),
    ],
)
def test_mark_overdue_for_a_milestone_is_refused_when_it_would_be_false(event_payload, why):
    store, cid = started()
    with pytest.raises(InvalidTransition):
        step(model(store, cid), Event(cid, "mark_overdue", wd(16), SCHEDULER_ACTOR, event_payload), PATHWAY)


def test_mark_overdue_for_a_milestone_twice_or_after_it_is_met_is_refused():
    store, cid = started()
    first = Event(cid, "mark_overdue", wd(16), SCHEDULER_ACTOR, {"milestone": REFERRAL, "due_at": wd(15).isoformat()})
    m = step(model(store, cid), first, PATHWAY)
    again = dataclasses.replace(first, event_id=uuid4())
    with pytest.raises(InvalidTransition, match="already overdue"):
        step(m, again, PATHWAY)
    met = step(m, Event(cid, "handoff_requested", wd(16, 1), PCO, {"to_provider": "endo-1"}), PATHWAY)
    with pytest.raises(InvalidTransition, match="already met"):
        step(met, dataclasses.replace(first, event_id=uuid4(), at=wd(17)), PATHWAY)


def test_an_escalate_for_a_milestone_needs_the_breach_and_follows_the_ladder_order():
    store, cid = started()
    rung = lambda name, **kw: Event(cid, "escalate", wd(16), SCHEDULER_ACTOR, {"rung": name, "milestone": REFERRAL, **kw})  # noqa: E731
    with pytest.raises(InvalidTransition, match="not overdue"):
        step(model(store, cid), rung("reminder"), PATHWAY)
    m = step(model(store, cid), Event(cid, "mark_overdue", wd(16), SCHEDULER_ACTOR,
                                      {"milestone": REFERRAL, "due_at": wd(15).isoformat()}), PATHWAY)
    with pytest.raises(InvalidTransition, match="never skipped"):
        step(m, rung("supervisor"), PATHWAY)
    m = step(m, rung("reminder", target="owner"), PATHWAY)
    assert m.milestones[REFERRAL].escalation_level == 1 and m.escalation_level == 0
    with pytest.raises(InvalidTransition, match="targets"):
        step(m, rung("supervisor", target="owner"), PATHWAY)


def test_payload_schema_needs_exactly_one_of_state_and_milestone():
    due = wd(15).isoformat()
    payloads.validate("mark_overdue", {"milestone": REFERRAL, "due_at": due})
    payloads.validate("mark_overdue", {"state": "acknowledged", "due_at": due})
    payloads.validate("escalate", {"rung": "reminder", "milestone": REFERRAL})
    payloads.validate("escalation_accepted", {"milestone": REFERRAL, "rung": "reminder"})
    for bad in ({"due_at": due}, {"state": "acknowledged", "milestone": REFERRAL, "due_at": due},
                {"milestone": "Referral Requested", "due_at": due}, {"milestone": REFERRAL}):
        with pytest.raises(payloads.PayloadSchemaError):
            payloads.validate("mark_overdue", bad)
    for bad in ({"rung": "reminder"}, {"rung": "reminder", "state": "acknowledged", "milestone": REFERRAL}):
        with pytest.raises(payloads.PayloadSchemaError):
            payloads.validate("escalate", bad)


def test_milestone_state_json_and_replay_agree_with_the_incremental_model():
    store, cid = new_case()
    accepted = referred_and_booked(store, cid)
    sweep(store, cid, UAE.add_working_days(accepted, 31) + timedelta(hours=1))
    kept = store.read_model(cid, PATHWAY)
    assert kept == replay_model(store.events_for(cid), PATHWAY)
    assert kept.to_json()["milestones"][COLONOSCOPY]["overdue"] is True
    assert set(kept.to_json()["milestones"]) == {REFERRAL, COLONOSCOPY}


# ---- a property: the milestone flags move only by their defined events -----------------------------------


@st.composite
def milestone_histories(draw):
    """Plausible and implausible events, many of them about milestones, applied in time order."""
    store_events = []
    names = [REFERRAL, COLONOSCOPY, "bogus"]
    for i in range(draw(st.integers(1, 40))):
        at = AT + timedelta(days=i * draw(st.integers(1, 3)) + 1, hours=draw(st.integers(0, 6)))
        kind = draw(st.sampled_from([
            "acknowledge", "accept_ownership", "contact_patient", "patient_acknowledge", "book_confirmatory",
            "complete_confirmatory", "clinician_review", "handoff_requested", "handoff_accepted", "handoff_rejected",
            "mark_overdue", "mark_overdue", "escalate", "escalate", "escalation_accepted", "consent_updated", "close",
        ]))
        name = draw(st.sampled_from(names))
        payload = {
            "mark_overdue": {"milestone": name, "due_at": draw(st.sampled_from([AT, AT + timedelta(days=20)])).isoformat()},
            "escalate": {"rung": draw(st.sampled_from(["reminder", "supervisor", "coordinator"])), "milestone": name},
            "escalation_accepted": {"milestone": name} if draw(st.booleans()) else {},
            "handoff_requested": {"to_provider": "endo"},
            "handoff_rejected": {"reason": "capacity"},
            "consent_updated": {"withdrawn": draw(st.booleans())},
            "close": {"evidence_ref": "DiagnosticReport/1", "signed_by": PCO},
        }.get(kind, {})
        actor = (SCHEDULER_ACTOR if kind in ("mark_overdue", "escalate") else
                 "supervisor:s" if kind == "escalation_accepted" else
                 SPEC if kind in ("handoff_accepted", "handoff_rejected") else PCO)
        store_events.append(Event(uuid4(), kind, at, actor, payload))
    return store_events


@settings(max_examples=600, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(milestone_histories())
def test_milestone_flags_move_only_by_their_defined_events(events):
    m = replay_model([Event(uuid4(), CASE_CREATED, AT, "system:intake")], PATHWAY)
    for ev in events:
        try:
            new = step(m, ev, PATHWAY)
        except InvalidTransition:
            continue
        for name, ms in new.milestones.items():
            prev = m.milestones.get(name, MilestoneState())
            if ms.overdue and not prev.overdue:
                assert ev.type == "mark_overdue" and ev.payload["milestone"] == name
                assert prev.started_at is not None and prev.met_at is None and not prev.void
            if ms.escalation_level > prev.escalation_level:
                assert ev.type == "escalate" and ev.payload["milestone"] == name and prev.overdue
                assert ms.escalation_level == prev.escalation_level + 1
            if prev.met_at is not None:
                assert ms == prev  # a met milestone never changes
            if ms.met_at is not None and prev.met_at is None:
                assert ev.type == PATHWAY.milestones[name].until_event
                assert not ms.overdue and ms.escalation_level == 0
            if ms.escalation_level:
                assert ms.overdue
            if ms.void:
                assert ms.met_at is None and not ms.overdue
        m = new


# ---- the read model, the timeline and the routes -----------------------------------------------------


client = make_client()
COORD = login_headers(client, "coordinator", "bea")
SUP = login_headers(client, "supervisor", "sam")
SCHED = {"Authorization": f"Bearer {app_settings.scheduler_service_token}"}
LABH = login_headers(client, "lab", "lab one")
PCOH = login_headers(client, "primary_care_owner", "Dr Milestone")


@pytest.fixture
def fresh_state():
    saved = cases.store
    cases.store = InMemoryEventStore()
    clock_module.clock.reset()
    yield
    clock_module.clock.reset()
    cases.store = saved


def api_post(cid, type_, headers, payload=None):
    r = client.post(f"/cases/{cid}/events", json={"type": type_, "payload": payload or {}}, headers=headers)
    assert r.status_code == 201, (type_, r.text)
    return r.json()


def test_the_case_page_carries_the_milestones_with_due_dates(fresh_state):
    cid = client.post("/cases", json={"patient_ref": "Patient/ms-1"}, headers=COORD).json()["case_id"]
    page = client.get(f"/cases/{cid}", headers=COORD).json()
    assert set(page["milestones"]) == {REFERRAL, COLONOSCOPY}
    ref = page["milestones"][REFERRAL]
    assert ref["due_at"] == "2026-01-26T08:00:00+00:00" and ref["met_at"] is None and ref["overdue"] is False
    assert ref["working_days"] == 15 and ref["met_late"] is None and ref["void"] is False
    col = page["milestones"][COLONOSCOPY]
    assert col["due_at"] is None and col["working_days"] == 31  # no referral yet: the colonoscopy clock has not started
    assert page["read_model"]["milestones"][REFERRAL]["started_at"] == "2026-01-05T08:00:00+00:00"
    assert page["due_at"] == "2026-01-07T08:00:00+00:00"  # the state's SLA is the next deadline


def test_a_referral_breach_over_http_shows_on_the_worklist_the_case_page_and_the_timeline(fresh_state):
    cid = client.post("/cases", json={"patient_ref": "Patient/ms-2"}, headers=COORD).json()["case_id"]
    client.post("/clock/advance", json={"days": 22}, headers=SUP)  # Tuesday 27 Jan: past 15 working days
    first = client.post("/scheduler/sweep", headers=SCHED).json()
    assert first["errors"] == [] and first["mark_overdue"] == 2  # the acknowledge SLA and the referral milestone
    assert client.post("/scheduler/sweep", headers=SCHED).json()["mark_overdue"] == 0
    page = client.get(f"/cases/{cid}", headers=COORD).json()
    ms = page["milestones"][REFERRAL]
    assert ms["overdue"] is True and ms["due_at"] == "2026-01-26T08:00:00+00:00" and ms["escalation_level"] >= 1
    breaches = [e for e in page["events"] if e["type"] == "mark_overdue" and e["payload"].get("milestone")]
    assert len(breaches) == 1 and breaches[0]["actor"] == "system:scheduler"
    assert breaches[0]["payload"]["due_at"] == "2026-01-26T08:00:00+00:00" and "state" not in breaches[0]["payload"]
    items = client.get(f"/cases/{cid}/timeline", headers=COORD).json()["items"]
    breach_item = next(i for i in items if i["kind"] == "milestone_breached")
    assert breach_item["milestone"] == REFERRAL and breach_item["at"] == "2026-01-26T08:00:00+00:00"
    assert breach_item["detected_at"] > breach_item["at"]
    # the referral ladder's reminder went to the owner in the same sweep as the state's, so it rides on that
    # escalation (A19): the timeline item for it names the milestone in `also_for`
    esc = [i for i in items if i["kind"] == "escalation"]
    assert esc and esc[0]["rung"] == "reminder"
    assert [x["milestone"] for x in esc[0]["also_for"]] == [REFERRAL]
    # the worklist row: overdue, and the next deadline is the oldest breach
    [row] = [r for r in client.get("/cases", headers=COORD).json() if r["case_id"] == cid]
    assert row["overdue"] is True
    assert [r["case_id"] for r in client.get("/cases", params={"overdue": "true"}, headers=COORD).json()] == [cid]
    # progress: the case moves on, requests the referral late; the milestone is met late and its breach clears
    api_post(cid, "acknowledge", LABH)
    api_post(cid, "accept_ownership", PCOH)
    api_post(cid, "handoff_requested", PCOH, {"to_provider": "endo-ms"})
    page = client.get(f"/cases/{cid}", headers=COORD).json()
    ms = page["milestones"][REFERRAL]
    assert ms["overdue"] is False and ms["met_at"] is not None and ms["met_late"] is True
    items = client.get(f"/cases/{cid}/timeline", headers=COORD).json()["items"]
    met = next(i for i in items if i["kind"] == "milestone_met" and i["milestone"] == REFERRAL)
    assert met["late"] is True
    # meeting the milestone cleared its ladder: there is no rung left to accept
    r = client.post(f"/cases/{cid}/events", json={"type": "escalation_accepted", "payload": {}}, headers=PCOH)
    assert r.status_code == 409


def test_a_milestone_breach_is_the_schedulers_alone(fresh_state):
    cid = client.post("/cases", json={"patient_ref": "Patient/ms-3"}, headers=COORD).json()["case_id"]
    body = {"type": "mark_overdue", "payload": {"milestone": REFERRAL, "due_at": "2026-01-06T08:00:00+00:00"}}
    for headers in (SUP, COORD, PCOH, LABH):
        assert client.post(f"/cases/{cid}/events", json=body, headers=headers).status_code == 403


def test_current_due_is_the_earliest_of_the_state_sla_and_the_running_milestones():
    store, cid = new_case()
    to_patient_acknowledged(store, cid)
    m = model(store, cid)
    state_due = UAE.add_working_days(AT + timedelta(days=4), 5)  # book_confirmatory, 5 working days from Fri 9 Jan
    assert readmodel.current_due(m, PATHWAY, UAE) == state_due < wd(15)
    sweep(store, cid, wd(1) + timedelta(days=40))  # everything breached: the breach dates are reported, oldest first
    m = model(store, cid)
    assert readmodel.current_due(m, PATHWAY, UAE) == min(m.breach_due_at, m.milestones[REFERRAL].breach_due_at)
    post(store, cid, "handoff_requested", PCO, wd(40), {"to_provider": "endo-1"})
    m = model(store, cid)
    # a met milestone is no longer a deadline
    assert readmodel.current_due(m, PATHWAY, UAE) == m.breach_due_at
    assert readmodel.milestones_json(m, PATHWAY, UAE)[REFERRAL]["due_at"] == wd(15).isoformat()  # but keeps its due date


def test_timeline_milestone_items_start_breach_and_met():
    store, cid = new_case()
    to_patient_acknowledged(store, cid)
    sweep(store, cid, wd(16, 1))
    post(store, cid, "handoff_requested", PCO, wd(16, 2), {"to_provider": "endo-1"})
    post(store, cid, "handoff_accepted", SPEC, wd(17))
    items = readmodel.timeline(store.events_for(cid), PATHWAY, UAE, wd(17))["items"]
    kinds = [(i["kind"], i["milestone"]) for i in items if i["kind"].startswith("milestone_")]
    assert kinds == [
        ("milestone_started", REFERRAL), ("milestone_breached", REFERRAL),
        ("milestone_met", REFERRAL), ("milestone_started", COLONOSCOPY),
    ]
    started = next(i for i in items if i["kind"] == "milestone_started" and i["milestone"] == REFERRAL)
    assert started["at"] == AT.isoformat() and started["due_at"] == wd(15).isoformat() and started["working_days"] == 15
    breach = next(i for i in items if i["kind"] == "milestone_breached")
    assert breach["at"] == wd(15).isoformat() and breach["detected_at"] == wd(16, 1).isoformat()
    met = next(i for i in items if i["kind"] == "milestone_met")
    assert met["at"] == wd(16, 2).isoformat() and met["late"] is True
