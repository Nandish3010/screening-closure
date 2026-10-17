"""Moat 1: the closure invariant, proven by property test, over the full event vocabulary.

(a) A case is never in `closed` unless a `close` event carrying evidence_ref AND
    signed_by (from a non-automated actor) was applied, from `clinician_reviewed`.
(b) Replaying the same events twice yields identical state (and read model).
(c) Every state change follows a declared transition; side states are entered and left only
    by their defined events (T-A14). The oracle below is written out by hand from the spec,
    independently of the YAML loader.
(d) The overdue / escalation flags move only by their defined events.
"""

import dataclasses
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.config import settings as app_settings
from app.events import CASE_CREATED, Event, InMemoryEventStore, replay, replay_model
from app.pathway import PathwayError, load_pathway
from app.state_machine import (
    CaseModel,
    InvalidTransition,
    apply,
    genesis,
    initial_model,
    is_automated,
    step,
)

PATHWAY = load_pathway(app_settings.pathway_path)
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
CASE = uuid4()

MAIN = list(PATHWAY.states)
SIDE = list(PATHWAY.side_states)
OPEN = [*MAIN[:-1], *SIDE]
ALL_EVENT_TYPES = [*PATHWAY.event_types, "bogus_event", CASE_CREATED]
HUMANS = [
    "clinician:dr_a", "primary_care_owner:pc", "specialist_owner:sp", "coordinator:co",
    "supervisor:su", "patient:pa", "lab:lb",
]
AUTOMATED = ["system:scheduler", "watchdog", "model:drafter"]
ACTORS = [*HUMANS, *AUTOMATED]
SIGNED = {"evidence_ref": "DiagnosticReport/1", "signed_by": "Practitioner/9"}
HUMAN_ONLY = {
    "escalation_accepted", "handoff_accepted", "consent_updated", "message_approved",
    "callback_requested", "reopen",
}


def make_event(type_: str, actor: str, payload: dict, at: datetime = AT) -> Event:
    return Event(CASE, type_, at, actor, payload)


def start_model() -> CaseModel:
    return genesis(initial_model(PATHWAY), make_event(CASE_CREATED, "system:intake", {}))


# ---- a hand-written oracle for the allowed state changes ----------------------------------


def allowed_change(prev: CaseModel, ev: Event, new_state: str) -> bool:
    s, t = prev.state, ev.type
    for a, b, event in zip(MAIN, MAIN[1:], [x.event for x in PATHWAY.main_transitions]):
        if (s, t, new_state) == (a, event, b):
            return True
    if t == "handoff_rejected":
        return s in ("patient_acknowledged", "confirmatory_booked") and new_state == "referral_rejected"
    if t in ("handoff_accepted", "reassign"):
        return s == "referral_rejected" and new_state == "owner_accepted"
    if t == "consent_updated":
        if ev.payload.get("withdrawn") is True:
            return s in OPEN and s != "consent_withdrawn" and new_state == "consent_withdrawn"
        return (
            ev.payload.get("withdrawn") is False
            and s == "consent_withdrawn"
            and new_state == prev.resume_state
        )
    if t == "mark_lost_contact":
        return s == "patient_contacted" and new_state == "lost_contact"
    if t == "contact_patient":
        return s == "lost_contact" and new_state == "patient_contacted"
    if t == "reopen":
        return s == "closed" and new_state == "reopened"
    if t == "clinician_review":
        return s == "reopened" and new_state == "clinician_reviewed"
    return False


def check_step(prev: CaseModel, ev: Event, new: CaseModel) -> None:
    # (c) state changes follow the declared transitions, annotations never change state
    if new.state != prev.state:
        assert allowed_change(prev, ev, new.state), (prev.state, ev.type, new.state)
    # closure invariant, step by step
    if new.state == "closed":
        assert ev.type == "close" and prev.state == "clinician_reviewed"
        assert ev.payload.get("evidence_ref") and ev.payload.get("signed_by")
        assert not is_automated(ev.actor)
    if ev.type in HUMAN_ONLY:
        assert not is_automated(ev.actor)
    if ev.type == "reopen":
        assert ev.payload.get("reason")
    if ev.type == "mark_lost_contact":
        assert ev.payload["failed_attempts"] >= 3
    # (d) flags
    if not prev.overdue and new.overdue:
        assert ev.type == "mark_overdue" and new.state == prev.state
    if prev.overdue and not new.overdue:
        assert new.state != prev.state
    if new.escalation_level > prev.escalation_level:
        assert ev.type == "escalate" and prev.overdue
        assert new.escalation_level == prev.escalation_level + 1
        assert not prev.escalation_accepted
    if new.escalation_level < prev.escalation_level:
        assert new.state != prev.state and new.escalation_level == 0
    if new.escalation_level:
        assert new.overdue
    if new.escalation_accepted and not prev.escalation_accepted:
        assert ev.type == "escalation_accepted" and prev.escalation_level >= 1
    if new.owner != prev.owner:
        assert ev.type in ("accept_ownership", "handoff_accepted", "reassign")
    # (e) referral handoff contract (A5): ownership moves only on acceptance of a pending request
    if ev.type == "handoff_requested":
        assert prev.owner is not None and prev.pending_handoff is None
        assert new.pending_handoff and new.pending_handoff["from_owner"] == prev.owner
        assert new.owner == prev.owner
    elif ev.type == "handoff_accepted":
        assert prev.pending_handoff is not None and new.pending_handoff is None
        assert new.owner == ev.actor and not is_automated(ev.actor)
        assert new.provider == (ev.payload.get("provider") or prev.pending_handoff["to_provider"]).strip()
    elif ev.type == "handoff_rejected":
        assert prev.pending_handoff is not None and new.pending_handoff is None
        assert new.owner == prev.owner  # the case stays with the referrer
        assert new.last_handoff["outcome"] == "rejected"
        if ev.payload["reason"] == PATHWAY.handoff_bounce_reason:
            assert ev.actor == "system:scheduler" and ev.payload.get("bounced") is True
        else:
            assert ev.payload["reason"] in PATHWAY.handoff_reject_reasons
            assert ev.actor != "system:scheduler"
    elif ev.type == "reassign":
        assert new.pending_handoff is None
    else:
        assert new.pending_handoff == prev.pending_handoff
    assert new.consent_frozen == (new.state == "consent_withdrawn")
    assert (new.resume_state is not None) == (new.state == "consent_withdrawn")
    assert new.last_event_at == ev.at


def run(events):
    """Apply events, skipping invalid ones (as the API does by returning 409)."""
    model = start_model()
    applied, trace = [], []
    for ev in events:
        if ev.type == CASE_CREATED:
            continue
        try:
            new = step(model, ev, PATHWAY)
        except InvalidTransition:
            continue
        trace.append((model, ev, new))
        applied.append(ev)
        model = new
    return model, applied, trace


def fold(events):
    model, applied, _ = run(events)
    return model.state, applied


# ---- generators ---------------------------------------------------------------------------

RUNGS = [r.rung for r in PATHWAY.escalation_ladder]


@st.composite
def plausible_event(draw, model: CaseModel, at: datetime):
    """An event that is usually valid for the current model, sometimes sabotaged."""
    outgoing = sorted({t.event for t in PATHWAY.transitions if t.from_state == model.state})
    if len(outgoing) > 1 and draw(st.integers(0, 9)) < 8:  # consent flips are everywhere: rarer
        outgoing = [e for e in outgoing if e != "consent_updated"]
    annotations = sorted(PATHWAY.annotations) if model.state in OPEN else []
    # half the time follow the pathway (so deep states, side states and `closed` are reached)
    if outgoing and (not annotations or draw(st.booleans())):
        type_ = draw(st.sampled_from(outgoing))
    else:
        type_ = draw(st.sampled_from(annotations or outgoing))
    if model.overdue and draw(st.integers(0, 2)) == 0:  # walk the ladder once a case is overdue
        type_ = draw(st.sampled_from(["escalate", "escalate", "escalation_accepted"]))
    next_rung = PATHWAY.rung_at(model.escalation_level + 1)
    payload = {
        **SIGNED,
        "reason": draw(st.sampled_from(["capacity", "wrong_service", "missing_info", "patient_declined", "timeout"])),
        "to_provider": "endoscopy-unit",
        "bounced": draw(st.booleans()),
        "owner": "primary_care_owner:new",
        "withdrawn": draw(st.booleans()),
        "failed_attempts": draw(st.integers(0, 5)),
        "state": model.state,
        "due_at": AT.isoformat(),
        "rung": next_rung.rung if next_rung else draw(st.sampled_from(RUNGS)),
    }
    if type_ == "escalation_accepted" and model.escalation_level:
        payload["rung"] = PATHWAY.rung_at(model.escalation_level).rung
    actor = draw(st.sampled_from(HUMANS * 4 + AUTOMATED))
    if model.escalation_level and draw(st.booleans()):  # a likely-valid acceptor
        role = PATHWAY.rung_at(model.escalation_level).accepted_by[0]
        actor = f"{role}:x"
    # sabotage at most one key (drop it or blank it) in about a fifth of the events
    roll = draw(st.integers(0, 5 * len(payload) - 1))
    if roll < len(payload):
        key = sorted(payload)[roll]
        if draw(st.booleans()):
            del payload[key]
        else:
            payload[key] = draw(st.sampled_from(["", None, 0, False, "bogus"]))
    return make_event(type_, actor, payload, at)


@st.composite
def noise_event(draw, at: datetime):
    payload = draw(
        st.fixed_dictionaries(
            {},
            optional={
                "evidence_ref": st.sampled_from(["", "DiagnosticReport/1", None]),
                "signed_by": st.sampled_from(["", "Practitioner/9", None]),
                "reason": st.sampled_from(["", "capacity", "timeout", "nonsense"]),
                "to_provider": st.sampled_from(["", "endoscopy-unit", 7]),
                "bounced": st.sampled_from([True, False, None]),
                "withdrawn": st.sampled_from([True, False, 1, 0, "true", None]),
                "failed_attempts": st.sampled_from([0, 2, 3, 4, True, "9"]),
                "state": st.sampled_from(OPEN),
                "due_at": st.sampled_from(["", "garbage", AT.isoformat()]),
                "rung": st.sampled_from([*RUNGS, "regulator", ""]),
                "owner": st.sampled_from(["", "x:y"]),
            },
        )
    )
    return make_event(
        draw(st.sampled_from(ALL_EVENT_TYPES)), draw(st.sampled_from(ACTORS)), payload, at
    )


@st.composite
def event_sequences(draw):
    """A stateful fuzzer: mostly events that are plausible for the model so far (so the search
    reaches reopened, lost_contact, escalation, consent excursions...), plus pure noise."""
    model = start_model()
    events = []
    # a valid prefix of the main path, so late states (and `closed`, `reopened`) are reached
    for i, t in enumerate(PATHWAY.main_transitions[: draw(st.integers(0, len(MAIN) - 1))]):
        ev = make_event(t.event, "primary_care_owner:pc", dict(SIGNED), AT + timedelta(days=i + 1))
        events.append(ev)
        model = step(model, ev, PATHWAY)
    for i in range(len(events), len(events) + draw(st.integers(0, 30))):
        at = AT + timedelta(days=i + 1)
        if draw(st.integers(0, 9)) < 7:
            ev = draw(plausible_event(model, at))
        else:
            ev = draw(noise_event(at))
        events.append(ev)
        try:
            model = step(model, ev, PATHWAY)
        except InvalidTransition:
            pass
    return events


# ---- T-A1 / T-A2 / T-A14 properties -------------------------------------------------------


@settings(max_examples=2000, deadline=None)
@given(event_sequences())
def test_never_closed_without_evidence_and_human_signoff(events):
    state, applied = fold(events)
    if state == "closed":
        closers = [e for e in applied if e.type == "close"]
        assert closers, "case is closed but no close event was applied"
        last = closers[-1]
        assert last.payload.get("evidence_ref"), "closed without evidence_ref"
        assert last.payload.get("signed_by"), "closed without signed_by"
        assert not last.actor.startswith(("system:", "scheduler", "watchdog", "model:"))
        # nothing happened after the last close except annotations that cannot reopen
        after = applied[applied.index(last) + 1:]
        assert all(e.type not in ("reopen",) for e in after)


@settings(max_examples=1500, deadline=None)
@given(event_sequences())
def test_side_states_and_flags_move_only_by_their_defined_events(events):
    """T-A14: the step-by-step invariants (see check_step) hold over the extended alphabet."""
    model, _, trace = run(events)
    for prev, ev, new in trace:
        check_step(prev, ev, new)
    # no side state is ever adjacent to `closed` in the trace
    for prev, ev, new in trace:
        if new.state == "closed":
            assert prev.state not in SIDE


@settings(max_examples=500, deadline=None)
@given(event_sequences())
def test_replay_is_deterministic(events):
    final, applied = fold(events)
    assert replay(applied, PATHWAY) == replay(applied, PATHWAY)
    assert replay_model(applied, PATHWAY) == replay_model(applied, PATHWAY)

    store = InMemoryEventStore()
    case_id = uuid4()
    store.create_case(
        case_id, PATHWAY.name, Event(case_id, CASE_CREATED, AT, "system:intake"),
        PATHWAY.initial_state,
    )
    for ev in applied:
        store.append(dataclasses.replace(ev, case_id=case_id), PATHWAY)
    assert store.current_state(case_id) == final
    assert replay(store.events_for(case_id), PATHWAY) == final
    # the read model kept incrementally equals a fresh replay, up to the genesis timestamp
    kept = store.read_model(case_id, PATHWAY)
    replayed = replay_model(store.events_for(case_id), PATHWAY)
    assert kept == replayed


@settings(max_examples=300, deadline=None)
@given(event_sequences())
def test_every_prefix_of_a_history_replays_to_the_incremental_model(events):
    model = start_model()
    log = [make_event(CASE_CREATED, "system:intake", {})]
    for ev in events:
        try:
            model = step(model, ev, PATHWAY)
        except InvalidTransition:
            continue
        log.append(ev)
        assert replay_model(log, PATHWAY) == model


# ---- unit tests: main path and the closure guard (T-A3, T-A4) -----------------------------


def test_every_pathway_transition_is_reachable_in_order():
    state = PATHWAY.initial_state
    for t in PATHWAY.main_transitions:
        state = apply(state, make_event(t.event, "clinician:dr_a", dict(SIGNED)), PATHWAY)
        assert state == t.to_state
    assert state == "closed"


@pytest.mark.parametrize(
    "payload",
    [{}, {"evidence_ref": "x"}, {"signed_by": "y"}, {"evidence_ref": "", "signed_by": "y"}],
)
def test_close_rejected_without_both_evidence_and_signoff(payload):
    with pytest.raises(InvalidTransition):
        apply("clinician_reviewed", make_event("close", "clinician:dr_a", payload), PATHWAY)


def test_automated_actor_cannot_close():
    payload = {"evidence_ref": "x", "signed_by": "y"}
    for actor in AUTOMATED:
        with pytest.raises(InvalidTransition):
            apply("clinician_reviewed", make_event("close", actor, payload), PATHWAY)


@pytest.mark.parametrize("state", [s for s in OPEN if s != "clinician_reviewed"])
def test_close_is_rejected_from_every_other_open_state(state):
    model = dataclasses.replace(start_model(), state=state)
    with pytest.raises(InvalidTransition):
        step(model, make_event("close", "clinician:dr_a", dict(SIGNED)), PATHWAY)


# ---- unit tests: read model and flags ------------------------------------------------------


def drive(*steps, model=None, start_day=0):
    """steps: (event type, actor, payload) applied one day apart. Returns the final model."""
    model = model or start_model()
    for i, (type_, actor, payload) in enumerate(steps, start=start_day + 1):
        model = step(model, make_event(type_, actor, payload, AT + timedelta(days=i)), PATHWAY)
    return model


OWNER = "primary_care_owner:dr_k"
TO_CONTACTED = [
    ("acknowledge", "lab:l", {}),
    ("accept_ownership", OWNER, {}),
    ("contact_patient", OWNER, {}),
]


def test_read_model_shape_and_values():
    m = drive(*TO_CONTACTED)
    assert m.to_json() == {
        "state": "patient_contacted",
        "overdue": False,
        "escalation_level": 0,
        "escalation_accepted": False,
        "owner": OWNER,
        "consent_frozen": False,
        "last_event_at": (AT + timedelta(days=3)).isoformat(),
        "resume_state": None,
        "clock_started_at": (AT + timedelta(days=3)).isoformat(),
        "breach_due_at": None,
        "provider": None,
        "pending_handoff": None,
        "last_handoff": None,
    }


def test_a_case_can_be_overdue_and_patient_contacted():
    m = drive(*TO_CONTACTED)
    due = AT + timedelta(days=10)
    over = step(
        m,
        make_event("mark_overdue", "system:scheduler",
                   {"state": "patient_contacted", "due_at": due.isoformat()}, due + timedelta(hours=1)),
        PATHWAY,
    )
    assert over.state == "patient_contacted" and over.overdue and over.breach_due_at == due
    # progress resolves the breach and starts the next state's clock
    nxt = step(over, make_event("patient_acknowledge", "patient:p", {}, due + timedelta(days=1)), PATHWAY)
    assert nxt.state == "patient_acknowledged" and not nxt.overdue
    assert nxt.escalation_level == 0 and nxt.breach_due_at is None
    assert nxt.clock_started_at == due + timedelta(days=1)


def over_model(rung_steps=0):
    m = drive(*TO_CONTACTED)
    due = AT + timedelta(days=10)
    m = step(
        m,
        make_event("mark_overdue", "system:scheduler",
                   {"state": "patient_contacted", "due_at": due.isoformat()}, due + timedelta(hours=1)),
        PATHWAY,
    )
    for name in RUNGS[:rung_steps]:
        m = step(
            m,
            make_event("escalate", "system:scheduler", {"rung": name, "state": "patient_contacted"},
                       due + timedelta(days=2)),
            PATHWAY,
        )
    return m


LADDER_ACTORS = [*ACTORS, "supervisor:su2", "coordinator:co2", "primary_care_owner:pc2", "specialist_owner:sp2"]


@settings(max_examples=400, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.sampled_from(["escalate", "escalation_accepted"]),
            st.sampled_from(LADDER_ACTORS),
            st.sampled_from([*RUNGS, "regulator", None]),
        ),
        max_size=12,
    )
)
def test_ladder_property_rungs_in_order_and_acceptance_stops_the_breach(steps):
    model = over_model()
    for i, (type_, actor, rung) in enumerate(steps):
        payload = {"state": "patient_contacted"}
        if rung is not None:
            payload["rung"] = rung
        ev = make_event(type_, actor, payload, AT + timedelta(days=30 + i))
        try:
            new = step(model, ev, PATHWAY)
        except InvalidTransition:
            continue
        check_step(model, ev, new)
        if type_ == "escalate":
            assert new.escalation_level == PATHWAY.rung_level(rung)
        model = new
    assert 0 <= model.escalation_level <= len(RUNGS)


@pytest.mark.parametrize(
    "event_payload,why",
    [
        ({"state": "owner_accepted", "due_at": (AT + timedelta(days=1)).isoformat()}, "wrong state"),
        ({"state": "patient_contacted", "due_at": (AT + timedelta(days=99)).isoformat()}, "not yet due"),
        ({"state": "patient_contacted", "due_at": "soon"}, "bad due_at"),
        ({"state": "patient_contacted"}, "no due_at"),
    ],
)
def test_mark_overdue_is_refused_when_it_would_be_false(event_payload, why):
    m = drive(*TO_CONTACTED)
    with pytest.raises(InvalidTransition):
        step(m, make_event("mark_overdue", "system:scheduler", event_payload, AT + timedelta(days=20)), PATHWAY)


def test_mark_overdue_twice_and_on_a_state_without_sla_is_refused():
    m = over_model()
    with pytest.raises(InvalidTransition):
        step(m, make_event("mark_overdue", "system:scheduler",
                           {"state": "patient_contacted", "due_at": AT.isoformat()}, AT + timedelta(days=30)), PATHWAY)
    withdrawn = drive(*TO_CONTACTED, ("consent_updated", "patient:p", {"withdrawn": True}))
    assert withdrawn.state == "consent_withdrawn"  # carries no SLA
    with pytest.raises(InvalidTransition):
        step(withdrawn, make_event("mark_overdue", "system:scheduler",
                                   {"state": "consent_withdrawn", "due_at": AT.isoformat()}, AT + timedelta(days=30)), PATHWAY)


def test_escalation_rungs_are_ordered_never_skipped_or_repeated():
    m = over_model()
    esc = lambda rung, actor="system:scheduler": make_event(  # noqa: E731
        "escalate", actor, {"rung": rung, "state": "patient_contacted"}, AT + timedelta(days=30))
    with pytest.raises(InvalidTransition):
        step(m, esc("supervisor"), PATHWAY)  # skipping the reminder
    m = step(m, esc("reminder"), PATHWAY)
    assert m.escalation_level == 1
    with pytest.raises(InvalidTransition):
        step(m, esc("reminder"), PATHWAY)  # repeat
    with pytest.raises(InvalidTransition):
        step(m, esc("coordinator"), PATHWAY)  # skip
    m = step(step(m, esc("supervisor"), PATHWAY), esc("coordinator"), PATHWAY)
    assert m.escalation_level == 3
    with pytest.raises(InvalidTransition):
        step(m, esc("regulator"), PATHWAY)  # the regulator rung is not built


def test_escalate_needs_an_overdue_case_in_the_named_state():
    m = drive(*TO_CONTACTED)
    with pytest.raises(InvalidTransition):
        step(m, make_event("escalate", "system:scheduler", {"rung": "reminder", "state": "patient_contacted"}), PATHWAY)


def test_only_a_human_in_the_target_role_accepts_a_rung_and_it_stops_the_ladder():
    m = over_model(rung_steps=2)  # supervisor rung emitted
    accept = lambda actor, **p: make_event("escalation_accepted", actor, p, AT + timedelta(days=40))  # noqa: E731
    for bad in ["system:scheduler", "watchdog", "model:drafter", "coordinator:co", OWNER, "patient:p"]:
        with pytest.raises(InvalidTransition):
            step(m, accept(bad), PATHWAY)
    accepted = step(m, accept("supervisor:su"), PATHWAY)
    assert accepted.escalation_accepted and accepted.escalation_level == 2
    with pytest.raises(InvalidTransition):  # no further rungs for this breach
        step(accepted, make_event("escalate", "system:scheduler", {"rung": "coordinator", "state": "patient_contacted"}), PATHWAY)
    with pytest.raises(InvalidTransition):  # not twice
        step(accepted, accept("supervisor:su2"), PATHWAY)
    with pytest.raises(InvalidTransition):  # not before any rung
        step(over_model(), accept("supervisor:su"), PATHWAY)
    # a new obligation (progress) starts a fresh ladder
    nxt = step(accepted, make_event("patient_acknowledge", "patient:p", {}, AT + timedelta(days=41)), PATHWAY)
    assert not nxt.escalation_accepted and nxt.escalation_level == 0


# ---- unit tests: side states --------------------------------------------------------------


@pytest.mark.parametrize("state", [s for s in OPEN if s != "consent_withdrawn"])
def test_consent_withdrawal_from_any_open_state_and_back(state):
    model = dataclasses.replace(start_model(), state=state, overdue=True, escalation_level=1)
    away = step(model, make_event("consent_updated", "patient:p", {"withdrawn": True}), PATHWAY)
    assert away.state == "consent_withdrawn" and away.consent_frozen
    assert away.resume_state == state
    assert away.overdue and away.escalation_level == 1  # the breach is not erased by withdrawing
    assert away.clock_started_at == model.clock_started_at
    back = step(away, make_event("consent_updated", "patient:p", {"withdrawn": False}), PATHWAY)
    assert back.state == state and not back.consent_frozen and back.resume_state is None
    assert back.overdue and back.clock_started_at == model.clock_started_at


def test_consent_updates_outside_withdrawal_are_annotations():
    m = drive(*TO_CONTACTED)
    again = step(m, make_event("consent_updated", "patient:p", {"withdrawn": False}), PATHWAY)
    assert again.state == m.state
    withdrawn = step(m, make_event("consent_updated", "patient:p", {"withdrawn": True}), PATHWAY)
    twice = step(withdrawn, make_event("consent_updated", "patient:p", {"withdrawn": True}), PATHWAY)
    assert twice.state == "consent_withdrawn" and twice.resume_state == "patient_contacted"
    # a lookalike value does not trigger the transition
    for v in (1, "true", None):
        assert step(m, make_event("consent_updated", "patient:p", {"withdrawn": v}), PATHWAY).state == m.state


def test_consent_cannot_be_updated_by_automation_or_on_a_closed_case():
    m = drive(*TO_CONTACTED)
    with pytest.raises(InvalidTransition):
        step(m, make_event("consent_updated", "system:scheduler", {"withdrawn": True}), PATHWAY)
    closed = dataclasses.replace(m, state="closed")
    with pytest.raises(InvalidTransition):
        step(closed, make_event("consent_updated", "patient:p", {"withdrawn": True}), PATHWAY)


def test_client_cannot_choose_where_a_case_resumes():
    away = step(drive(*TO_CONTACTED), make_event("consent_updated", "patient:p", {"withdrawn": True}), PATHWAY)
    back = step(
        away,
        make_event("consent_updated", "patient:p", {"withdrawn": False, "resume_state": "closed"}),
        PATHWAY,
    )
    assert back.state == "patient_contacted"


def test_referral_rejection_and_reroute():
    m = drive(*TO_CONTACTED, ("patient_acknowledge", "patient:p", {}),
              ("handoff_requested", OWNER, {"to_provider": "endo-1"}))
    with pytest.raises(InvalidTransition):  # reason is mandatory
        step(m, make_event("handoff_rejected", "specialist_owner:s", {}), PATHWAY)
    rej = step(m, make_event("handoff_rejected", "specialist_owner:s", {"reason": "capacity"}), PATHWAY)
    assert rej.state == "referral_rejected" and rej.owner == OWNER and rej.pending_handoff is None
    # re-route: the referrer asks again, the new provider accepts
    again = step(rej, make_event("handoff_requested", OWNER, {"to_provider": "endo-2"}), PATHWAY)
    via_accept = step(again, make_event("handoff_accepted", "specialist_owner:t", {}), PATHWAY)
    assert via_accept.state == "owner_accepted" and via_accept.owner == "specialist_owner:t"
    assert via_accept.provider == "endo-2"
    via_reassign = step(rej, make_event("reassign", "supervisor:su", {"owner": "primary_care_owner:z"}), PATHWAY)
    assert via_reassign.state == "owner_accepted" and via_reassign.owner == "primary_care_owner:z"
    with pytest.raises(InvalidTransition):
        step(rej, make_event("reassign", "supervisor:su", {}), PATHWAY)  # needs an owner
    with pytest.raises(InvalidTransition):  # nothing pending: nothing to accept
        step(rej, make_event("handoff_accepted", "specialist_owner:t", {}), PATHWAY)
    with pytest.raises(InvalidTransition):
        step(again, make_event("handoff_accepted", "system:scheduler", {}), PATHWAY)
    with pytest.raises(InvalidTransition):  # nothing pending in a state that could reject
        step(drive(*TO_CONTACTED), make_event("handoff_rejected", "specialist_owner:s", {"reason": "capacity"}), PATHWAY)


def test_lost_contact_after_n_failed_attempts_and_exit():
    m = drive(*TO_CONTACTED)
    for bad in ({}, {"failed_attempts": 2}, {"failed_attempts": "9"}, {"failed_attempts": True}):
        with pytest.raises(InvalidTransition):
            step(m, make_event("mark_lost_contact", "coordinator:co", bad), PATHWAY)
    lost = step(m, make_event("mark_lost_contact", "coordinator:co", {"failed_attempts": 3}), PATHWAY)
    assert lost.state == "lost_contact"
    again = step(lost, make_event("contact_patient", OWNER, {}), PATHWAY)
    assert again.state == "patient_contacted"
    with pytest.raises(InvalidTransition):  # lost_contact has no other way out
        step(lost, make_event("patient_acknowledge", "patient:p", {}), PATHWAY)
    with pytest.raises(InvalidTransition):  # only reachable from patient_contacted
        step(drive(*TO_CONTACTED[:2]), make_event("mark_lost_contact", "coordinator:co", {"failed_attempts": 5}), PATHWAY)


FULL_RUN = [
    *TO_CONTACTED,
    ("patient_acknowledge", "patient:p", {}),
    ("book_confirmatory", OWNER, {}),
    ("complete_confirmatory", "specialist_owner:s", {}),
    ("clinician_review", OWNER, {}),
    ("close", OWNER, dict(SIGNED)),
]


def test_reopen_needs_a_human_and_a_reason_and_cannot_skip_review():
    closed = drive(*FULL_RUN)
    assert closed.state == "closed"
    for actor, payload in [("system:scheduler", {"reason": "x"}), ("model:drafter", {"reason": "x"}),
                           (OWNER, {}), (OWNER, {"reason": ""})]:
        with pytest.raises(InvalidTransition):
            step(closed, make_event("reopen", actor, payload), PATHWAY)
    reopened = step(closed, make_event("reopen", OWNER, {"reason": "pathology amended"}), PATHWAY)
    assert reopened.state == "reopened"
    for payload in ({}, dict(SIGNED)):  # reopened cannot jump back to closed
        with pytest.raises(InvalidTransition):
            step(reopened, make_event("close", OWNER, payload), PATHWAY)
    reviewed = step(reopened, make_event("clinician_review", OWNER, {}), PATHWAY)
    assert reviewed.state == "clinician_reviewed"
    assert step(reviewed, make_event("close", OWNER, dict(SIGNED)), PATHWAY).state == "closed"
    with pytest.raises(InvalidTransition):  # reopen is only for closed cases
        step(drive(*TO_CONTACTED), make_event("reopen", OWNER, {"reason": "x"}), PATHWAY)


# (handoff_requested / handoff_accepted / handoff_rejected have preconditions: test_handoff.py)
ANNOTATIONS_WITHOUT_RULES = [
    "message_drafted", "message_approved", "message_sent",
    "callback_requested", "watchdog_flagged", "consent_updated",
]


@pytest.mark.parametrize(
    "state,type_",
    [
        (s, t)
        for s in OPEN
        for t in ANNOTATIONS_WITHOUT_RULES
    ],
)
def test_annotations_are_allowed_in_any_open_state_without_changing_it(state, type_):
    model = dataclasses.replace(start_model(), state=state)
    actor = "supervisor:su"
    payload = {"withdrawn": False} if type_ == "consent_updated" else {}
    if state == "consent_withdrawn" and type_ == "consent_updated":
        payload = {"withdrawn": True}  # (False would be the resume transition)
    new = step(model, make_event(type_, actor, payload), PATHWAY)
    assert new.state == state


@pytest.mark.parametrize("type_", [*PATHWAY.annotations, "reopen"])
def test_nothing_but_reopen_works_on_a_closed_case(type_):
    closed = dataclasses.replace(start_model(), state="closed")
    ok = type_ == "reopen"
    ev = make_event(type_, "supervisor:su", {"reason": "r"})
    if ok:
        assert step(closed, ev, PATHWAY).state == "reopened"
    else:
        with pytest.raises(InvalidTransition):
            step(closed, ev, PATHWAY)


def test_last_event_at_tracks_annotations_too():
    m = drive(*TO_CONTACTED)
    later = AT + timedelta(days=9)
    flagged = step(m, make_event("watchdog_flagged", "watchdog", {}, later), PATHWAY)
    assert flagged.last_event_at == later and flagged.state == m.state
    assert flagged.clock_started_at == m.clock_started_at


def test_owner_follows_accept_ownership():
    m = drive(("acknowledge", "lab:l", {}))
    assert m.owner is None
    assert drive(("accept_ownership", OWNER, {}), model=m).owner == OWNER


def test_unknown_and_genesis_events_are_not_applicable():
    for type_ in ("bogus_event", CASE_CREATED):
        with pytest.raises(InvalidTransition):
            step(start_model(), make_event(type_, "coordinator:co", {}), PATHWAY)


# ---- pathway loader validation ------------------------------------------------------------

BASE = """
pathway: p
version: 0.1
states: [a, b, closed]
side_states: [side]
transitions:
  - {from: a, event: go, to: b}
  - {from: b, event: close, to: closed, requires: [evidence_ref, signed_by]}
  - {from: b, event: park, to: side}
  - {from: side, event: unpark, to: b}
"""


def load_text(tmp_path, text):
    f = tmp_path / "p.yaml"
    f.write_text(text)
    return load_pathway(f)


def test_pathway_validation_rejects_unknown_state(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "pathway: p\nversion: 0.1\nstates: [a]\ntransitions:\n  - {from: a, event: e, to: nope}\n"
    )
    with pytest.raises(PathwayError):
        load_pathway(bad)


def test_minimal_valid_pathway_loads(tmp_path):
    pw = load_text(tmp_path, BASE)
    assert pw.open_states == ("a", "b", "side")


@pytest.mark.parametrize(
    "extra,fragment",
    [
        ("annotations: [close]\n", "closing event"),
        ("annotations: [go]\n", "main-path"),
        ("annotations: [note, note]\n", "twice"),
        ("human_only_events: [nothing]\n", "unknown event"),
        ("  - {from: side, event: sneak, to: closed, requires: [evidence_ref, signed_by]}\n", "only 'b'"),
        ("  - {from: a, event: sneak2, to: closed, requires: [evidence_ref, signed_by]}\n", "only 'b'"),
        ("  - {from: b, event: sneak3, to: closed}\n", "evidence_ref and signed_by"),
        ("  - {from: b, event: sneak4, to: closed, requires: [evidence_ref]}\n", "evidence_ref and signed_by"),
        ("  - {from: a, event: back, to: resume_state}\n", "resume_state"),
        ("side_states: [side, orphan]\n", "cannot be entered"),
    ],
)
def test_loader_refuses_configs_that_could_weaken_closure(tmp_path, extra, fragment):
    if extra.startswith("  - "):
        text = BASE + extra
    elif extra.startswith("side_states"):
        text = BASE.replace("side_states: [side]\n", extra)
    else:
        text = BASE + extra
    with pytest.raises(PathwayError, match=fragment):
        load_text(tmp_path, text)


def test_loader_refuses_a_side_state_that_cannot_be_left(tmp_path):
    text = BASE.replace("  - {from: side, event: unpark, to: b}\n", "")
    with pytest.raises(PathwayError, match="cannot be left"):
        load_text(tmp_path, text)


def test_loader_ladder_validation(tmp_path):
    ladder = (
        "escalation_ladder:\n"
        "  - {rung: r1, after_breach_days: 3, notify: o, target: o, action: a, accepted_by: [x]}\n"
        "  - {rung: r2, after_breach_days: 1, notify: o, target: o, action: a, accepted_by: [x]}\n"
    )
    with pytest.raises(PathwayError, match="non-decreasing"):
        load_text(tmp_path, BASE + ladder)


def test_any_open_and_resume_expansion_on_the_real_config():
    sources = {t.from_state for t in PATHWAY.transitions
               if t.event == "consent_updated" and t.to_state == "consent_withdrawn"}
    assert sources == set(OPEN) - {"consent_withdrawn"}
    assert PATHWAY.resumable_states == {"consent_withdrawn"}
    assert "closed" not in sources


def test_real_config_loads_every_documented_event():
    expected = {
        "mark_overdue", "escalate", "escalation_accepted", "handoff_requested",
        "handoff_accepted", "handoff_rejected", "consent_updated", "message_drafted",
        "message_approved", "message_sent", "callback_requested", "watchdog_flagged",
        "reopen", "mark_lost_contact", "reassign",
    }
    assert expected <= set(PATHWAY.event_types)
    assert PATHWAY.sla_for_state("patient_contacted") == ("patient_acknowledge", 7)
    assert PATHWAY.sla_for_state("lost_contact") == ("contact_patient", 5)
    assert PATHWAY.sla_for_state("closed") is None and PATHWAY.sla_for_state("consent_withdrawn") is None
    assert PATHWAY.sla_for_state("referral_rejected") == ("reassign", 2)  # re-route a bounced referral (A5)
