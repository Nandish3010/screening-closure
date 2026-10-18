"""A3 of the engineering review: ONE default-deny table says which actor may write which event type
(`state_machine.ACTOR_EVENTS`), and the state machine applies it first thing in `step`.

The grid below is exhaustive: every actor prefix (and a set of actors that match no prefix) against every event type the
system knows, in every state of the pathway. A pair that is not in the table is refused whatever the state or the payload;
a pair that is in the table is never refused BY THE TABLE (other rules may still refuse it). Nothing else decides who may
write what: the per-actor allow-lists that used to live in the pathway YAML, the sweep, the consent module and the
precheck are gone, and `human_only_events` is only checked against the table here."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app import auth, effort, payloads, policy, sweep, watchdog
from app.config import settings as app_settings
from app.events import Event
from app.pathway import load_pathway
from app.state_machine import (
    ACTOR_EVENTS,
    AUTOMATION_ACTOR,
    CASE_CREATED,
    CONSENT_ACTOR,
    HUMAN_EVENTS,
    HUMAN_ROLES,
    NOTIFIER_ACTOR,
    SCHEDULER_ACTOR_NAME,
    TRIAGE_ACTOR,
    WATCHDOG_ACTOR,
    CaseModel,
    InvalidTransition,
    actor_key,
    allowed_events,
    step,
)

PATHWAY = load_pathway(app_settings.pathway_path)
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
CASE = uuid4()
STATES = [*PATHWAY.states, *PATHWAY.side_states]

# One concrete actor for every row of the table, and actors that match no row (they must be refused everything).
ROW_ACTORS = {key: (key if not key.endswith(":") else f"{key}x1") for key in ACTOR_EVENTS}
UNKNOWN_ACTORS = ["clinician:dr_a", "watchdog", "scheduler", "system:intake", "system:", "system:automation2",
                  "system:scheduler:x", "system:agency:", "model:", "lab:", ":x", "", "automation", "nurse:n1",
                  "SYSTEM:AUTOMATION", "system:Automation"]
EVENT_TYPES = sorted({*PATHWAY.event_types, *policy.EVENT_POLICY, *payloads.SCHEMAS, CASE_CREATED, "bogus_event"})


def event(type_: str, actor: str) -> Event:
    return Event(CASE, type_, AT, actor, {})


def refused_by_the_table(model: CaseModel, type_: str, actor: str) -> bool:
    try:
        step(model, event(type_, actor), PATHWAY)
    except InvalidTransition as exc:
        return "actor table" in str(exc)
    return False


def test_the_table_has_exactly_the_documented_rows():
    assert set(ACTOR_EVENTS) == {
        *(f"{role}:" for role in HUMAN_ROLES), "system:scheduler", "system:watchdog", "system:notifier",
        "system:consent", "system:automation", "model:", "system:triage", "system:agency:",
    }
    assert ACTOR_EVENTS[TRIAGE_ACTOR] == frozenset() and ACTOR_EVENTS["system:agency:"] == frozenset()  # placeholders
    assert ACTOR_EVENTS[CONSENT_ACTOR] == {"review_requested"}
    assert ACTOR_EVENTS[WATCHDOG_ACTOR] == {"watchdog_flagged"}
    assert ACTOR_EVENTS[NOTIFIER_ACTOR] == {"message_sent"}
    assert ACTOR_EVENTS["model:"] == {"message_drafted"}
    assert ACTOR_EVENTS[SCHEDULER_ACTOR_NAME] == {"mark_overdue", "escalate", "handoff_rejected"}
    assert ACTOR_EVENTS[AUTOMATION_ACTOR] == {
        "acknowledge", "assign_ownership", "complete_confirmatory", "closure_proposed", "handoff_requested",
        "message_sent", "review_requested"}
    assert all(isinstance(v, frozenset) for v in ACTOR_EVENTS.values())


@pytest.mark.parametrize("state", STATES)
def test_only_whitelisted_actor_event_pairs_are_accepted_in_every_state(state):
    model = dataclasses.replace(CaseModel(state=state))
    checked = 0
    for key, actor in ROW_ACTORS.items():
        assert actor_key(actor) == key
        for type_ in EVENT_TYPES:
            whitelisted = type_ in ACTOR_EVENTS[key]
            assert whitelisted == (type_ in allowed_events(actor))
            refused = refused_by_the_table(model, type_, actor)
            assert refused == (not whitelisted), (state, actor, type_)
            checked += 1
    for actor in UNKNOWN_ACTORS:
        assert actor_key(actor) is None and allowed_events(actor) == frozenset(), actor
        for type_ in EVENT_TYPES:
            assert refused_by_the_table(model, type_, actor), (state, actor, type_)
            checked += 1
    assert checked == (len(ROW_ACTORS) + len(UNKNOWN_ACTORS)) * len(EVENT_TYPES) > 800


def test_a_whitelisted_pair_is_accepted_somewhere_for_every_service_row():
    """The grid would pass if every row were refused for another reason: for each service row, one event that the machine
    does accept, to show the table is let-through as well as keep-out."""
    ok = {
        "system:scheduler": ("handoff_rejected", {"reason": PATHWAY.handoff_bounce_reason, "bounced": True,
                                                  "to_provider": "X"}, "patient_acknowledged"),
        "system:watchdog": ("watchdog_flagged", {"reason": "idle"}, "owner_accepted"),
        "system:automation": ("acknowledge", {"auto": True}, "result_received"),
        "model:drafter": ("message_drafted", {}, "owner_accepted"),
    }
    for actor, (type_, payload, state) in ok.items():
        model = CaseModel(state=state, pending_handoff={"to_provider": "X", "requested_by": "p:1", "from_owner": "p:1",
                                                        "requested_at": AT.isoformat(), "requested_event_id": "e"})
        new = step(model, Event(CASE, type_, AT, actor, payload), PATHWAY)
        assert new.last_event_at == AT


def test_a_person_is_listed_once_per_role_with_the_same_events_and_the_policy_stays_within_them():
    assert HUMAN_ROLES == auth.USER_ROLES == effort.HUMAN_ROLES
    for role in HUMAN_ROLES:
        assert ACTOR_EVENTS[f"{role}:"] == HUMAN_EVENTS
    # every event a person's role may post (app.policy.EVENT_POLICY) is one the machine lets a person write ...
    for type_, roles in policy.EVENT_POLICY.items():
        if set(roles) & set(HUMAN_ROLES):
            assert type_ in HUMAN_EVENTS, type_
    # ... and an event a person may write that no role may post is one a dedicated route writes (the approval routes)
    posted_by_someone = {t for t, roles in policy.EVENT_POLICY.items() if set(roles) & set(HUMAN_ROLES)}
    assert HUMAN_EVENTS - posted_by_someone == {"message_approved", "message_rejected"}
    # the service roles: what the policy lets a service token post is inside that service's row
    assert {t for t, roles in policy.EVENT_POLICY.items() if "scheduler" in roles} <= ACTOR_EVENTS[SCHEDULER_ACTOR_NAME]
    assert {t for t, roles in policy.EVENT_POLICY.items() if "watchdog" in roles} <= ACTOR_EVENTS[WATCHDOG_ACTOR]
    assert not {t for t, roles in policy.EVENT_POLICY.items() if "automation" in roles}


def test_the_events_only_a_service_writes_are_never_a_persons():
    service_only = set(EVENT_TYPES) - HUMAN_EVENTS - {CASE_CREATED, "bogus_event"}
    assert service_only == {"mark_overdue", "assign_ownership", "closure_proposed", "review_requested", "message_sent",
                            "watchdog_flagged"}
    for role in HUMAN_ROLES:
        for type_ in service_only:
            assert type_ not in allowed_events(f"{role}:u1")


def test_no_service_row_holds_a_decision_that_only_a_person_makes():
    human_only = set(PATHWAY.human_only_events)
    assert human_only, "the pathway lists the human decisions"
    for key, events in ACTOR_EVENTS.items():
        if key.rstrip(":") not in HUMAN_ROLES:
            assert not events & human_only, (key, sorted(events & human_only))
    assert human_only <= HUMAN_EVENTS


def test_the_actor_names_in_the_table_are_the_ones_the_services_use():
    assert sweep.SCHEDULER_ACTOR == SCHEDULER_ACTOR_NAME
    assert sweep.SCHEDULER_EVENT_TYPES == ACTOR_EVENTS[SCHEDULER_ACTOR_NAME]
    assert watchdog.WATCHDOG_ACTOR == WATCHDOG_ACTOR
    assert auth.Identity("service", "scheduler", "scheduler").actor == SCHEDULER_ACTOR_NAME
    assert auth.Identity("service", "watchdog", "watchdog").actor == WATCHDOG_ACTOR
    assert auth.Identity("service", "automation", "automation").actor == AUTOMATION_ACTOR
    assert auth.Identity("user", "coordinator", "Maryam").actor.split(":")[0] in HUMAN_ROLES


def test_the_table_is_read_when_an_event_is_applied_so_a_branch_can_fill_its_placeholder(monkeypatch):
    """What branches cut-triage and cut-agency do: give their placeholder row its events. Until then, nothing."""
    model = CaseModel(state="owner_accepted")
    flag = Event(CASE, "review_requested", AT, TRIAGE_ACTOR, {"reason": "opt_out", "source_event_id": str(uuid4())})
    with pytest.raises(InvalidTransition, match="actor table"):
        step(model, flag, PATHWAY)
    monkeypatch.setitem(ACTOR_EVENTS, TRIAGE_ACTOR, frozenset({"review_requested"}))
    assert step(model, flag, PATHWAY).outreach_holds == ("opt_out",)
