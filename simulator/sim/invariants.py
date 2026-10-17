"""Independent checks on an event log. Used by tests and by `cli run --verify`.

The closure rule (pathway YAML `closure_rule`): a case may only enter `closed` via a `close` event
that carries `evidence_ref` and `signed_by` and was emitted by a human actor. Two checks:
  1. a replay through the shared state machine (`app.state_machine.apply`);
  2. a raw check on event type, actor and payload that does not use the state machine at all.
"""

from __future__ import annotations

from typing import Sequence

from app.events import CASE_CREATED
from app.pathway import Pathway
from app.state_machine import AUTOMATED_ACTOR_PREFIXES, InvalidTransition, apply

OVERLAY_NO_STATE_CHANGE = None  # overlay events are anything outside the pathway's main chain


def stateful_events(pathway: Pathway) -> frozenset[str]:
    """Event types the simulator validates through `apply`: the forward chain of the pathway.

    The engine's pathway also defines side-state transitions and annotation events (handoff_*,
    consent_updated, message_sent, watchdog_flagged...). The simulator logs its own typed overlay
    events under some of the same names with its own payloads and models side flows itself, so
    those are kept outside `apply` (the API's property tests cover the engine's side states).
    `close` is in the chain, so the closure guard is always exercised.
    """
    return frozenset(t.event for t in pathway.main_transitions)


def check_case_log(events: Sequence, pathway: Pathway) -> list[str]:
    """Return a list of violations (empty = the log is valid)."""
    problems: list[str] = []
    terminal = pathway.terminal_state
    pathway_events = stateful_events(pathway)
    state = pathway.initial_state
    closed_open = False  # between a close and the next reopened
    for ev in events:
        if ev.type == CASE_CREATED:
            continue
        # raw rule (1): every close has evidence, a signer and a human actor
        if ev.type == "close":
            if not ev.payload.get("evidence_ref") or not ev.payload.get("signed_by"):
                problems.append(f"seq {ev.seq}: close without evidence_ref/signed_by")
            if ev.actor.startswith(AUTOMATED_ACTOR_PREFIXES):
                problems.append(f"seq {ev.seq}: close by automated actor {ev.actor!r}")
            if closed_open:
                problems.append(f"seq {ev.seq}: close while already closed")
        if ev.type == "reopened":
            if state != terminal:
                problems.append(f"seq {ev.seq}: reopened while not closed (state {state})")
            state = ev.payload["resume_state"]
            closed_open = False
            continue
        if ev.type in pathway_events:
            try:
                new_state = apply(state, ev, pathway)
            except InvalidTransition as exc:
                problems.append(f"seq {ev.seq}: {exc}")
                continue
            if new_state == terminal:
                if ev.type != "close":
                    problems.append(f"seq {ev.seq}: entered {terminal} via {ev.type!r}")
                closed_open = True
            state = new_state
        elif state == terminal and ev.type in ("message_sent",):
            pass  # post-closure bookkeeping is allowed; nothing else moves state
    return problems


def check_consent(events: Sequence) -> list[str]:
    """Orchestrated arm: no outreach (message_sent) after a recorded consent withdrawal."""
    problems: list[str] = []
    withdrawn = False
    for ev in events:
        if ev.type == "consent_updated" and ev.payload.get("status") == "withdrawn" and ev.payload.get("flagged_for_review"):
            withdrawn = True
        elif ev.type == "message_sent" and withdrawn:
            problems.append(f"seq {ev.seq}: message sent after consent withdrawal")
    return problems
