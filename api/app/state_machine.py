"""Pure state-machine functions. No I/O, no clock, no randomness: same input, same output.

`step(model, event, pathway)` folds one event into the case's read model and raises
`InvalidTransition` (changing nothing) if the event is not allowed. The read model is what a
replay of the event log produces:

    state                main or side state ("closed" is terminal)
    overdue              the current state's SLA is breached and not yet resolved
    escalation_level     rungs of the escalation ladder emitted for the current breach
    escalation_accepted  a human accepted the latest rung (no further rungs for this breach)
    escalation_event_id  the `event_id` of the `escalate` event the ladder's current rung is recorded on, and
    escalation_at        that event's `at` (A19, ADR-031). Ladders that were coalesced (`also_for`) or attached
                         (`attached_to`) to one escalation share its id, and one `escalation_accepted` accepts them all
    owner                actor of the latest accept_ownership / handoff_accepted / reassign
    provider             the provider the owner acts for (set by handoff_accepted, or by
                         accept_ownership / reassign when the payload names one)
    pending_handoff      an unanswered handoff request {to_provider, requested_by, from_owner,
                         requested_at, requested_event_id}; ownership changes only when it is accepted (A5)
    last_handoff         outcome of the latest answered or bounced handoff (A5)
    consent_frozen       outreach is frozen (state is consent_withdrawn)
    approved_unsent      draft ids a human approved whose `message_sent` has not been written yet (SEC-2, ADR-030):
                         the approval gate lives HERE, so a `message_sent` is accepted only for a draft in this set
    last_event_at        `at` of the latest event, including annotations
    last_activity_at     `at` of the latest event that is not a watchdog flag: the idle clock (A11)
    last_human_activity_at  `at` of the latest event written by a person (not the scheduler, the notifier, the
                         consent service, a drafting model or the watchdog): the idle clock (A11, SEC-2). An
                         overdue case the sweep keeps escalating is not being looked at, so its idle clock runs
    last_human_activity_event_id  the `event_id` of that event: what the watchdog derives its flag id from, so a
                         later episode that starts at the same instant is still a different episode (SEC-2)
    watchdog_flag        {reason, idle_days, at} from a watchdog_flagged until a person acts on the case,
                         then None (A9, A11, SEC-2: an automated event does not clear it)
    resume_state         where the case returns to when consent is restored (side state only)
    clock_started_at     when the current state's SLA clock started: the event that entered the
                         state, or, for an SLA with `clock_start: handoff_accepted` (A13), the
                         latest referral acceptance when there was one
    clock_started_event_id  the `event_id` of that event. The scheduler derives the ids of `mark_overdue`
                         and the escalation rungs from it (never from a timestamp, SEC-2): re-entering a
                         state is a new clock and so a new breach, even at the very same instant
    referral_accepted_at `at` of the latest handoff_accepted (A13), None before any
    breach_due_at        the due date of the open breach (set with overdue)
    milestones           per milestone of the pathway (A18, ADR-028): {started_at, met_at, overdue,
                         breach_due_at, escalation_level, escalation_accepted, void}. A milestone is a clock
                         that spans states (referral within 15 working days of the result, colonoscopy within
                         31 working days of referral); it has its own overdue flag and ladder position, kept
                         apart from the state-level ones above. `overdue` above stays the state's
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
CHANNELS = ("sms", "whatsapp", "phone", "letter")  # contact channels (the `channel_*` consent keys)
# Events refused while consent is withdrawn (outreach and handing the patient to someone new).
CONSENT_FROZEN_EVENTS = frozenset(
    {"contact_patient", "message_drafted", "message_approved", "message_sent", "handoff_requested"}
)
CONSENT_ACTOR = "system:consent"  # emits review_requested {consent_withdrawn}, and nothing else
REVIEW_REASONS = ("consent_withdrawn",)
# Reply triage (D13): the API classifies an inbound patient reply as `system:triage`. That actor writes the
# reply and its classification and may ask for a human to look at the case; it may do nothing else. Its review
# reasons are separate from the consent one, so a triage flag can never be mistaken for, or hide, a consent flag.
TRIAGE_ACTOR = "system:triage"
TRIAGE_REVIEW_REASONS = ("opt_out", "wrong_number", "already_done", "triage_uncertain")
TRIAGE_CALLBACK_REASONS = ("reschedule", "cannot_attend", "patient_does_not_understand")
TRIAGE_ALLOWED_EVENTS = frozenset({"reply_received", "reply_classified", "review_requested", "callback_requested"})
TRIAGE_ONLY_EVENTS = frozenset({"reply_received", "reply_classified"})  # nobody else may write these
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


# The fields of one ladder (the state's or a milestone's) that a new obligation, a met milestone or a void one clears.
_NO_LADDER = {
    "escalation_level": 0, "escalation_accepted": False, "escalation_event_id": None, "escalation_at": None,
}


@dataclass(frozen=True)
class MilestoneState:
    """One milestone's state in the read model (A18). `overdue` is the milestone's own breach flag: the state-level
    `CaseModel.overdue` is untouched by it. `void` = the case closed before the milestone was met."""

    started_at: datetime | None = None
    # the `event_id` of the event that started this clock (the `case_created` marker, the `from_event`, or the
    # event that entered the awaiting state): the sweep derives the milestone's mark_overdue and rung ids from it,
    # never from a timestamp (SEC-2), so a restarted milestone is a new breach even at the very same instant
    started_event_id: UUID | None = None
    met_at: datetime | None = None
    overdue: bool = False
    breach_due_at: datetime | None = None
    escalation_level: int = 0
    escalation_accepted: bool = False
    escalation_event_id: UUID | None = None  # the escalate event the current rung is recorded on (A19)
    escalation_at: datetime | None = None  # that event's `at`
    void: bool = False

    def to_json(self) -> dict:
        def iso(dt):
            return dt.isoformat() if dt is not None else None

        return {
            "started_at": iso(self.started_at),
            "met_at": iso(self.met_at),
            "overdue": self.overdue,
            "breach_due_at": iso(self.breach_due_at),
            "escalation_level": self.escalation_level,
            "escalation_accepted": self.escalation_accepted,
            "void": self.void,
        }


@dataclass(frozen=True)
class CaseModel:
    state: str
    overdue: bool = False
    escalation_level: int = 0
    escalation_accepted: bool = False
    escalation_event_id: UUID | None = None  # the escalate event the current rung is recorded on (A19)
    escalation_at: datetime | None = None  # that event's `at`
    owner: str | None = None
    consent_frozen: bool = False
    last_event_at: datetime | None = None
    last_activity_at: datetime | None = None
    last_human_activity_at: datetime | None = None
    last_human_activity_event_id: UUID | None = None
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
    approved_unsent: tuple = ()  # draft ids approved by a human and not yet sent (ADR-030)
    # Consent ledger (A6): current values, and every change in order (append-only by replay).
    consent: dict = field(default_factory=lambda: {**DEFAULT_CONSENT, "updated_at": None, "updated_by": None})
    consent_history: tuple = ()
    review_reason: str | None = None  # human review flag (consent withdrawal), set by review_requested
    review_requested_at: datetime | None = None
    # A triage flag (D13): the latest `review_requested` from system:triage. Kept apart from review_reason (consent) so
    # neither can overwrite or hide the other. Nothing here changes the state; a person sees it on the worklist.
    triage_review_reason: str | None = None
    triage_review_requested_at: datetime | None = None
    backdated_events: int = 0  # T-F4, ADR-027: accepted events dated before the one just before them
    created_at: datetime | None = None  # the genesis event's `at` (a milestone may start there)
    created_event_id: UUID | None = None  # the genesis event's `event_id` (a milestone started there derives its ids from it)
    milestones: dict = field(default_factory=dict)  # name -> MilestoneState (A18); never mutated in place

    @property
    def milestone_overdue(self) -> bool:
        """True while any milestone is breached and unmet."""
        return any(m.overdue for m in self.milestones.values())

    @property
    def any_overdue(self) -> bool:
        """The state SLA or any milestone is breached: what the worklist's `overdue` means (A18)."""
        return self.overdue or self.milestone_overdue

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
            "approved_unsent_drafts": list(self.approved_unsent),
            "last_event_at": iso(self.last_event_at),
            "last_activity_at": iso(self.last_activity_at),
            "last_human_activity_at": iso(self.last_human_activity_at),
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
            "triage_review_reason": self.triage_review_reason,
            "triage_review_requested_at": iso(self.triage_review_requested_at),
            "backdated_events": self.backdated_events,
            "milestones": {name: m.to_json() for name, m in self.milestones.items()},
        }


def initial_model(pathway: Pathway) -> CaseModel:
    return CaseModel(state=pathway.initial_state, milestones={n: MilestoneState() for n in pathway.milestones})


def genesis(model: CaseModel, event: Event, pathway: Pathway | None = None) -> CaseModel:
    """The case_created marker starts the first state's SLA clock and any milestone that runs from
    `case_created`; it changes no state. Without a pathway the milestones start at the first `step`
    instead (`start_genesis_milestones`), which is how a store that holds only the incremental model
    stays correct."""
    at = _aware(event.at)
    out = replace(model, last_event_at=at, last_activity_at=at, last_human_activity_at=at,
                  last_human_activity_event_id=event.event_id,
                  clock_started_at=at, clock_started_event_id=event.event_id,
                  created_at=at, created_event_id=event.event_id)
    return start_genesis_milestones(out, pathway) if pathway is not None else out


def start_genesis_milestones(model: CaseModel, pathway: Pathway) -> CaseModel:
    """Idempotent: start every unstarted milestone that runs from `case_created` at the case's creation, and
    make sure every milestone of the pathway has an entry."""
    changed: dict[str, MilestoneState] = {}
    for name, d in pathway.milestones.items():
        cur = model.milestones.get(name)
        if cur is None:
            cur = MilestoneState()
            changed[name] = cur
        if (d.from_event == CASE_CREATED and model.created_at is not None and cur.started_at is None
                and cur.met_at is None and not cur.void):
            changed[name] = replace(cur, started_at=model.created_at, started_event_id=model.created_event_id)
    return replace(model, milestones={**model.milestones, **changed}) if changed else model


def replay_model(events, pathway: Pathway) -> CaseModel:
    """Deterministically fold events into the read model, from the pathway's initial state."""
    model = initial_model(pathway)
    for ev in events:
        model = genesis(model, ev, pathway) if ev.type == CASE_CREATED else step(model, ev, pathway)
    return model


def step(model: CaseModel, event: Event, pathway: Pathway) -> CaseModel:
    """Return the new read model, or raise InvalidTransition. Never mutates anything."""
    if event.type in pathway.human_only_events and is_automated(event.actor) and not _relays_patient_request(event):
        raise InvalidTransition(f"event {event.type!r} requires a human actor")
    if pathway.milestones:
        model = start_genesis_milestones(model, pathway)
    _precheck(model, event, pathway)

    candidates = pathway.transitions_for(model.state, event.type)
    chosen = next((t for t in candidates if t.matches(event.payload)), None)
    if chosen is not None:
        done = _finish(_transition(model, event, pathway, chosen), event, model, pathway)
        return _advance_milestones(done, model, event, pathway)
    if event.type in pathway.annotations and model.state in pathway.open_states:
        done = _finish(_annotate(model, event, pathway), event, model, pathway)
        return _advance_milestones(done, model, event, pathway)
    if candidates:
        raise InvalidTransition(
            f"event {event.type!r} from state {model.state!r}: payload does not satisfy "
            "the transition's conditions"
        )
    raise InvalidTransition(f"event {event.type!r} not allowed from state {model.state!r}")


def _relays_patient_request(event: Event) -> bool:
    """The one exception to "human only": `system:triage` passes on a callback the PATIENT asked for in their own reply
    (reschedule, cannot attend, did not understand). The reason is from the triage list and the payload must name the
    classification it came from (`source_event_id`), so the request can be traced to the reply."""
    return (
        event.actor == TRIAGE_ACTOR
        and event.type == "callback_requested"
        and event.payload.get("reason") in TRIAGE_CALLBACK_REASONS
        and bool(event.payload.get("source_event_id"))
    )


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
            escalation_event_id=None,
            escalation_at=None,
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


# -- milestones (A18, ADR-028) -------------------------------------------------------------


def _ms(model: CaseModel, name: str) -> MilestoneState:
    return model.milestones.get(name) or MilestoneState()


def _set_ms(model: CaseModel, name: str, ms: MilestoneState) -> CaseModel:
    return replace(model, milestones={**model.milestones, name: ms})


def _advance_milestones(new: CaseModel, before: CaseModel, event: Event, pathway: Pathway) -> CaseModel:
    """Fold one accepted event into every milestone: the `until_event` meets it (and clears its breach and ladder),
    the `from_event` (re)starts an unmet one, entering the state that awaits the `until_event` starts one that never
    got a `from_event` (a case with no formal referral keeps a colonoscopy clock, ADR-020), and closing the case
    voids the unmet ones. A met milestone never changes again."""
    if not pathway.milestones:
        return new
    at = _aware(event.at)
    closing = new.state == pathway.terminal_state and before.state != pathway.terminal_state
    entered = new.state != before.state and new.state in pathway.open_states
    awaiting = {m.name for m in pathway.milestone_awaiting(new.state)} if entered else set()
    out = dict(new.milestones)
    for name, d in pathway.milestones.items():
        cur = out.get(name) or MilestoneState()
        if cur.met_at is None and not cur.void:
            if event.type == d.until_event:
                cur = replace(cur, met_at=at, overdue=False, **_NO_LADDER)
            elif event.type == d.from_event:
                cur = MilestoneState(started_at=at, started_event_id=event.event_id)  # a new obligation: breach and ladder cleared
            elif cur.started_at is None and name in awaiting:
                cur = replace(cur, started_at=at, started_event_id=event.event_id)
            if closing and cur.met_at is None:
                cur = replace(cur, void=True, overdue=False, **_NO_LADDER)
        out[name] = cur
    return replace(new, milestones=out)


def _running_milestone(model: CaseModel, name, pathway: Pathway, what: str) -> MilestoneState:
    """The named milestone, which must exist, have started and be unmet and not void."""
    if name not in pathway.milestones:
        raise InvalidTransition(f"{what} names unknown milestone {name!r}")
    ms = _ms(model, name)
    if ms.void or ms.met_at is not None:
        raise InvalidTransition(f"milestone {name!r} is already {'met' if ms.met_at is not None else 'void'}")
    if ms.started_at is None:
        raise InvalidTransition(f"milestone {name!r} has not started")
    return ms


def _milestone_in(model: CaseModel, event: Event, pathway: Pathway) -> tuple[str, MilestoneState]:
    """The named milestone of a mark_overdue / escalate payload, which must be running and unmet."""
    name = event.payload.get("milestone")
    return name, _running_milestone(model, name, pathway, f"event {event.type!r}")


def _mark_overdue(model: CaseModel, event: Event, pathway: Pathway) -> CaseModel:
    milestone = event.payload.get("milestone")
    if milestone is None:
        _require_current_state(model, event)
        if pathway.sla_for_state(model.state) is None:
            raise InvalidTransition(f"state {model.state!r} has no SLA to breach")
        if model.overdue:
            raise InvalidTransition("case is already overdue for this state")
    else:
        name, ms = _milestone_in(model, event, pathway)
        if ms.overdue:
            raise InvalidTransition(f"milestone {name!r} is already overdue")
    raw = event.payload.get("due_at")
    try:
        due = _aware(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
    except ValueError:
        raise InvalidTransition(f"mark_overdue needs an ISO due_at, got {raw!r}") from None
    if _aware(event.at) <= due:
        raise InvalidTransition("mark_overdue before the due date")
    if milestone is not None:
        return _set_ms(model, name, replace(ms, overdue=True, breach_due_at=due))
    return replace(model, overdue=True, breach_due_at=due)


def _ladder(model: CaseModel, milestone: str | None):
    """(overdue, accepted, level, escalation_event_id, escalation_at) of the state's ladder (`milestone` None) or of
    a milestone's."""
    x = model if milestone is None else _ms(model, milestone)
    return x.overdue, x.escalation_accepted, x.escalation_level, x.escalation_event_id, x.escalation_at


def _set_ladder(model: CaseModel, milestone: str | None, **changes) -> CaseModel:
    if milestone is None:
        return replace(model, **changes)
    return _set_ms(model, milestone, replace(_ms(model, milestone), **changes))


def _ladders(model: CaseModel) -> list:
    """Every ladder of the case: None (the state's) first, then each milestone in the pathway's order."""
    return [None, *model.milestones]


def _escalate(model: CaseModel, event: Event, pathway: Pathway) -> CaseModel:
    milestone = event.payload.get("milestone")
    if milestone is None:
        _require_current_state(model, event)
        what = "case"
    else:
        name, _ = _milestone_in(model, event, pathway)
        what = f"milestone {name!r}"
    overdue, accepted, current, _, _ = _ladder(model, milestone)
    if not overdue:
        raise InvalidTransition(f"cannot escalate a {what} that is not overdue")
    if accepted:
        raise InvalidTransition("a human accepted the latest rung: no further rungs for this breach")
    rung = pathway.rung(str(event.payload.get("rung")))
    if rung is None:
        raise InvalidTransition(f"unknown escalation rung {event.payload.get('rung')!r}")
    level = pathway.rung_level(rung.rung)
    if level != current + 1:
        raise InvalidTransition(
            f"rung {rung.rung!r} is level {level}; the next rung is level "
            f"{current + 1} (rungs are never skipped or repeated)"
        )
    target = event.payload.get("target")
    if target is not None and target != rung.target:
        raise InvalidTransition(f"rung {rung.rung!r} targets {rung.target!r}, not {target!r}")
    also_for = event.payload.get("also_for")
    attached_to = event.payload.get("attached_to")
    if also_for is not None and attached_to is not None:
        raise InvalidTransition("an escalation cannot both cover other ladders (also_for) and be attached_to another")
    at = _aware(event.at)
    if attached_to is not None:
        holder_at = _attachment_holder(model, milestone, attached_to, rung.target, pathway)
        return _set_ladder(model, milestone, escalation_level=level, escalation_event_id=UUID(str(attached_to)),
                           escalation_at=holder_at)
    out = _set_ladder(model, milestone, escalation_level=level, escalation_event_id=event.event_id, escalation_at=at)
    if also_for is not None:
        for scope, entry_level in _also_for_ladders(model, milestone, also_for, rung.target, pathway):
            out = _set_ladder(out, scope, escalation_level=entry_level, escalation_event_id=event.event_id,
                              escalation_at=at)
    return out


def _also_for_ladders(model: CaseModel, primary: str | None, also_for, target: str, pathway: Pathway) -> list:
    """Validate the `also_for` entries of an escalation to `target` and return [(ladder, new level)]: each named
    ladder must be another one of this case, overdue, unaccepted, with its next rung (the entry's `level`) aimed at
    the same target (A19)."""
    if not isinstance(also_for, list) or not also_for:
        raise InvalidTransition("also_for must be a non-empty list")
    seen = {primary}
    out = []
    for entry in also_for:
        if not isinstance(entry, dict):
            raise InvalidTransition("also_for entries must be objects")
        has_state, has_ms = entry.get("state") is not None, entry.get("milestone") is not None
        if has_state == has_ms:
            raise InvalidTransition("an also_for entry names exactly one of 'state' and 'milestone'")
        if has_state:
            if entry["state"] != model.state:
                raise InvalidTransition(
                    f"also_for names state {entry['state']!r} but the case is in {model.state!r}")
            scope = None
        else:
            scope = entry["milestone"]
            _running_milestone(model, scope, pathway, "also_for")
        if scope in seen:
            raise InvalidTransition("also_for names a ladder twice, or the escalation's own ladder")
        seen.add(scope)
        label = "the state" if scope is None else f"milestone {scope!r}"
        overdue, accepted, current, _, _ = _ladder(model, scope)
        if not overdue:
            raise InvalidTransition(f"also_for: {label} is not overdue")
        if accepted:
            raise InvalidTransition(f"also_for: a human accepted the latest rung of {label}")
        rung = pathway.rung(str(entry.get("rung")))
        if rung is None:
            raise InvalidTransition(f"also_for: unknown escalation rung {entry.get('rung')!r}")
        level = pathway.rung_level(rung.rung)
        if level != current + 1 or entry.get("level") not in (None, level):
            raise InvalidTransition(
                f"also_for: rung {rung.rung!r} is not the next rung of {label} (level {current + 1})")
        if rung.target != target:
            raise InvalidTransition(
                f"also_for: rung {rung.rung!r} targets {rung.target!r}, not the escalation's {target!r}")
        out.append((scope, level))
    return out


def _attachment_holder(model: CaseModel, own: str | None, attached_to, target: str, pathway: Pathway):
    """The `escalation_at` of the escalation `attached_to` names: it must be the current, unaccepted escalation of
    another ladder of this case whose rung is aimed at `target` (A19). The same-working-day rule needs a calendar,
    so the sweep applies it when it plans the event; the machine checks everything it can see."""
    try:
        wanted = UUID(str(attached_to))
    except ValueError:
        raise InvalidTransition(f"attached_to {attached_to!r} is not an event id") from None
    for scope in _ladders(model):
        if scope == own:
            continue
        _, accepted, level, ev_id, ev_at = _ladder(model, scope)
        rung = pathway.rung_at(level)
        if ev_id == wanted and level > 0 and not accepted and rung is not None and rung.target == target:
            return ev_at
    raise InvalidTransition(
        f"attached_to {attached_to}: no other ladder of this case has that as its current unaccepted "
        f"escalation to {target!r}"
    )


def _escalation_accepted(model: CaseModel, event: Event, pathway: Pathway) -> CaseModel:
    """Accepts the latest rung of one ladder. The ladder is the milestone the payload names; without one it is the
    state's ladder if that has a rung, else the one milestone ladder that has a rung (so a client that does not
    know about milestones can still accept). One acceptance covers the whole coalesced escalation: every other
    ladder whose current rung is recorded on the same escalation event is accepted with it (A19)."""
    milestone = event.payload.get("milestone")
    if milestone is None and model.escalation_level == 0:
        running = [n for n, m in model.milestones.items() if m.escalation_level > 0]
        if len({_ms(model, n).escalation_event_id or n for n in running}) == 1:
            milestone = running[0]
    if milestone is not None and milestone not in pathway.milestones:
        raise InvalidTransition(f"escalation_accepted names unknown milestone {milestone!r}")
    _, accepted, level, ev_id, _ = _ladder(model, milestone)
    rung = pathway.rung_at(level)
    if rung is None:
        raise InvalidTransition("no escalation rung to accept")
    if accepted:
        raise InvalidTransition(f"rung {rung.rung!r} is already accepted")
    if actor_role(event.actor) not in rung.accepted_by:
        raise InvalidTransition(
            f"rung {rung.rung!r} is accepted by {list(rung.accepted_by)}, "
            f"not by {actor_role(event.actor)!r}"
        )
    named = event.payload.get("rung")
    if named is not None and named != rung.rung:
        raise InvalidTransition(f"the latest rung is {rung.rung!r}, not {named!r}")
    out = _set_ladder(model, milestone, escalation_accepted=True)
    if ev_id is not None:
        for scope in _ladders(out):
            if scope == milestone:
                continue
            _, other_accepted, other_level, other_id, _ = _ladder(out, scope)
            if other_id == ev_id and other_level > 0 and not other_accepted:
                out = _set_ladder(out, scope, escalation_accepted=True)
    return out


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
        allowed = TRIAGE_REVIEW_REASONS if actor == TRIAGE_ACTOR else REVIEW_REASONS
        if payload.get("reason") not in allowed:
            raise PayloadError(f"review_requested reason must be one of {list(allowed)}")


def _precheck(model: CaseModel, event: Event, pathway: Pathway) -> None:
    kind, actor = event.type, event.actor
    # Only the consent service and the reply triage may flag a review, and each may flag nothing else.
    if kind == "review_requested" and actor not in (CONSENT_ACTOR, TRIAGE_ACTOR):
        raise InvalidTransition(f"review_requested is emitted only by {CONSENT_ACTOR!r} or {TRIAGE_ACTOR!r}")
    if actor == CONSENT_ACTOR and kind != "review_requested":
        raise InvalidTransition(f"{CONSENT_ACTOR!r} may only emit review_requested")
    # Reply triage (D13): it writes the reply and what it made of it, may flag a review or relay the patient's own
    # callback request, and writes nothing else; and nobody else writes a reply event, so a log entry saying "the
    # patient replied" cannot be forged through the events route or by another service.
    if actor == TRIAGE_ACTOR and kind not in TRIAGE_ALLOWED_EVENTS:
        raise InvalidTransition(f"{TRIAGE_ACTOR!r} may only emit {sorted(TRIAGE_ALLOWED_EVENTS)}, not {kind!r}")
    if kind in TRIAGE_ONLY_EVENTS and actor != TRIAGE_ACTOR:
        raise InvalidTransition(f"{kind} is written only by {TRIAGE_ACTOR!r}, not {actor!r}")
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
    # The approval gate, in the machine (SEC-2, ADR-030): a message goes out only for a draft a human
    # approved on THIS case and that has not gone out yet, on a channel the patient consents to NOW (at
    # apply time, under the case lock), not when the approval was given. A bug or a stray caller in the
    # notifier cannot send what nobody approved, send it twice, or send it past an opt-out.
    if kind == "message_approved" and event.payload.get("draft_id") in model.approved_unsent:
        raise InvalidTransition(f"message_approved: draft {event.payload.get('draft_id')!r} is already approved")
    if kind == "message_sent":
        draft_id = event.payload.get("draft_id")
        if draft_id is None or draft_id not in model.approved_unsent:
            raise InvalidTransition(
                f"message_sent: draft {draft_id!r} is not an approved, unsent draft of this case"
            )
        consented, why = channel_consent(model, event.payload.get("channel"))
        if not consented:
            raise InvalidTransition(f"message_sent: {why}")
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


def channel_consent(model: CaseModel, channel) -> tuple[bool, str]:
    """(allowed, reason): may the patient be contacted on `channel` given the consent ledger as it stands.
    Not allowed when consent is withdrawn, the channel is unknown, or the channel is switched off."""
    if model.consent_frozen or model.consent.get("withdrawn"):
        return False, "consent withdrawn"
    if channel not in CHANNELS:
        return False, f"unknown channel {channel!r}"
    if not model.consent.get(f"channel_{channel}", False):
        return False, f"patient has not consented to contact by {channel}"
    return True, "ok"


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
        out = replace(out, last_activity_at=at)
        if not is_automated(event.actor):
            # A person acted: the case is being looked at, so the idle clock restarts and the flag clears.
            # An automated event (the sweep's mark_overdue and escalations, a drafted message, the notifier)
            # is not anyone looking at the case: it neither restarts the idle clock nor clears the flag.
            out = replace(out, last_human_activity_at=at, last_human_activity_event_id=event.event_id,
                          watchdog_flag=None)
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
                          breach_due_at=None, **_NO_LADDER)
    elif kind == "handoff_rejected":
        pending = (before or model).pending_handoff
        out = replace(out, pending_handoff=None, last_handoff={
            "outcome": "rejected", "reason": payload["reason"],
            "bounced": payload.get("bounced") is True, "to_provider": pending["to_provider"],
            "by": event.actor, "at": at.isoformat(),
        })
    elif kind == "message_approved" and isinstance(payload.get("draft_id"), str):
        out = replace(out, approved_unsent=(*out.approved_unsent, payload["draft_id"]))
    elif kind == "message_sent":
        out = replace(out, approved_unsent=tuple(d for d in out.approved_unsent if d != payload.get("draft_id")))
    elif kind == "consent_updated":
        out = _consent_after(out, event, (before or model).consent_frozen)
    elif kind == "review_requested" and event.actor == TRIAGE_ACTOR:
        out = replace(out, triage_review_reason=payload["reason"], triage_review_requested_at=at)
    elif kind == "review_requested":
        out = replace(out, review_reason=payload["reason"], review_requested_at=at)
    return out
