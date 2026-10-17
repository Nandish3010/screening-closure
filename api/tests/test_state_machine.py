"""Moat 1: the closure invariant, proven by property test.

(a) A case is never in `closed` unless a `close` event carrying evidence_ref AND
    signed_by (from a non-automated actor) was applied.
(b) Replaying the same events twice yields identical state.
"""

import dataclasses
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.config import settings as app_settings
from app.events import CASE_CREATED, Event, InMemoryEventStore, replay
from app.pathway import PathwayError, load_pathway
from app.state_machine import InvalidTransition, apply

PATHWAY = load_pathway(app_settings.pathway_path)
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
CASE = uuid4()

HAPPY_PATH = [t.event for t in PATHWAY.transitions]
ALL_EVENT_TYPES = [*PATHWAY.event_types, "bogus_event", CASE_CREATED]
ACTORS = ["clinician:dr_a", "coordinator:b", "system:scheduler", "watchdog", "model:drafter"]
payloads = st.fixed_dictionaries(
    {},
    optional={
        "evidence_ref": st.sampled_from(["", "DiagnosticReport/1", None]),
        "signed_by": st.sampled_from(["", "Practitioner/9", None]),
    },
)


def make_event(type_: str, actor: str, payload: dict) -> Event:
    return Event(CASE, type_, AT, actor, payload)


events_strategy = st.builds(
    make_event, st.sampled_from(ALL_EVENT_TYPES), st.sampled_from(ACTORS), payloads
)


@st.composite
def event_sequences(draw):
    """Random noise, optionally preceded by a valid prefix of the happy path so
    the search actually reaches late states (including `closed`)."""
    prefix_len = draw(st.integers(0, len(HAPPY_PATH)))
    prefix = [
        make_event(
            ev,
            draw(st.sampled_from(ACTORS)),
            draw(
                st.just({"evidence_ref": "DiagnosticReport/1", "signed_by": "Practitioner/9"})
                | payloads
            ),
        )
        for ev in HAPPY_PATH[:prefix_len]
    ]
    noise = draw(st.lists(events_strategy, max_size=15))
    return prefix + noise


def fold(events):
    """Apply events, skipping invalid ones (as the API does by returning 409)."""
    state = PATHWAY.initial_state
    applied = []
    for ev in events:
        if ev.type == CASE_CREATED:
            continue
        try:
            state = apply(state, ev, PATHWAY)
        except InvalidTransition:
            continue
        applied.append(ev)
    return state, applied


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


@settings(max_examples=500, deadline=None)
@given(event_sequences())
def test_replay_is_deterministic(events):
    _, applied = fold(events)
    assert replay(applied, PATHWAY) == replay(applied, PATHWAY)

    store = InMemoryEventStore()
    case_id = uuid4()
    store.create_case(
        case_id, PATHWAY.name, Event(case_id, CASE_CREATED, AT, "system:intake"),
        PATHWAY.initial_state,
    )
    for ev in applied:
        store.append(dataclasses.replace(ev, case_id=case_id), PATHWAY)
    expected = fold(applied)[0]
    assert store.current_state(case_id) == expected
    assert replay(store.events_for(case_id), PATHWAY) == expected


def test_every_pathway_transition_is_reachable_in_order():
    state = PATHWAY.initial_state
    for t in PATHWAY.transitions:
        payload = {"evidence_ref": "DiagnosticReport/1", "signed_by": "Practitioner/9"}
        state = apply(state, make_event(t.event, "clinician:dr_a", payload), PATHWAY)
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
    with pytest.raises(InvalidTransition):
        apply("clinician_reviewed", make_event("close", "system:scheduler", payload), PATHWAY)


def test_pathway_validation_rejects_unknown_state(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "pathway: p\nversion: 0.1\nstates: [a]\ntransitions:\n  - {from: a, event: e, to: nope}\n"
    )
    with pytest.raises(PathwayError):
        load_pathway(bad)
