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
                         requested_at, requested_event_id}; ownership changes only when it is accepted (A5)
    last_handoff         outcome of the latest answered or bounced handoff (A5)
    consent_frozen       outreach is frozen (state is consent_withdrawn)
    last_event_at        `at` of the latest event, including annotations
    last_activity_at     `at` of the latest event that is not a watchdog flag: the idle clock (A11)
    last_activity_event_id  the `event_id` of that event: what the watchdog derives its flag id from, so a
                         later episode that starts at the same instant is still a different episode (SEC-2)
    watchdog_flag        {reason, idle_days, at} while a watchdog_flagged is the newest word on the
                         case, None as soon as any other event arrives (A9, A11)
    resume_state         where the case returns to when consent is restored (side state only)
    clock_started_at     when the current state's SLA clock started: the event that entered the
                         state, or, for an SLA with `clock_start: handoff_accepted` (A13), the
                         latest referral acceptance when there was one
    clock_started_event_id  the `event_id` of that event. The scheduler derives the ids of `mark_overdue`
                         and the escalation rungs from it (never from a timestamp, SEC-2): re-entering a
                         state is a new clock and so a new breach, even at the very same instant
    referral_accepted_at `at` of the latest handoff_accepted (A13), None before any
    breach_due_at        the due date of the open breach (set with overdue)
    backdated_events     how many accepted events had an `at` earlier than the previous event's `at`
                         (T-F4, ADR-027): a user token's backdated event is refused at the API (SEC-2); only the
                         service actors that legitimately post late (the sweep, the notifier) can still produce
                         one, and it is accepted, ordered by `seq`, and counted

`overdue` and `escalation_level` are flags, not states, because a case can be overdue AND in
patient_contacted (ADR-011). Side states that change the path are real states.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from app.pathway import CLOCK_HANDOFF_ACCEPTED, RESUME_STATE, Pathway, Transition

if TYPE_CHECKING:  # avoid a circular import; events.py imports this module
    from app.events import Event

CASE_CREATED = "case_created"  # genesis marker; does not change state
CONSENT_WITHDRAWN = "consent_withdrawn"

# Consent ledger (A6). Keys a `consent_updated` payload may carry (all booleans, any subset).
CONSENT_KEYS = (
    "channel_sms",
    "channel_whatsapp",
    "channel_phone",
    "channel_letter",
    "share_with_specialist",
    "withdrawn",
)
# Programme default before the patient says anything: contact on every channel and sharing
# with the specialist are allowed (opt-out model; ADR-017). Nothing has been recorded yet.
DEFAULT_CONSENT = {
    "channel_sms": True,
    "channel_whatsapp": True,
    "channel_phone": True,
    "channel_letter": True,
    "share_with_specialist": True,
    "withdrawn": False,
}
# Events refused while consent is withdrawn (outreach and handing the patient to someone new).
CONSENT_FROZEN_EVENTS = frozenset(
    {"contact_patient", "message_drafted", "message_approved", "message_sent", "handoff_requested"}
)
CONSENT_ACTOR = "system:consent"  # emits review_requested, and nothing else
REVIEW_REASONS = ("consent_withdrawn",)
SCHEDULER_ACTOR_NAME = "system:scheduler"
MODEL_ACTOR_PREFIX = "model:"
MODEL_ALLOWED_EVENTS = frozenset({"message_drafted"})  # the only thing a drafting model may emit (T-D2)
NOTIFIER_ACTOR = "system:notifier"  # the notification mock: the only actor that writes message_sent

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
    last_activity_at: datetime | None = None
    last_activity_event_id: UUID | None = None
    watchdog_flag: dict | None = None
    resume_state: str | None = None
    clock_started_at: datetime | None = None
    clock_started_event_id: UUID | None = None
    breach_due_at: datetime | None = None
    referral_accepted_at: datetime | None = None
    referral_accepted_event_id: UUID | None = None
    provider: str | None = None
    pending_handoff: dict | None = None
    last_handoff: dict | None = None
    # Consent ledger (A6): current values, and every change in order (append-only by replay).
    consent: dict = field(default_factory=lambda: {**DEFAULT_CONSENT, "updated_at": None, "updated_by": None})
    consent_history: tuple = ()
    review_reason: str | None = None  # human review flag (consent withdrawal), set by review_requested
    review_requested_at: datetime | None = None
    backdated_events: int = 0  # T-F4, ADR-027: accepted events dated before the one just before them

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
            "last_activity_at": iso(self.last_activity_at),
            "watchdog_flag": dict(self.watchdog_flag) if self.watchdog_flag else None,
            "resume_state": self.resume_state,
            "clock_started_at": iso(self.clock_started_at),
            "breach_due_at": iso(self.breach_due_at),
            "referral_accepted_at": iso(self.referral_accepted_at),
            "provider": self.provider,
            "pending_handoff": dict(self.pending_handoff) if self.pending_handoff else None,
            "last_handoff": dict(self.last_handoff) if self.last_handoff else None,
            "consent": dict(self.consent),
            "consent_history": [dict(h) for h in self.consent_history],
            "review_reason": self.review_reason,
            "review_requested_at": iso(self.review_requested_at),
            "backdated_events": self.backdated_events,
        }


def initial_model(pathway: Pathway) -> CaseModel:
    return CaseModel(state=pathway.initial_state)


def genesis(model: CaseModel, event: Event) -> CaseModel:
    """The case_created marker starts the first state's SLA clock; it changes no state."""
    at = _aware(event.at)
    return replace(model, last_event_at=at, last_activity_at=at, last_activity_event_id=event.event_id,
                   clock_started_at=at, clock_started_event_id=event.event_id)


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
        return _finish(_transition(model, event, pathway, chosen), event, model, pathway)
    if event.type in pathway.annotations and model.state in pathway.open_states:
        return _finish(_annotate(model, event, pathway), event, model, pathway)
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
        )
        started_at, started_by = _sla_clock_start(
            pathway, target, _aware(event.at), event.event_id, model.referral_accepted_at,
            model.referral_accepted_event_id,
        )
        new = replace(new, clock_started_at=started_at, clock_started_event_id=started_by)
    return new


def _sla_clock_start(pathway: Pathway, state: str, entry_at: datetime, entry_event_id: UUID,
                     referral_at: datetime | None, referral_event_id: UUID | None):
    """Where the SLA for `state` starts counting (A13), and the event that started it: state entry,
    or the latest referral acceptance for an SLA with `clock_start: handoff_accepted` (state entry if
    none)."""
    if referral_at is not None and pathway.state_clock_start(state) == CLOCK_HANDOFF_ACCEPTED:
        return referral_at, referral_event_id
    return entry_at, entry_event_id


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


# -- payload validation and the handoff / consent / freeze rules (A5, A6) ------------------


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
    elif kind == "consent_updated":
        given = [k for k in CONSENT_KEYS if k in payload]
        if not given:
            raise PayloadError(f"consent_updated needs at least one of {list(CONSENT_KEYS)}")
        bad = [k for k in given if not isinstance(payload[k], bool)]
        if bad:
            raise PayloadError(f"consent_updated values must be booleans: {bad}")
    elif kind == "review_requested":
        if payload.get("reason") not in REVIEW_REASONS:
            raise PayloadError(f"review_requested reason must be one of {list(REVIEW_REASONS)}")


def _precheck(model: CaseModel, event: Event, pathway: Pathway) -> None:
    kind, actor = event.type, event.actor
    # Only the consent service may flag a review, and it may flag nothing else.
    if kind == "review_requested" and actor != CONSENT_ACTOR:
        raise InvalidTransition(f"review_requested is emitted only by {CONSENT_ACTOR!r}")
    if actor == CONSENT_ACTOR and kind != "review_requested":
        raise InvalidTransition(f"{CONSENT_ACTOR!r} may only emit review_requested")
    validate_payload(event, pathway)
    # A drafting model may annotate a case with its draft and do nothing else (T-D2).
    if actor.startswith(MODEL_ACTOR_PREFIX) and kind not in MODEL_ALLOWED_EVENTS:
        raise InvalidTransition(f"a model actor may only emit {sorted(MODEL_ALLOWED_EVENTS)}, not {kind!r}")
    # A message is "sent" only by the notification mock, after the approval gate (T-D1).
    if kind == "message_sent" and actor != NOTIFIER_ACTOR:
        raise InvalidTransition(f"message_sent is written only by {NOTIFIER_ACTOR!r}, not {actor!r}")
    # Consent withdrawn freezes outreach, and handing the patient to someone new (T-A23).
    if model.consent_frozen and kind in CONSENT_FROZEN_EVENTS:
        raise InvalidTransition(
            f"consent withdrawn: {kind!r} is refused while the patient's consent is withdrawn; "
            "a human must review the case"
        )
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


def _consent_after(model: CaseModel, event: Event, was_frozen: bool) -> CaseModel:
    changes = {k: event.payload[k] for k in CONSENT_KEYS if k in event.payload}
    at = _aware(event.at)
    consent = {**model.consent, **changes, "updated_at": at.isoformat(), "updated_by": event.actor}
    consent["withdrawn"] = model.state == CONSENT_WITHDRAWN  # the state is the authority
    history = (
        *model.consent_history,
        {"at": at.isoformat(), "actor": event.actor, "changes": changes},
    )
    out = replace(model, consent=consent, consent_history=history)
    if was_frozen and not consent["withdrawn"]:  # consent restored: the review flag is moot
        out = replace(out, review_reason=None, review_requested_at=None)
    return out


def _finish(
    model: CaseModel, event: Event, before: CaseModel | None = None, pathway: Pathway | None = None
) -> CaseModel:
    """Bookkeeping common to every accepted event: last_event_at, ownership, handoff, consent."""
    kind, payload, at = event.type, event.payload, _aware(event.at)
    # T-F4 / ADR-027: an event dated before the previous one is accepted (the log is ordered by seq, not by
    # `at`) but counted, and the timeline shows it. The comparison is with the previous event's own `at`.
    previous = (before or model).last_event_at
    backdated = previous is not None and at < previous
    out = replace(model, last_event_at=at, backdated_events=model.backdated_events + (1 if backdated else 0))
    if kind == "watchdog_flagged":
        # A flag is not activity: the idle clock keeps running from the last real event.
        out = replace(out, watchdog_flag={"reason": payload.get("reason"),
                                          "idle_days": payload.get("idle_days"), "at": at.isoformat()})
    else:
        # Any other event is activity: the case is being looked at, so the flag clears.
        out = replace(out, last_activity_at=at, last_activity_event_id=event.event_id, watchdog_flag=None)
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
            "requested_event_id": str(event.event_id),
        })
    elif kind == "handoff_accepted":
        pending = (before or model).pending_handoff
        provider = (payload.get("provider") or pending["to_provider"]).strip()
        out = replace(out, owner=event.actor, provider=provider, pending_handoff=None,
                      referral_accepted_at=at, referral_accepted_event_id=event.event_id,
                      last_handoff={"outcome": "accepted", "to_provider": pending["to_provider"],
                                    "by": event.actor, "at": at.isoformat()})
        if (pathway is not None and out.state in pathway.open_states
                and pathway.state_clock_start(out.state) == CLOCK_HANDOFF_ACCEPTED):
            # A referral accepted while the state's SLA runs from acceptance restarts that
            # clock: a new obligation, like a state change (A13, ADR-020).
            out = replace(out, clock_started_at=at, clock_started_event_id=event.event_id, overdue=False,
                          escalation_level=0, escalation_accepted=False, breach_due_at=None)
    elif kind == "handoff_rejected":
        pending = (before or model).pending_handoff
        out = replace(out, pending_handoff=None, last_handoff={
            "outcome": "rejected", "reason": payload["reason"],
            "bounced": payload.get("bounced") is True, "to_provider": pending["to_provider"],
            "by": event.actor, "at": at.isoformat(),
        })
    elif kind == "consent_updated":
        out = _consent_after(out, event, (before or model).consent_frozen)
    elif kind == "review_requested":
        out = replace(out, review_reason=payload["reason"], review_requested_at=at)
    return out
