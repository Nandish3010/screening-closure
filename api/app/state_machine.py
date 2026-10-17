"""Pure state-machine functions. No I/O, no clock, no randomness: same input, same output.

`step(model, event, pathway)` folds one event into the case's read model and raises
`InvalidTransition` (changing nothing) if the event is not allowed. The read model is what a
replay of the event log produces:

    state                main or side state ("closed" is terminal)
    overdue              the current state's SLA is breached and not yet resolved
    escalation_level     rungs of the escalation ladder emitted for the current breach
    escalation_accepted  a human accepted the latest rung (no further rungs for this breach)
    owner                actor of the latest accept_ownership / handoff_accepted / reassign
    provider             the provider the owner acts for (set by handoff_accepted, or by
                         accept_ownership / reassign when the payload names one)
    pending_handoff      an unanswered handoff request {to_provider, requested_by, from_owner,
                         requested_at}; ownership changes only when it is accepted (A5)
    last_handoff         outcome of the latest answered or bounced handoff (A5)
    consent_frozen       outreach is frozen (state is consent_withdrawn)
    last_event_at        `at` of the latest event, including annotations
    resume_state         where the case returns to when consent is restored (side state only)
    clock_started_at     when the current state's SLA clock started
    breach_due_at        the due date of the open breach (set with overdue)

`overdue` and `escalation_level` are flags, not states, because a case can be overdue AND in
patient_contacted (ADR-011). Side states that change the path are real states.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.pathway import RESUME_STATE, Pathway, Transition

if TYPE_CHECKING:  # avoid a circular import; events.py imports this module
    from app.events import Event

CASE_CREATED = "case_created"  # genesis marker; does not change state
CONSENT_WITHDRAWN = "consent_withdrawn"

SCHEDULER_ACTOR_NAME = "system:scheduler"

# Actor prefixes that denote automation (scheduler, watchdog, drafting model...).
AUTOMATED_ACTOR_PREFIXES = ("system:", "scheduler", "watchdog", "model:")


class InvalidTransition(Exception):
    pass


class PayloadError(InvalidTransition):
    """The event's payload is malformed (the API answers 422; a store answers like any refusal)."""


def is_automated(actor: str) -> bool:
    return actor.startswith(AUTOMATED_ACTOR_PREFIXES)


def actor_role(actor: str) -> str:
    """Actors are `role:name`; this returns the role part."""
    return actor.split(":", 1)[0]


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


@dataclass(frozen=True)
class CaseModel:
    state: str
    overdue: bool = False
    escalation_level: int = 0
    escalation_accepted: bool = False
    owner: str | None = None
    consent_frozen: bool = False
    last_event_at: datetime | None = None
    resume_state: str | None = None
    clock_started_at: datetime | None = None
    breach_due_at: datetime | None = None
    provider: str | None = None
    pending_handoff: dict | None = None
    last_handoff: dict | None = None

    def to_json(self) -> dict:
        def iso(dt):
            return dt.isoformat() if dt is not None else None

        return {
            "state": self.state,
            "overdue": self.overdue,
            "escalation_level": self.escalation_level,
            "escalation_accepted": self.escalation_accepted,
            "owner": self.owner,
            "consent_frozen": self.consent_frozen,
            "last_event_at": iso(self.last_event_at),
            "resume_state": self.resume_state,
            "clock_started_at": iso(self.clock_started_at),
            "breach_due_at": iso(self.breach_due_at),
            "provider": self.provider,
            "pending_handoff": dict(self.pending_handoff) if self.pending_handoff else None,
            "last_handoff": dict(self.last_handoff) if self.last_handoff else None,
        }


def initial_model(pathway: Pathway) -> CaseModel:
    return CaseModel(state=pathway.initial_state)


def genesis(model: CaseModel, event: Event) -> CaseModel:
    """The case_created marker starts the first state's SLA clock; it changes no state."""
    at = _aware(event.at)
    return replace(model, last_event_at=at, clock_started_at=at)


def replay_model(events, pathway: Pathway) -> CaseModel:
    """Deterministically fold events into the read model, from the pathway's initial state."""
    model = initial_model(pathway)
    for ev in events:
        model = genesis(model, ev) if ev.type == CASE_CREATED else step(model, ev, pathway)
    return model


def step(model: CaseModel, event: Event, pathway: Pathway) -> CaseModel:
    """Return the new read model, or raise InvalidTransition. Never mutates anything."""
    if event.type in pathway.human_only_events and is_automated(event.actor):
        raise InvalidTransition(f"event {event.type!r} requires a human actor")
    _precheck(model, event, pathway)

    candidates = pathway.transitions_for(model.state, event.type)
    chosen = next((t for t in candidates if t.matches(event.payload)), None)
    if chosen is not None:
        return _finish(_transition(model, event, pathway, chosen), event, model)
    if event.type in pathway.annotations and model.state in pathway.open_states:
        return _finish(_annotate(model, event, pathway), event, model)
    if candidates:
        raise InvalidTransition(
            f"event {event.type!r} from state {model.state!r}: payload does not satisfy "
            "the transition's conditions"
        )
    raise InvalidTransition(f"event {event.type!r} not allowed from state {model.state!r}")


def apply(state: str, event: Event, pathway: Pathway) -> str:
    """State-only convenience over `step` for callers that hold no read model.

    Annotation rules that need history (escalation, resume) will refuse here; the stores
    use `step` with the replayed model.
    """
    return step(CaseModel(state=state), event, pathway).state


# -- transitions ------------------------------------------------------------------------


def _transition(model: CaseModel, event: Event, pathway: Pathway, t: Transition) -> CaseModel:
    # Closure guard: evidence AND human sign-off, never an automated actor.
    if t.to_state == pathway.terminal_state and is_automated(event.actor):
        raise InvalidTransition("automated actors may not close a case")
    for key in t.requires:
        if not event.payload.get(key):
            raise InvalidTransition(f"event {event.type!r} requires non-empty {key!r}")
    for key, minimum in t.minimums:
        value = event.payload.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
            raise InvalidTransition(f"event {event.type!r} requires {key!r} >= {minimum}")

    resuming = t.to_state == RESUME_STATE
    if resuming:
        if model.resume_state is None:
            raise InvalidTransition(f"event {event.type!r}: no earlier state to resume")
        target = model.resume_state
    else:
        target = t.to_state

    resumable = pathway.resumable_states
    new = replace(model, state=target, consent_frozen=target == CONSENT_WITHDRAWN)
    new = replace(new, resume_state=model.state if target in resumable else None)
    # The SLA clock, overdue flag and ladder position belong to the SLA subject. They
    # carry over an excursion into a resumable side state and back, and restart on any
    # other state change (a new state is a new obligation).
    keeps_clock = target in resumable or (resuming and model.state in resumable)
    if not keeps_clock:
        new = replace(
            new,
            overdue=False,
            escalation_level=0,
            escalation_accepted=False,
            breach_due_at=None,
            clock_started_at=_aware(event.at),
        )
    return new


# -- annotations ------------------------------------------------------------------------


def _annotate(model: CaseModel, event: Event, pathway: Pathway) -> CaseModel:
    rule = _ANNOTATION_RULES.get(event.type)
    return rule(model, event, pathway) if rule else model


def _require_current_state(model: CaseModel, event: Event) -> None:
    named = event.payload.get("state")
    if named != model.state:
        raise InvalidTransition(
            f"event {event.type!r} names state {named!r} but the case is in {model.state!r}"
        )


def _mark_overdue(model: CaseModel, event: Event, pathway: Pathway) -> CaseModel:
    _require_current_state(model, event)
    if pathway.sla_for_state(model.state) is None:
        raise InvalidTransition(f"state {model.state!r} has no SLA to breach")
    if model.overdue:
        raise InvalidTransition("case is already overdue for this state")
    raw = event.payload.get("due_at")
    try:
        due = _aware(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
    except ValueError:
        raise InvalidTransition(f"mark_overdue needs an ISO due_at, got {raw!r}") from None
    if _aware(event.at) <= due:
        raise InvalidTransition("mark_overdue before the due date")
    return replace(model, overdue=True, breach_due_at=due)


def _escalate(model: CaseModel, event: Event, pathway: Pathway) -> CaseModel:
    _require_current_state(model, event)
    if not model.overdue:
        raise InvalidTransition("cannot escalate a case that is not overdue")
    if model.escalation_accepted:
        raise InvalidTransition("a human accepted the latest rung: no further rungs for this breach")
    rung = pathway.rung(str(event.payload.get("rung")))
    if rung is None:
        raise InvalidTransition(f"unknown escalation rung {event.payload.get('rung')!r}")
    level = pathway.rung_level(rung.rung)
    if level != model.escalation_level + 1:
        raise InvalidTransition(
            f"rung {rung.rung!r} is level {level}; the next rung is level "
            f"{model.escalation_level + 1} (rungs are never skipped or repeated)"
        )
    target = event.payload.get("target")
    if target is not None and target != rung.target:
        raise InvalidTransition(f"rung {rung.rung!r} targets {rung.target!r}, not {target!r}")
    return replace(model, escalation_level=level)


def _escalation_accepted(model: CaseModel, event: Event, pathway: Pathway) -> CaseModel:
    rung = pathway.rung_at(model.escalation_level)
    if rung is None:
        raise InvalidTransition("no escalation rung to accept")
    if model.escalation_accepted:
        raise InvalidTransition(f"rung {rung.rung!r} is already accepted")
    if actor_role(event.actor) not in rung.accepted_by:
        raise InvalidTransition(
            f"rung {rung.rung!r} is accepted by {list(rung.accepted_by)}, "
            f"not by {actor_role(event.actor)!r}"
        )
    named = event.payload.get("rung")
    if named is not None and named != rung.rung:
        raise InvalidTransition(f"the latest rung is {rung.rung!r}, not {named!r}")
    return replace(model, escalation_accepted=True)


# Annotations without an entry here have no effect on the read model beyond last_event_at
# (handoff_requested, message_*, callback_requested, watchdog_flagged, consent_updated
# outside the withdrawn transitions).
_ANNOTATION_RULES = {
    "mark_overdue": _mark_overdue,
    "escalate": _escalate,
    "escalation_accepted": _escalation_accepted,
}


# -- payload validation and the handoff rules (A5) ------------------


def validate_payload(event: Event, pathway: Pathway) -> None:
    """Raise PayloadError if the event's payload is malformed for its type. Pure; the API calls
    it first so a malformed payload is a 422, and `step` calls it again so no store can skip it."""
    payload, actor, kind = event.payload, event.actor, event.type
    if kind == "handoff_requested":
        provider = payload.get("to_provider")
        if not isinstance(provider, str) or not provider.strip():
            raise PayloadError("handoff_requested needs a non-empty string 'to_provider'")
    elif kind == "handoff_rejected":
        reason = payload.get("reason")
        if reason == pathway.handoff_bounce_reason:
            if actor != SCHEDULER_ACTOR_NAME or payload.get("bounced") is not True:
                raise PayloadError(
                    f"reason {reason!r} is reserved for the scheduler's bounce "
                    "(actor system:scheduler with bounced: true)"
                )
        else:
            if actor == SCHEDULER_ACTOR_NAME:
                raise PayloadError(
                    f"the scheduler may only reject a handoff with reason "
                    f"{pathway.handoff_bounce_reason!r}"
                )
            if reason not in pathway.handoff_reject_reasons:
                raise PayloadError(
                    f"handoff_rejected reason must be one of {list(pathway.handoff_reject_reasons)}, "
                    f"got {reason!r}"
                )
    elif kind == "handoff_accepted":
        provider = payload.get("provider")
        if provider is not None and (not isinstance(provider, str) or not provider.strip()):
            raise PayloadError("handoff_accepted 'provider', if given, must be a non-empty string")


def _precheck(model: CaseModel, event: Event, pathway: Pathway) -> None:
    kind, actor = event.type, event.actor
    validate_payload(event, pathway)
    pending = model.pending_handoff
    if kind == "handoff_requested":
        if model.owner is None:
            raise InvalidTransition("handoff_requested: the case has no owner to hand over from")
        if pending is not None:
            raise InvalidTransition(
                f"a handoff to {pending['to_provider']!r} is already pending; "
                "it must be answered or bounce first"
            )
    elif kind in ("handoff_accepted", "handoff_rejected"):
        if pending is None:
            raise InvalidTransition(f"{kind}: there is no pending handoff request")
        if kind == "handoff_accepted" and actor in (pending["requested_by"], pending["from_owner"]):
            raise InvalidTransition("a handoff cannot be accepted by the party that requested it")


def _finish(model: CaseModel, event: Event, before: CaseModel | None = None) -> CaseModel:
    """Bookkeeping common to every accepted event: last_event_at, ownership, handoff, consent."""
    kind, payload, at = event.type, event.payload, _aware(event.at)
    out = replace(model, last_event_at=at)
    if kind == "accept_ownership":
        out = replace(out, owner=event.actor, provider=payload.get("provider"))
    elif kind == "reassign":
        out = replace(out, owner=str(payload["owner"]), provider=payload.get("provider"),
                      pending_handoff=None)
    elif kind == "handoff_requested":
        out = replace(out, pending_handoff={
            "to_provider": payload["to_provider"].strip(),
            "requested_by": event.actor,
            "from_owner": model.owner,
            "requested_at": at.isoformat(),
        })
    elif kind == "handoff_accepted":
        pending = (before or model).pending_handoff
        provider = (payload.get("provider") or pending["to_provider"]).strip()
        out = replace(out, owner=event.actor, provider=provider, pending_handoff=None,
                      last_handoff={"outcome": "accepted", "to_provider": pending["to_provider"],
                                    "by": event.actor, "at": at.isoformat()})
    elif kind == "handoff_rejected":
        pending = (before or model).pending_handoff
        out = replace(out, pending_handoff=None, last_handoff={
            "outcome": "rejected", "reason": payload["reason"],
            "bounced": payload.get("bounced") is True, "to_provider": pending["to_provider"],
            "by": event.actor, "at": at.isoformat(),
        })
    return out
