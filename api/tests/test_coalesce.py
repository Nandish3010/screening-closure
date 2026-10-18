"""A19, ADR-031: concurrent ladders coalesce, so a role is escalated once per case per working day.

A case can have the state's SLA ladder and one or more milestone ladders running at once, and every rung is an
`escalate` to the rung's target role. Without coalescing one role would be escalated twice on one working day.

Calendar (UAE, Mon-Fri): a case created Monday 5 Jan 2026 08:00Z has its referral milestone due 15 working days
later, Monday 26 Jan 08:00Z. The patient acknowledges (state `patient_acknowledged`, 5 working days to book) at a time
chosen per test, so the state breach falls on the same instant as, the same day as, or another day than, the
milestone's.
"""

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app import fhir_projection as fp
from app import payloads
from app.calendar import WorkingCalendar
from app.config import settings as app_settings
from app.events import CASE_CREATED, Event, InMemoryEventStore
from app.pathway import load_pathway
from app.risk.features import summarise_events
from app.state_machine import InvalidTransition, step
from app.readmodel import timeline
from app.sweep import SCHEDULER_ACTOR, plan_case, run_sweep, sweep_event_id
from fhirtest import FakeFhirClient

PATHWAY = load_pathway(app_settings.pathway_path)
UAE = WorkingCalendar()
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # Monday
PCO = "primary_care_owner:dr_k"
SUP = "supervisor:sam"
COORD = "coordinator:co"
REFERRAL = "referral_requested"
NS = uuid.UUID("a19a19a1-9a19-4a19-8a19-a19a19a19a19")
STATE = "patient_acknowledged"


def ts(day, hour=8, minute=0):
    """January 2026 at `hour`:`minute` Z."""
    return datetime(2026, 1, day, hour, minute, tzinfo=UTC)


def build(ack_at, store=None, salt="a"):
    """A case with deterministic event ids that sits in `patient_acknowledged` from `ack_at`, with the referral
    milestone running. Returns (store, case id)."""
    store = store or InMemoryEventStore()
    cid = uuid.uuid5(NS, f"case-{salt}")
    n = [0]

    def eid():
        n[0] += 1
        return uuid.uuid5(NS, f"{salt}-event-{n[0]}")

    store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, AT, "system:intake", {}, eid()), PATHWAY.initial_state)
    for kind, actor, when in (
        ("acknowledge", "lab:l", AT + timedelta(days=1)),
        ("accept_ownership", PCO, AT + timedelta(days=2)),
        ("contact_patient", PCO, AT + timedelta(days=3)),
        ("patient_acknowledge", "patient:p", ack_at),
    ):
        store.append(Event(cid, kind, when, actor, {}, eid()), PATHWAY)
    return store, cid


def sweep(store, cid, now):
    return run_sweep(store, PATHWAY, UAE, now, only=[cid])


def escalations(store, cid):
    return [e for e in store.events_for(cid) if e.type == "escalate"]


def model(store, cid):
    return store.read_model(cid, PATHWAY)


def real(store, cid):
    """The escalations that told somebody: the ones that are not attached to another's."""
    return [e for e in escalations(store, cid) if not e.payload.get("attached_to")]


def local_day(dt):
    return UAE.local_date(dt)


# ---- the same sweep ----------------------------------------------------------------------------------------


def test_the_state_sla_and_the_referral_milestone_breaching_in_one_sweep_make_one_escalation_with_also_for():
    store, cid = build(ts(19))  # the book_confirmatory SLA (5 working days) ends Mon 26 Jan 08:00Z, with the milestone
    now = ts(26, 9)
    result = sweep(store, cid, now)
    assert result.errors == [] and result.mark_overdue == 2 and result.escalate == 1
    [esc] = escalations(store, cid)
    assert esc.payload["state"] == STATE and esc.payload["rung"] == "reminder" and esc.payload["target"] == "owner"
    [also] = esc.payload["also_for"]
    assert also["milestone"] == REFERRAL and also["rung"] == "reminder" and also["level"] == 1
    assert also["due_at"] == ts(26).isoformat() and also["rung_due_at"] == ts(26).isoformat()
    assert "attached_to" not in esc.payload
    # the primary keeps the id of its own clock
    m = model(store, cid)
    assert esc.event_id == sweep_event_id(cid, m.clock_started_event_id, "escalate:reminder")
    # both ladders moved to the rung and record it on that one event
    ms = m.milestones[REFERRAL]
    assert m.escalation_level == ms.escalation_level == 1
    assert m.escalation_event_id == ms.escalation_event_id == esc.event_id
    assert m.escalation_at == ms.escalation_at == now
    # the breaches come first, then the escalation
    assert [e.type for e in store.events_for(cid)][-3:] == ["mark_overdue", "mark_overdue", "escalate"]


@pytest.mark.parametrize("payload", [{}, {"milestone": REFERRAL}, {"rung": "reminder"}])
def test_one_escalation_accepted_accepts_both_ladders_and_no_further_rung_follows(payload):
    store, cid = build(ts(19))
    sweep(store, cid, ts(26, 9))
    store.append(Event(cid, "escalation_accepted", ts(26, 10), PCO, payload), PATHWAY)
    m = model(store, cid)
    assert m.escalation_accepted and m.milestones[REFERRAL].escalation_accepted
    sweep(store, cid, ts(26) + timedelta(days=60))
    assert len(escalations(store, cid)) == 1  # the supervisor and coordinator rungs of both ladders never come


def test_acceptance_by_the_wrong_role_is_still_refused_and_changes_neither_ladder():
    store, cid = build(ts(19))
    sweep(store, cid, ts(26, 9))
    with pytest.raises(InvalidTransition):
        store.append(Event(cid, "escalation_accepted", ts(26, 10), SUP, {}), PATHWAY)  # the reminder is the owner's
    m = model(store, cid)
    assert not m.escalation_accepted and not m.milestones[REFERRAL].escalation_accepted


def test_a_long_outage_coalesces_every_rung_and_each_ladder_climbs_one_level_at_a_time():
    store, cid = build(ts(19))
    result = sweep(store, cid, ts(26) + timedelta(days=60))
    assert result.errors == [] and result.rejected == 0 and result.escalate == 3
    escs = escalations(store, cid)
    assert [e.payload["rung"] for e in escs] == ["reminder", "supervisor", "coordinator"]
    assert all(e.payload["state"] == STATE and [x["milestone"] for x in e.payload["also_for"]] == [REFERRAL] for e in escs)
    assert [x["level"] for e in escs for x in e.payload["also_for"]] == [1, 2, 3]
    m = model(store, cid)
    assert m.escalation_level == m.milestones[REFERRAL].escalation_level == 3


def test_a_milestone_that_is_a_level_behind_climbs_first_so_the_covered_levels_stay_in_order():
    """The state's ladder is at level 1 and the milestone's at 0: the milestone's reminder cannot ride on the
    state's supervisor escalation, so it is emitted first and only the supervisor rungs coalesce."""
    store, cid = build(ts(19))
    store.append(Event(cid, "mark_overdue", ts(26, 9), SCHEDULER_ACTOR, {
        "state": STATE, "due_at": ts(26).isoformat()}), PATHWAY)
    store.append(Event(cid, "escalate", ts(26, 9), SCHEDULER_ACTOR, {
        "rung": "reminder", "level": 1, "target": "owner", "state": STATE, "due_at": ts(26).isoformat()}), PATHWAY)
    now = UAE.add_working_days(ts(26), 3) + timedelta(hours=1)  # the supervisor rungs of both ladders are due
    planned = plan_case(cid, model(store, cid), PATHWAY, UAE, now)
    kinds = [(e.type, e.payload.get("rung"), e.payload.get("milestone"), len(e.payload.get("also_for", ()))) for e in planned]
    assert kinds == [("mark_overdue", None, REFERRAL, 0), ("escalate", "reminder", REFERRAL, 0),
                     ("escalate", "supervisor", None, 1)]
    m = model(store, cid)
    for e in planned:
        m = step(m, e, PATHWAY)  # the machine accepts the plan in order
    assert m.escalation_level == m.milestones[REFERRAL].escalation_level == 2


# ---- a later sweep, the same working day ---------------------------------------------------------------------


def test_a_breach_later_the_same_working_day_attaches_to_the_earlier_escalation():
    store, cid = build(ts(19, 14))  # the state SLA ends Mon 26 Jan 14:00Z, six hours after the milestone's
    first = sweep(store, cid, ts(26, 10))
    assert first.mark_overdue == 1 and first.escalate == 1
    [m1] = escalations(store, cid)
    assert m1.payload["milestone"] == REFERRAL and "attached_to" not in m1.payload and "also_for" not in m1.payload

    second = sweep(store, cid, ts(26, 15))
    assert second.errors == [] and second.mark_overdue == 1 and second.escalate == 1
    s1 = escalations(store, cid)[-1]
    assert s1.payload["state"] == STATE and s1.payload["rung"] == "reminder" and s1.payload["target"] == "owner"
    assert s1.payload["attached_to"] == str(m1.event_id) and "also_for" not in s1.payload
    # it has the id of its own clock, not the primary's, and it moved the state's ladder
    m = model(store, cid)
    assert s1.event_id == sweep_event_id(cid, m.clock_started_event_id, "escalate:reminder") != m1.event_id
    assert m.escalation_level == 1 and m.escalation_event_id == m1.event_id and m.escalation_at == m1.at
    # told once: one escalation that is not an attachment
    assert [e.event_id for e in real(store, cid)] == [m1.event_id]
    # the read model's consumers do not count it
    assert summarise_events(store.events_for(cid)).prior_escalations == 1
    # accepting it accepts both
    store.append(Event(cid, "escalation_accepted", ts(26, 16), PCO, {}), PATHWAY)
    m = model(store, cid)
    assert m.escalation_accepted and m.milestones[REFERRAL].escalation_accepted


def test_the_timeline_item_carries_also_for_and_attached_to_and_acceptance_marks_every_ladder():
    store, cid = build(ts(19))
    sweep(store, cid, ts(26, 9))
    store.append(Event(cid, "escalation_accepted", ts(26, 10), PCO, {}), PATHWAY)
    items = timeline(store.events_for(cid), PATHWAY, UAE, ts(26, 11))["items"]
    [item] = [i for i in items if i["kind"] == "escalation"]
    assert item["attached_to"] is None and [x["milestone"] for x in item["also_for"]] == [REFERRAL]
    assert item["accepted"] is True and item["accepted_by"] == PCO

    store, cid = build(ts(19, 14), salt="b")
    sweep(store, cid, ts(26, 10))
    sweep(store, cid, ts(26, 15))
    store.append(Event(cid, "escalation_accepted", ts(26, 16), PCO, {}), PATHWAY)
    a, b = [i for i in timeline(store.events_for(cid), PATHWAY, UAE, ts(26, 17))["items"] if i["kind"] == "escalation"]
    assert a["attached_to"] is None and a["also_for"] is None and b["attached_to"] == a["event_id"]
    assert a["accepted"] and b["accepted"]


def test_the_attached_escalation_creates_no_second_task_and_its_event_is_still_projected():
    fake = FakeFhirClient()
    inner = InMemoryEventStore()
    tag = {"system": "https://screening-closure.example/test", "code": "unit"}
    projector = fp.Projector(fake, inner, PATHWAY, UAE, fp.InMemoryProjectionQueue(), tag=tag)
    store = fp.ProjectingStore(inner, projector)
    store, cid = build(ts(19, 14), store=store)
    sweep(store, cid, ts(26, 10))
    sweep(store, cid, ts(26, 15))
    [attached] = [e for e in escalations(store, cid) if e.payload.get("attached_to")]
    tasks = fake.of_type("Task")
    assert [t["id"] for t in tasks] == [f"task-{cid}"]  # one Task for the case, whoever was escalated
    assert tasks[0]["extension"][1] == {"url": fp.EXT + "escalation-level", "valueInteger": 1}
    assert f"prov-{attached.event_id}" in {p["id"] for p in fake.of_type("Provenance")}  # still in the audit trail


# ---- another working day ---------------------------------------------------------------------------------------


def test_breaches_on_different_working_days_are_two_escalations_to_the_owner():
    store, cid = build(ts(20, 14))  # the state SLA ends Tue 27 Jan 14:00Z, a day after the milestone's
    sweep(store, cid, ts(26, 10))
    sweep(store, cid, ts(27, 15))
    owner = [e for e in escalations(store, cid) if e.payload["target"] == "owner"]
    assert [e.payload.get("milestone") or e.payload.get("state") for e in owner] == [REFERRAL, STATE]
    assert all("attached_to" not in e.payload and "also_for" not in e.payload for e in owner)
    assert len({local_day(e.at) for e in owner}) == 2
    m = model(store, cid)
    assert m.escalation_event_id == owner[1].event_id != m.milestones[REFERRAL].escalation_event_id


def test_an_accepted_escalation_does_not_hold_the_day_open():
    store, cid = build(ts(19, 14))
    sweep(store, cid, ts(26, 10))  # the referral milestone's reminder to the owner
    store.append(Event(cid, "escalation_accepted", ts(26, 11), PCO, {"milestone": REFERRAL}), PATHWAY)
    sweep(store, cid, ts(26, 15))  # the state's reminder, same day: nothing unaccepted to attach to
    s1 = escalations(store, cid)[-1]
    assert s1.payload["state"] == STATE and "attached_to" not in s1.payload
    assert len(real(store, cid)) == 2


# ---- ids and idempotency ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("ack", [ts(19), ts(19, 14)])
def test_ids_are_deterministic_and_a_rerun_emits_nothing(ack):
    runs = []
    for _ in range(2):
        store, cid = build(ack)
        for when in (ts(26, 10), ts(26, 15), ts(27, 9)):
            sweep(store, cid, when)
        runs.append([(e.event_id, e.type, e.payload) for e in store.events_for(cid)])
    assert runs[0] == runs[1]  # the same case replayed gives the same ids and payloads
    store, cid = build(ack)
    sweep(store, cid, ts(27, 9))
    before = [e.event_id for e in store.events_for(cid)]
    again = sweep(store, cid, ts(27, 9))
    assert again.mark_overdue == again.escalate == again.rejected == 0 and again.errors == []
    assert [e.event_id for e in store.events_for(cid)] == before
    # the planner is a pure function of the model and the time
    m = model(store, cid)
    assert plan_case(cid, m, PATHWAY, UAE, ts(27, 9)) == plan_case(cid, m, PATHWAY, UAE, ts(27, 9))


def test_a_second_writer_planning_from_the_same_stale_model_adds_nothing():
    store, cid = build(ts(19))
    stale = model(store, cid)
    a = plan_case(cid, stale, PATHWAY, UAE, ts(26, 9))
    b = plan_case(cid, stale, PATHWAY, UAE, ts(26, 9, 30))
    assert [e.event_id for e in a] == [e.event_id for e in b]
    for e in (*a, *b):
        store.append(e, PATHWAY)
    assert len(escalations(store, cid)) == 1 and len(store.events_for(cid)) == 8


# ---- the property: a role is not escalated twice on one working day ----------------------------------------------


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    ack_offset_minutes=st.integers(min_value=0, max_value=12 * 24 * 60),  # the state SLA ends within ~12 days of ...
    gaps=st.lists(st.integers(min_value=30, max_value=2 * 24 * 60), min_size=3, max_size=40),
    accept=st.one_of(st.none(), st.integers(min_value=0, max_value=39)),
)
def test_a_role_is_never_escalated_twice_on_one_working_day_while_an_earlier_escalation_is_unaccepted(
    ack_offset_minutes, gaps, accept
):
    # ... the milestone's due date (Mon 26 Jan 08:00Z); sweeps run at least every two days (a scheduler outage that
    # makes one ladder catch up two rungs at once is covered by the outage test above)
    ack = ts(14) + timedelta(minutes=ack_offset_minutes)
    store, cid = build(ack)
    now = ts(20)
    accepted_at = None
    for i, gap in enumerate(gaps):
        now = now + timedelta(minutes=gap)
        sweep(store, cid, now)
        if accept == i and accepted_at is None and model(store, cid).escalation_level:
            m = model(store, cid)
            who = PCO if m.escalation_level == 1 else SUP if m.escalation_level == 2 else COORD
            store.append(Event(cid, "escalation_accepted", now + timedelta(minutes=1), who, {}), PATHWAY)
            accepted_at = now
    told = {}
    for e in real(store, cid):
        told.setdefault((e.payload["target"], local_day(e.at)), []).append(e)
    for key, evs in told.items():
        if accepted_at is None:
            assert len(evs) == 1, (key, [(x.payload.get("state") or x.payload.get("milestone"), x.at) for x in evs])
    # every escalate the machine accepted names its ladder; every attached one points at a real earlier event
    ids = [e.event_id for e in store.events_for(cid)]
    assert len(ids) == len(set(ids))
    for e in escalations(store, cid):
        if e.payload.get("attached_to"):
            assert UUID(e.payload["attached_to"]) in ids[: ids.index(e.event_id)]


# ---- the state machine refuses what it cannot see through ------------------------------------------------------------------


def overdue_model(state_only=False):
    """Both ladders overdue at level 0 (state patient_acknowledged, milestone referral_requested)."""
    store, cid = build(ts(19))
    now = ts(26, 9)
    due = ts(26).isoformat()
    store.append(Event(cid, "mark_overdue", now, SCHEDULER_ACTOR, {"state": STATE, "due_at": due}), PATHWAY)
    if not state_only:
        store.append(Event(cid, "mark_overdue", now, SCHEDULER_ACTOR, {"milestone": REFERRAL, "due_at": due}), PATHWAY)
    return model(store, cid), cid


def esc(cid, payload, at=ts(26, 9), eid=None):
    return Event(cid, "escalate", at, SCHEDULER_ACTOR, payload, eid or uuid.uuid4())


def entry(rung="reminder", level=1, milestone=REFERRAL, **extra):
    return {"milestone": milestone, "rung": rung, "level": level, "due_at": ts(26).isoformat(),
            "rung_due_at": ts(26).isoformat(), **extra}


def primary(rung="reminder", level=1, target="owner", **extra):
    return {"rung": rung, "level": level, "target": target, "state": STATE, **extra}


def refused(m, cid, payload, match=None):
    with pytest.raises(InvalidTransition, match=match):
        step(m, esc(cid, payload), PATHWAY)


def test_a_valid_also_for_moves_both_ladders():
    m, cid = overdue_model()
    ev = esc(cid, primary(also_for=[entry()]))
    out = step(m, ev, PATHWAY)
    assert out.escalation_level == out.milestones[REFERRAL].escalation_level == 1
    assert out.escalation_event_id == out.milestones[REFERRAL].escalation_event_id == ev.event_id


def test_also_for_refusals():
    m, cid = overdue_model()
    refused(*overdue_model(state_only=True), primary(also_for=[entry()]), "not overdue")  # the milestone has not breached
    refused(m, cid, primary(also_for=[entry(level=2, rung="supervisor")]), "next rung")  # not the milestone's next rung
    refused(m, cid, primary(also_for=[entry(rung="supervisor", level=1)]), "next rung")  # level 1 is the reminder
    refused(m, cid, primary(also_for=[entry(milestone="no_such")]), "unknown milestone")
    refused(m, cid, primary(also_for=[{"state": "closed", "rung": "reminder", "level": 1}]), "names state")
    refused(m, cid, primary(also_for=[entry(), entry()]), "twice")  # the same ladder twice
    refused(m, cid, primary(also_for=[{"state": STATE, "rung": "reminder", "level": 1}]), "twice")  # its own ladder
    refused(m, cid, primary(also_for=[]), "non-empty")
    refused(m, cid, primary(also_for="x"), "non-empty")
    refused(m, cid, primary(also_for=["x"]), "objects")
    refused(m, cid, primary(also_for=[{"rung": "reminder", "level": 1}]), "exactly one")
    refused(m, cid, primary(also_for=[entry(rung="nope")]), "unknown escalation rung")
    refused(m, cid, primary(also_for=[entry()], attached_to=str(uuid.uuid4())), "both")


def test_also_for_with_a_target_that_is_not_the_primarys_is_refused():
    m, cid = overdue_model()
    # the state is at level 1 so its next rung is the supervisor's; the milestone is also at level 1, so ITS next is too:
    m = step(m, esc(cid, primary(also_for=[entry()])), PATHWAY)
    # primary: the state's supervisor rung; the entry claims the milestone's reminder rung, whose target is the owner
    refused(m, cid, primary(rung="supervisor", level=2, target="supervisor", also_for=[entry(rung="reminder", level=2)]),
            "next rung")


def test_also_for_for_an_accepted_ladder_is_refused():
    m, cid = overdue_model()
    m = step(m, esc(cid, primary(also_for=[entry()])), PATHWAY)
    m = step(m, Event(cid, "escalation_accepted", ts(26, 10), PCO, {"milestone": REFERRAL}), PATHWAY)
    assert m.escalation_accepted  # accepted together
    refused(m, cid, primary(rung="supervisor", level=2, target="supervisor", also_for=[entry(rung="supervisor", level=2)]))


def test_attached_to_refusals():
    m, cid = overdue_model()
    first = esc(cid, {"rung": "reminder", "level": 1, "target": "owner", "milestone": REFERRAL})
    m1 = step(m, first, PATHWAY)  # the milestone's reminder is the current escalation to the owner
    attach = {"rung": "reminder", "level": 1, "target": "owner", "state": STATE}
    ok = step(m1, esc(cid, {**attach, "attached_to": str(first.event_id)}), PATHWAY)
    assert ok.escalation_level == 1 and ok.escalation_event_id == first.event_id and ok.escalation_at == first.at
    refused(m1, cid, {**attach, "attached_to": str(uuid.uuid4())}, "attached_to")  # not an escalation of this case
    refused(m, cid, {**attach, "attached_to": str(first.event_id)}, "attached_to")  # nobody has escalated yet
    refused(m1, cid, {**attach, "attached_to": "not-a-uuid"}, "not an event id")
    # an escalation cannot attach to itself: the ladder's own current escalation is not "another ladder's"
    m2 = step(m1, esc(cid, {"rung": "supervisor", "level": 2, "target": "supervisor", "milestone": REFERRAL}), PATHWAY)
    refused(m2, cid, {**attach, "attached_to": str(first.event_id)}, "attached_to")  # superseded by the next rung
    # the escalation it names is aimed at another role
    own = esc(cid, {"rung": "supervisor", "level": 2, "target": "supervisor", "state": STATE,
                    "attached_to": str(first.event_id)})
    mid = step(m, esc(cid, attach), PATHWAY)  # the state is at level 1 on its own escalation
    refused(mid, cid, own.payload, "attached_to")  # first's holder is the milestone, still at level 0 here
    # an accepted escalation cannot be attached to
    done = step(m1, Event(cid, "escalation_accepted", ts(26, 10), PCO, {"milestone": REFERRAL}), PATHWAY)
    refused(done, cid, {**attach, "attached_to": str(first.event_id)}, "attached_to")


# ---- the payload schema ----------------------------------------------------------------------------------------------------


def test_the_payload_schema_takes_also_for_and_attached_to_strictly():
    good = {"rung": "reminder", "state": STATE, "level": 1, "target": "owner"}
    payloads.validate("escalate", {**good, "also_for": [entry()]})
    payloads.validate("escalate", {**good, "also_for": [{**entry(milestone=None), "state": STATE}]} if False else
                      {**good, "attached_to": str(uuid.uuid4())})
    bad = [
        {"also_for": []},
        {"also_for": [entry(extra_key=1)]},
        {"also_for": [{**entry(), "state": STATE}]},  # both
        {"also_for": [{k: v for k, v in entry().items() if k != "milestone"}]},  # neither
        {"also_for": [{k: v for k, v in entry().items() if k != "rung"}]},
        {"also_for": [{k: v for k, v in entry().items() if k != "level"}]},
        {"also_for": [{**entry(), "level": 0}]},
        {"also_for": [{**entry(), "due_at": "tomorrow"}]},
        {"also_for": entry()},
        {"attached_to": "x"},
        {"attached_to": 5},
        {"also_for": [entry()], "attached_to": str(uuid.uuid4())},
    ]
    for extra in bad:
        with pytest.raises(payloads.PayloadSchemaError):
            payloads.validate("escalate", {**good, **extra})
