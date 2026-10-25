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
                         requested_at, requested_event_id, resumed_at?}; ownership changes only when it is accepted (A5).
                         While the referral is held (consent withdrawn or sharing refused, A36, ADR-044) it stays pending,
                         and when the hold ends `resumed_at` records when its bounce timer starts again
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
    referral_accepted_at `at` of the latest handoff_accepted (A13), None before any. For reporting only since A25: the
                         31-working-day milestone starts at the FIRST handoff_requested and never restarts
    breach_due_at        the due date of the open breach (set with overdue)
    milestones           per milestone of the pathway (A18, ADR-028): {started_at, met_at, overdue,
                         breach_due_at, escalation_level, escalation_accepted, void}. A milestone is a clock
                         that spans states (referral within 15 working days of the result, colonoscopy within
                         31 working days of referral); it has its own overdue flag and ladder position, kept
                         apart from the state-level ones above. `overdue` above stays the state's
    closure_proposal     {evidence_ref, proposed_at, event_id} from a `closure_proposed` (A21, ADR-032) until the case closes
                         or is reopened: the evidence scan found closure evidence and the clinician has a one-click
                         sign-off. A proposal only: `close` does not read it and still needs a human and evidence
    auto_message_times   `at` of every `message_sent` the automation sent (A21), for the accounting
    outbound_message_times  `at` of EVERY `message_sent`, automatic or an approved draft: the weekly cap on automatic
                         messages counts all outbound messages (A5 of the engineering review), checked at append time
                         against the policy's limit (`outreach_policy.auto_message_guard`), never during replay
    outreach_holds       the open outreach holds, `opt_out` and/or `wrong_number` (`review_requested` from the triage
                         actor): while any is open nothing is sent, automatic or an approved draft (ADR-038).
                         `opt_out` is cleared by a human `consent_updated`, `wrong_number` by a human
                         `contact_updated {contact_valid: true}`
    contact_confirmed_at `at` of the latest `contact_updated`: a person confirmed the contact details, which overrides a
                         `contact_valid: false` on the case_created payload
    incomplete_procedure {evidence_ref, flagged_at} from a `review_requested` with reason `incomplete_procedure` (A24,
                         ADR-036): the automation found the case's colonoscopy record in HAPI and it is incomplete,
                         stopped, not final or without a final report. A flag for a person; it changes no state.
                         Cleared by a closure proposal (a complete record was found), a close or a reopen
    reroute_exhausted_at when the automation found the reroute list used up (`review_requested`, reason
                         `reroute_exhausted`) until the case leaves `referral_rejected`
    refusal              the informed refusal on record (A33, ADR-042): counselling_ref, counselled_by, reason, recorded_at,
                         event_id and re_offer_due_at (the refusal's date plus the pathway's re_offer_months); cleared by a reopen
    closure_outcome      "declined" or "investigated" once closed (A33), None while open
    backdated_events     how many accepted events had an `at` earlier than the previous event's `at`
                         (T-F4, ADR-027): a user token's backdated event is refused at the API (SEC-2); only the
                         service actors that legitimately post late (the sweep, the notifier) can still produce
                         one, and it is accepted, ordered by `seq`, and counted

`overdue` and `escalation_level` are flags, not states, because a case can be overdue AND in
patient_contacted (ADR-011). Side states that change the path are real states.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
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
# A36 (ADR-044): the referral events that move the patient to another provider. They are refused while the referral is
# HELD: consent is withdrawn, or the patient does not allow sharing with the specialist (`share_with_specialist` false).
# A request already pending when the hold begins is kept (not cancelled), its bounce timer does not run, and it can be
# accepted again once consent is restored (`referral_hold` below; the sweep restarts the timer from `resumed_at`).
REFERRAL_HELD_EVENTS = frozenset({"handoff_requested", "handoff_accepted"})
CONSENT_ACTOR = "system:consent"  # emits review_requested (reason consent_withdrawn), and nothing else
# Why a case is flagged for a person (`review_requested.reason`), and the ONE actor that may give each reason
# (`REVIEW_EMITTER`, enforced in `_precheck`): the consent service when consent is withdrawn (A6), the automation when
# the reroute list is used up (A21), and the reply triage (D13) for what a patient's own reply says.
REVIEW_REASONS = ("consent_withdrawn", "reroute_exhausted", "opt_out", "wrong_number", "triage_uncertain", "already_done",
                  "incomplete_procedure")
OUTREACH_HOLD_REASONS = ("opt_out", "wrong_number")  # while one is open the automation sends nothing (A5)
# Reply triage (D13): the API classifies an inbound patient reply as `system:triage`. That actor writes the reply and its
# classification, may ask for a person to look at the case, and may relay a callback the PATIENT asked for in their own
# reply (traceably: a reason from TRIAGE_CALLBACK_REASONS and the `source_event_id` of the classification). Its review
# reasons are kept apart from the consent one in the read model (`triage_review_reason`), so a triage flag can never
# overwrite or hide a consent flag.
TRIAGE_REVIEW_REASONS = ("opt_out", "wrong_number", "already_done", "triage_uncertain")
TRIAGE_CALLBACK_REASONS = ("reschedule", "cannot_attend", "patient_does_not_understand")
SCHEDULER_ACTOR_NAME = "system:scheduler"
WATCHDOG_ACTOR = "system:watchdog"  # emits watchdog_flagged, and nothing else
MODEL_ACTOR_PREFIX = "model:"
NOTIFIER_ACTOR = "system:notifier"  # the notification mock: writes message_sent for an approved draft
ERASURE_ACTOR = "system:erasure"  # A32 (ADR-025): writes `erasure_completed`, and nothing else
ERASURE_EVENT = "erasure_completed"
TRIAGE_ACTOR = "system:triage"  # D13: classifies inbound patient replies (rows below)
# The agency (A22, ADR-033): agents are labour, not authority. An agency actor may emit NO event: whatever an agent
# causes on a case is written by the actor that owns that path (the drafting model's `message_drafted` through the
# drafts route), under that actor's own rules. Its row below is empty, and `step` refuses the prefix by name first.
AGENCY_ACTOR_PREFIX = "system:agency:"
# A28, ADR-039: the HL7 v2 adapter. It books the confirmatory appointment from an SIU message and nothing else as an event
# (a case is created by the intake path, colonoscopy evidence is recorded in HAPI and appends no event).
HL7_ACTOR = "system:hl7"
# A21, ADR-032: the automation actor. It does the steps that are not a human decision and nothing else. It can never
# close a case (the closure guard refuses every automated actor) and none of the human-only events.
AUTOMATION_ACTOR = "system:automation"
AUTO_MESSAGE_WINDOW = timedelta(days=7)  # "per week" in max_auto_messages_per_week

# The roles a person can act in (equals app.auth.USER_ROLES; tests/test_actor_table.py keeps the two in step).
HUMAN_ROLES = ("lab", "primary_care_owner", "specialist_owner", "coordinator", "patient", "supervisor")
# What a person may emit as far as the machine is concerned: every event except the ones only a service writes. WHICH
# person (which role, on whose case) is app.policy's job (EVENT_POLICY, owner scope): the machine's table says only that
# a service-only event is never a person's, whatever route it came through.
HUMAN_EVENTS = frozenset({
    "acknowledge", "accept_ownership", "contact_patient", "patient_acknowledge", "book_confirmatory",
    "complete_confirmatory", "clinician_review", "close", "reopen", "escalate", "escalation_accepted",
    "handoff_requested", "handoff_accepted", "handoff_rejected", "reassign", "consent_updated", "contact_updated",
    "mark_lost_contact", "message_drafted", "message_approved", "message_rejected", "callback_requested",
    "record_informed_refusal", "appointment_cancelled",
})

# THE table of who may write what (A3 of the engineering review): actor -> the event types the machine accepts from it.
# Default deny: an actor that matches no key, or an event type its row does not list, is refused before anything else is
# looked at, in `step`, so no store, route or in-process caller can skip it. A key that ends in ":" is a prefix (a person
# by role, any model, any agency); any other key is one exact actor. The rows of the services that write events here:
#   system:scheduler    the sweep: breaches, escalations, and the bounce of an unanswered referral request
#   system:watchdog     the watchdog's flags
#   system:notifier     `message_sent` for a draft a person approved (the outbox)
#   system:consent      the review flag raised when consent is withdrawn
#   system:automation   the steps that are not a human decision (A21, ADR-032); every one carries `auto: true`
#   model:              a drafting model annotates a case with its draft (T-D2)
#   system:triage       reply triage (D13): the reply and its classification, a review flag for the triage reasons, and a
#                       traceable relay of the patient's own callback request (the one human-only event a service row
#                       holds; `_precheck` limits it to the triage callback reasons with a source_event_id)
#   system:hl7          the HL7 v2 adapter (A28, ADR-039, A34 ADR-042): `book_confirmatory` with the appointment fields from
#                       an SIU message, and `appointment_cancelled` (SIU S15, or a reschedule of a booked case, which
#                       cancels and rebooks); nothing else; no human-only event, no closure, no state change but those two
#   system:agency:      the agency's agents (A22, ADR-033): EMPTY, and it stays empty. Drafting goes through the drafts
#                       route as `model:<provider>`; every other agent only writes its own ledger rows
# Humans are listed once per role. Nothing but this table decides which actor may emit which event type.
ACTOR_EVENTS: dict[str, frozenset[str]] = {
    **{f"{role}:": HUMAN_EVENTS for role in HUMAN_ROLES},
    SCHEDULER_ACTOR_NAME: frozenset({"mark_overdue", "escalate", "handoff_rejected"}),
    WATCHDOG_ACTOR: frozenset({"watchdog_flagged"}),
    NOTIFIER_ACTOR: frozenset({"message_sent"}),
    CONSENT_ACTOR: frozenset({"review_requested"}),
    AUTOMATION_ACTOR: frozenset({
        "acknowledge", "assign_ownership", "complete_confirmatory", "closure_proposed", "handoff_requested",
        "message_sent", "review_requested",
    }),
    MODEL_ACTOR_PREFIX: frozenset({"message_drafted"}),
    TRIAGE_ACTOR: frozenset({"reply_received", "reply_classified", "review_requested", "callback_requested"}),
    AGENCY_ACTOR_PREFIX: frozenset(),
    ERASURE_ACTOR: frozenset({ERASURE_EVENT}),
    HL7_ACTOR: frozenset({"book_confirmatory", "appointment_cancelled"}),
}
# Human-only events (pathway `human_only_events`) a service row may hold, and the rule that makes each one traceable to a
# person: only triage's relay of the patient's own callback request (D13).
RELAYED_HUMAN_EVENTS: dict[str, frozenset[str]] = {TRIAGE_ACTOR: frozenset({"callback_requested"})}
# What the automation may emit, and which of those must say so (payload `auto: true`; nobody else may carry the flag).
AUTOMATION_ALLOWED_EVENTS = ACTOR_EVENTS[AUTOMATION_ACTOR]
AUTO_FLAGGED_EVENTS = AUTOMATION_ALLOWED_EVENTS - {"review_requested"}
# The one actor that may give each review reason (the table says only which actors may emit `review_requested` at all).
REVIEW_EMITTER: dict[str, str] = {
    "consent_withdrawn": CONSENT_ACTOR,
    "reroute_exhausted": AUTOMATION_ACTOR,
    "incomplete_procedure": AUTOMATION_ACTOR,  # A24: the colonoscopy record in HAPI is not closure evidence
    **{reason: TRIAGE_ACTOR for reason in TRIAGE_REVIEW_REASONS},
}
assert set(REVIEW_EMITTER) == set(REVIEW_REASONS)


def actor_key(actor: str) -> str | None:
    """The row of `ACTOR_EVENTS` that governs `actor`, or None (an unknown actor: it may emit nothing)."""
    if actor in ACTOR_EVENTS and not actor.endswith(":"):
        return actor
    return next((k for k in ACTOR_EVENTS if k.endswith(":") and actor.startswith(k) and len(actor) > len(k)), None)


def allowed_events(actor: str) -> frozenset[str]:
    """The event types `actor` may emit (empty for an actor with no row)."""
    key = actor_key(actor)
    return ACTOR_EVENTS[key] if key is not None else frozenset()

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
    # never from a timestamp (SEC-2). A milestone's clock never restarts (A25), so its ids are stable for its life
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
    reroute_exhausted_at: datetime | None = None  # A21: the automation ran out of providers to reroute to
    # A24: the evidence scan found the colonoscopy record and it is not closure evidence: {evidence_ref, flagged_at} until
    # a closure proposal, the close or a reopen replaces it
    incomplete_procedure: dict | None = None
    closure_proposal: dict | None = None  # A21: {evidence_ref, proposed_at, event_id} until close or reopen
    auto_message_times: tuple = ()  # A21: `at` of each message_sent by system:automation (the accounting)
    outbound_message_times: tuple = ()  # `at` of every message_sent, automatic or approved draft (the weekly cap counts all)
    outreach_holds: tuple = ()  # open holds (`opt_out`, `wrong_number`): nothing is sent while any is open (ADR-038)
    contact_confirmed_at: datetime | None = None  # a person confirmed the contact details (`contact_updated`)
    # A triage flag (D13): the latest `review_requested` from system:triage. Kept apart from review_reason (consent) so
    # neither can overwrite or hide the other. Nothing here changes the state; a person sees it on the worklist.
    triage_review_reason: str | None = None
    triage_review_requested_at: datetime | None = None
    backdated_events: int = 0  # T-F4, ADR-027: accepted events dated before the one just before them
    created_at: datetime | None = None  # the genesis event's `at` (a milestone may start there)
    created_event_id: UUID | None = None  # the genesis event's `event_id` (a milestone started there derives its ids from it)
    milestones: dict = field(default_factory=dict)  # name -> MilestoneState (A18); never mutated in place
    erased: bool = False  # A32 (ADR-025): `erasure_completed` is in the log; the case accepts no further event
    # A33 (ADR-042): the informed refusal on record {counselling_ref, counselled_by, reason, recorded_at, event_id,
    # re_offer_due_at}, from `record_informed_refusal` until a reopen; it stays after the declined closure so the case shows
    # its re-offer date. `closure_outcome`: how the case closed ("declined" or "investigated"), None while open.
    refusal: dict | None = None
    closure_outcome: str | None = None

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
            "referral_held": referral_hold(self),
            "last_handoff": dict(self.last_handoff) if self.last_handoff else None,
            "consent": dict(self.consent),
            "consent_history": [dict(h) for h in self.consent_history],
            # consent withdrawal takes precedence (it freezes outreach); then a triage flag; a reroute flag is shown when
            # there is no other. The two flags stay apart in the model (and below), so neither hides the other.
            "review_reason": (self.review_reason or self.triage_review_reason
                              or ("reroute_exhausted" if self.reroute_exhausted_at else None)
                              or ("incomplete_procedure" if self.incomplete_procedure else None)),
            "review_requested_at": (iso(self.review_requested_at) if self.review_reason else
                                    iso(self.triage_review_requested_at) if self.triage_review_reason else
                                    iso(self.reroute_exhausted_at) if self.reroute_exhausted_at else
                                    self.incomplete_procedure["flagged_at"] if self.incomplete_procedure else None),
            "incomplete_procedure": dict(self.incomplete_procedure) if self.incomplete_procedure else None,
            "triage_review_reason": self.triage_review_reason,
            "triage_review_requested_at": iso(self.triage_review_requested_at),
            "closure_proposal": dict(self.closure_proposal) if self.closure_proposal else None,
            "auto_messages_sent": len(self.auto_message_times),
            "outbound_messages_sent": len(self.outbound_message_times),
            "outreach_holds": list(self.outreach_holds),
            "contact_confirmed_at": iso(self.contact_confirmed_at),
            "backdated_events": self.backdated_events,
            "refusal": dict(self.refusal) if self.refusal else None,
            "re_offer_due_at": self.refusal["re_offer_due_at"] if self.refusal else None,
            "closure_outcome": self.closure_outcome,
            "milestones": {name: m.to_json() for name, m in self.milestones.items()},
        }


def referral_hold(model: CaseModel) -> str | None:
    """A36 (ADR-044): why the referral is held, or None. Held while consent is withdrawn or the patient has said their data
    may not go to the specialist: no referral may be requested or accepted, and a pending request waits (it is neither
    cancelled nor bounced) until a person records consent again."""
    if model.consent_frozen or model.consent.get("withdrawn"):
        return "consent_withdrawn"
    if model.consent.get("share_with_specialist") is False:
        return "share_with_specialist_refused"
    return None


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
    """Return the new read model, or raise InvalidTransition. Never mutates anything.

    Replay-safe: it reads the event, the model and the pathway and nothing else. No configuration file, no clock, no
    policy: a log that was accepted once is accepted again whatever has been edited since (the append-time policy gates
    live in app.outreach_policy and app.policy, as guards of the store transaction)."""
    # The agency (A22, ADR-033) is labour, not authority: its row of the table is empty, and it gets its own refusal so
    # the log of a refused attempt says why. Whatever an agent causes is written under another actor's own rules.
    if event.actor.startswith(AGENCY_ACTOR_PREFIX):
        raise InvalidTransition(f"an agency actor may emit no event ({event.type!r} refused; its row of the actor table "
                                "is empty): agents are labour, not authority")
    if event.type not in allowed_events(event.actor):
        raise InvalidTransition(f"actor {event.actor!r} may not emit {event.type!r} (it is not in its row of the actor table)")
    if pathway.milestones:
        model = start_genesis_milestones(model, pathway)
    # A32 (ADR-025): an erased case is final, and the erasure record is written in any state of a CLOSED case (it annotates
    # nothing and moves nothing; the pathway has no transition for it). It is neither activity nor a person looking at the case.
    if model.erased:
        raise InvalidTransition(f"case is erased: {event.type!r} refused (the audit trail is closed)")
    if event.type == ERASURE_EVENT:
        if model.state != pathway.terminal_state:
            raise InvalidTransition(f"{ERASURE_EVENT}: only a closed case can be erased, this one is {model.state!r}")
        return replace(model, last_event_at=_aware(event.at), erased=True)
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


def apply(state: str, event: Event, pathway: Pathway) -> str:
    """State-only convenience over `step` for callers that hold no read model.

    Annotation rules that need history (escalation, resume) will refuse here; the stores
    use `step` with the replayed model.
    """
    return step(CaseModel(state=state), event, pathway).state


# -- transitions ------------------------------------------------------------------------

PHYSICIAN_ROLES = ("primary_care_owner", "specialist_owner")  # who may sign a declined closure (A33; H-9 is open)


def _person_id(identity: str) -> str:
    """The user id inside an actor (`role:user_id`) or a Practitioner reference (`Practitioner/user_id`)."""
    return identity.split("/", 1)[1] if identity.startswith("Practitioner/") else identity.split(":", 1)[-1]


def add_months(at: datetime, months: int) -> datetime:
    """`at` plus calendar months, the day clamped to the end of a shorter month (pure; for the re-offer date)."""
    index = at.year * 12 + (at.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    for day in (at.day, 30, 29, 28):
        try:
            return at.replace(year=year, month=month, day=day)
        except ValueError:
            continue
    raise AssertionError("unreachable")


def signer_resolves_to(actor: str, signed_by) -> bool:
    """A36 (ADR-044, superseding the signer part of ADR-021): `signed_by` names the person who writes the event, as their
    actor string (`role:user_id`) or as their Practitioner (`Practitioner/user_id`). Compared by user id: user ids are
    opaque and per role (A14), and the simulator logs a person under a descriptive role name for the same user id."""
    if not isinstance(signed_by, str) or ":" not in actor:
        return False
    if signed_by.startswith("Practitioner/"):
        signer = signed_by[len("Practitioner/"):]
    elif ":" in signed_by:
        signer = signed_by.split(":", 1)[1]
    else:
        return False
    return signer != "" and signer == actor.split(":", 1)[1]


def _check_signer(event: Event) -> None:
    """The signer is the closer (close, either outcome) and the reviewer (`clinician_review.signed_by`, when given). In the
    machine, so no store, route or in-process caller can write a signature in someone else's name. The route keeps its
    own check (`routers.cases._check_signature_and_evidence`) as a second layer, with the 403 a person sees."""
    signed_by = event.payload.get("signed_by")
    if event.type == "clinician_review" and signed_by is None:
        return
    if not signer_resolves_to(event.actor, signed_by):
        raise InvalidTransition(
            f"{event.type}: signed_by must be the signer's own identity ({event.actor} or "
            f"Practitioner/<their user id>), not {signed_by!r}")


def _check_declined_close(model: CaseModel, event: Event, pathway: Pathway) -> None:
    """A33: closing with outcome `declined`. The declined closure is only for a recorded refusal and a recorded refusal can
    only close as declined. A physician (not the counsellor) signs, and the evidence is the counselling record itself."""
    declined = event.payload.get("outcome") == "declined"
    waiting = model.state == pathway.refusal_state
    if declined != waiting:
        raise InvalidTransition("close: outcome 'declined' is for a case waiting for the sign-off of an informed refusal, and only for that")
    if not declined:
        return
    refusal = model.refusal
    if refusal is None:
        raise InvalidTransition("close: no informed refusal on record")
    if actor_role(event.actor) not in PHYSICIAN_ROLES:
        raise InvalidTransition(f"close (declined): signed by a physician ({list(PHYSICIAN_ROLES)}), not {actor_role(event.actor)!r}")
    if event.payload.get("evidence_ref") != refusal["counselling_ref"]:
        raise InvalidTransition("close (declined): evidence_ref must be the counselling record of the refusal")
    counsellor = _person_id(refusal["counselled_by"])
    if counsellor in (_person_id(event.actor), _person_id(str(event.payload.get("signed_by")))):
        raise InvalidTransition("close (declined): the physician who signs must not be the one who counselled the patient")


def _transition(model: CaseModel, event: Event, pathway: Pathway, t: Transition) -> CaseModel:
    # Closure guard: evidence AND human sign-off, never an automated actor.
    if t.to_state == pathway.terminal_state and is_automated(event.actor):
        raise InvalidTransition("automated actors may not close a case")
    for key in t.requires:
        if not event.payload.get(key):
            raise InvalidTransition(f"event {event.type!r} requires non-empty {key!r}")
    if t.to_state == pathway.terminal_state or event.type == "clinician_review":
        _check_signer(event)  # A36: the signer is the person writing the event
    if t.to_state == pathway.terminal_state:
        _check_declined_close(model, event, pathway)
    for key, minimum in t.minimums:
        value = event.payload.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < minimum:
            raise InvalidTransition(f"event {event.type!r} requires {key!r} >= {minimum}")

    # A closure proposal is for the evidence as it stands: closing consumes it, and a reopen (the evidence was
    # inadequate, or something new was found) withdraws it. The scan may propose again from fresh evidence.
    clears_proposal = t.to_state == pathway.terminal_state or event.type == "reopen"
    resuming = t.to_state == RESUME_STATE
    if resuming:
        if model.resume_state is None:
            raise InvalidTransition(f"event {event.type!r}: no earlier state to resume")
        target = model.resume_state
    else:
        target = t.to_state

    resumable = pathway.resumable_states
    new = replace(model, state=target, consent_frozen=target == CONSENT_WITHDRAWN)
    if clears_proposal:
        new = replace(new, closure_proposal=None, incomplete_procedure=None)
    if model.state == "referral_rejected" and target != "referral_rejected" and target not in pathway.resumable_states:
        new = replace(new, reroute_exhausted_at=None)  # the human re-route the flag asked for has happened
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
    the FIRST `from_event` starts one that has not started and a later one changes nothing (A25: a clock that has
    started never restarts, so a second referral request cannot hand the case a fresh 31 days), entering the state
    that awaits the `until_event` starts one that never got a `from_event` (a case with no formal referral keeps a
    colonoscopy clock, ADR-020), and closing the case voids the unmet ones. A met milestone never changes again."""
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
            elif event.type == d.from_event and cur.started_at is None:
                cur = MilestoneState(started_at=at, started_event_id=event.event_id)  # the first one only; never restarts (A25)
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


def _assign_ownership(model: CaseModel, event: Event, pathway: Pathway) -> CaseModel:
    """A21: the automation names the owner before anyone has accepted (the owner is notified by the ladder if they do
    nothing). Once, while the case is waiting for an owner: it can never take a case from the person who accepted it.
    Only the automation may write it: the actor table says so."""
    if model.owner is not None:
        raise InvalidTransition(f"assign_ownership: the case already has an owner ({model.owner})")
    if model.state not in ("result_received", "acknowledged"):
        raise InvalidTransition(f"assign_ownership: not allowed from state {model.state!r}; ownership is assigned before it is accepted")
    return model  # the owner and provider are set in _finish with the other ownership events


# States in which closure evidence can be proposed: the colonoscopy is completed (by a person, or by the automation in
# the same scan) and the clinician has not yet closed the case.
CLOSURE_PROPOSAL_STATES = ("confirmatory_completed", "clinician_reviewed")
# States in which the evidence scan may flag an incomplete colonoscopy record (A24): the ones it scans.
INCOMPLETE_PROCEDURE_STATES = ("confirmatory_booked", *CLOSURE_PROPOSAL_STATES)


def _closure_proposed(model: CaseModel, event: Event, pathway: Pathway) -> CaseModel:
    """A21: record that the evidence scan found closure evidence. Changes no state and closes nothing; it only gives the
    clinician a one-click sign-off with the evidence prefilled. `close` is unaffected (still evidence and a person).
    Only the automation may write it: the actor table says so."""
    if model.state not in CLOSURE_PROPOSAL_STATES:
        raise InvalidTransition(f"closure_proposed: not allowed from state {model.state!r}")
    return replace(model, incomplete_procedure=None, closure_proposal={  # complete evidence was found: the flag is moot
        "evidence_ref": event.payload["evidence_ref"],
        "proposed_at": _aware(event.at).isoformat(),
        "event_id": str(event.event_id),
    })


# Annotations without an entry here have no effect on the read model beyond last_event_at
# (handoff_requested, message_*, callback_requested, watchdog_flagged, consent_updated
# outside the withdrawn transitions).
_ANNOTATION_RULES = {
    "assign_ownership": _assign_ownership,
    "closure_proposed": _closure_proposed,
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
    elif kind == "closure_proposed":
        if not isinstance(payload.get("evidence_ref"), str) or not payload["evidence_ref"]:
            raise PayloadError("closure_proposed needs the evidence_ref it found")
    elif kind == "assign_ownership":
        if not isinstance(payload.get("owner"), str) or not payload["owner"]:
            raise PayloadError("assign_ownership needs the owner it names")
    elif kind == "record_informed_refusal":
        if payload.get("reason") not in pathway.refusal_reasons:
            raise PayloadError(f"record_informed_refusal reason must be one of {list(pathway.refusal_reasons)}")
    elif kind == "appointment_cancelled":
        if payload.get("reason") not in pathway.appointment_cancel_reasons:
            raise PayloadError(f"appointment_cancelled reason must be one of {list(pathway.appointment_cancel_reasons)}")
        if not isinstance(payload.get("by_patient"), bool):
            raise PayloadError("appointment_cancelled needs by_patient (true or false)")
    elif kind == "contact_updated":
        if payload.get("contact_valid") is not True:
            raise PayloadError("contact_updated says the contact details are valid: contact_valid must be true")
    # A21: the `auto` flag is the automation's alone, and the automation always sets it (an auto event is never
    # mistaken for a person's, and a person never passes for the automation).
    if payload.get("auto") is not None and actor != AUTOMATION_ACTOR:
        raise PayloadError(f"{kind}: 'auto' may be set only by {AUTOMATION_ACTOR!r}")
    if actor == AUTOMATION_ACTOR and kind in AUTO_FLAGGED_EVENTS and payload.get("auto") is not True:
        raise PayloadError(f"{kind} from {AUTOMATION_ACTOR!r} must carry auto: true")
    if kind == "message_sent":
        has_template = payload.get("template_id") is not None or payload.get("template_version") is not None
        if payload.get("auto") is True:
            if payload.get("template_id") is None or payload.get("template_version") is None:
                raise PayloadError("an automatic message_sent names its template_id and template_version")
            if payload.get("draft_id") is not None:
                raise PayloadError("an automatic message_sent is a template, not a draft: no draft_id")
            if payload.get("template_id") != f"{payload.get('purpose')}.{payload.get('channel')}":
                raise PayloadError("an automatic message_sent's template_id is '<purpose>.<channel>'")
        elif has_template:
            raise PayloadError("template_id and template_version belong to an automatic message_sent (auto: true)")


def _precheck(model: CaseModel, event: Event, pathway: Pathway) -> None:
    kind, actor = event.type, event.actor
    # Which actor may emit which event TYPE is the actor table, applied in `step`. What it cannot say is which actor may
    # give which REASON: each review reason has exactly one emitter (REVIEW_EMITTER).
    if kind == "review_requested":
        reason = event.payload.get("reason")
        expected = REVIEW_EMITTER.get(reason) if isinstance(reason, str) else None
        if expected is not None and actor != expected:  # an unknown reason is validate_payload's (PayloadError)
            raise InvalidTransition(f"review_requested with reason {reason!r} is emitted only by {expected!r}")
    # The triage row holds one human-only event, `callback_requested`, and only as a relay of the patient's own request
    # (D13): a reason from the triage list and the classification it came from, so the request traces to the reply.
    if kind == "callback_requested" and actor == TRIAGE_ACTOR:
        if event.payload.get("reason") not in TRIAGE_CALLBACK_REASONS or not event.payload.get("source_event_id"):
            raise InvalidTransition(
                f"{TRIAGE_ACTOR!r} relays only a callback the patient asked for: reason one of "
                f"{list(TRIAGE_CALLBACK_REASONS)} and the source_event_id of the classification")
    validate_payload(event, pathway)
    if kind == "review_requested" and event.payload.get("reason") == "reroute_exhausted":
        if model.state != "referral_rejected" or model.pending_handoff is not None:
            raise InvalidTransition("reroute_exhausted: only a case in referral_rejected with no pending request")
    if kind == "review_requested" and event.payload.get("reason") == "incomplete_procedure":
        if model.state not in INCOMPLETE_PROCEDURE_STATES:
            raise InvalidTransition(f"incomplete_procedure: not allowed from state {model.state!r}")
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
    if kind == "message_sent" and actor == AUTOMATION_ACTOR:
        _check_automatic_message(model, event)
    elif kind == "message_sent":  # the notifier, by the table
        draft_id = event.payload.get("draft_id")
        if draft_id is None or draft_id not in model.approved_unsent:
            raise InvalidTransition(
                f"message_sent: draft {draft_id!r} is not an approved, unsent draft of this case"
            )
        # An open opt-out or wrong-number hold stops an approved draft too (A27, ADR-038): the approval may predate the
        # patient's "stop" or the wrong-number reply, and the outbox may send it later (a retry, the next drain). A
        # person clears the hold first (`consent_updated` / `contact_updated`), as for an automatic message.
        if model.outreach_holds:
            raise InvalidTransition(
                f"message_sent: outreach is held for this case ({', '.join(model.outreach_holds)}): "
                "a person must clear the flag before an approved draft goes out")
        consented, why = channel_consent(model, event.payload.get("channel"))
        if not consented:
            raise InvalidTransition(f"message_sent: {why}")
    pending = model.pending_handoff
    # A36 (ADR-044): no referral is requested or accepted while it is held (consent withdrawn, or sharing with the
    # specialist refused); a pending request waits, and the scheduler may not bounce it while it waits.
    held = referral_hold(model)
    if held is not None and kind in REFERRAL_HELD_EVENTS:
        raise InvalidTransition(
            f"referral held ({held}): {kind!r} is refused while the patient's consent is withdrawn or they do not "
            "allow sharing with the specialist; a pending request waits until a person records consent again")
    if (held is not None and kind == "handoff_rejected" and actor == SCHEDULER_ACTOR_NAME
            and event.payload.get("bounced") is True):
        raise InvalidTransition(f"referral held ({held}): the bounce timer does not run while the request is held")
    if kind == "handoff_requested":
        if actor == AUTOMATION_ACTOR and model.state != "referral_rejected":
            raise InvalidTransition(
                "handoff_requested from the automation is a reroute: only for a case in referral_rejected"
            )
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


def _check_automatic_message(model: CaseModel, event: Event) -> None:
    """What the machine itself checks of a message the automation sends without a draft (A21, ADR-032): only what the
    log decides, so that replaying the log never depends on a file. The patient must consent to the channel NOW, and no
    outreach hold (an opt-out or a wrong number flagged by triage) may be open. The payload's shape (`auto`, template id
    and version, no draft) is `validate_payload`'s.

    NOT here: the template allow-list and versions, and the weekly cap. They come from config/outreach_policy.yaml, which
    can be edited or fail to load after the event was written, so they are checked at APPEND time, inside the store
    transaction, under the case lock, by `outreach_policy.auto_message_guard`, and never when a log is replayed."""
    payload = event.payload
    if model.outreach_holds:
        raise InvalidTransition(
            f"message_sent: automatic messages are held for this case ({', '.join(model.outreach_holds)}): "
            "a person must clear the flag first")
    consented, why = channel_consent(model, payload.get("channel"))
    if not consented:
        raise InvalidTransition(f"message_sent: {why}")


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
    if kind == "record_informed_refusal":
        months = pathway.re_offer_months if pathway is not None else 12
        out = replace(out, refusal={
            "counselling_ref": payload["counselling_ref"], "counselled_by": payload["counselled_by"],
            "reason": payload["reason"], "recorded_at": at.isoformat(), "event_id": str(event.event_id),
            "re_offer_due_at": add_months(at, months).isoformat(),
        })
    elif kind == "close":
        out = replace(out, closure_outcome=payload.get("outcome") or "investigated")
    elif kind == "reopen":
        out = replace(out, refusal=None, closure_outcome=None)
    if kind == "accept_ownership":
        # the provider the owner acts for: the one they name, else the one the automation assigned them under (A21)
        kept = (before or model).provider if (before or model).owner == event.actor else None
        out = replace(out, owner=event.actor, provider=payload.get("provider") or kept)
    elif kind == "assign_ownership":
        out = replace(out, owner=payload["owner"], provider=payload.get("provider"))
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
        out = replace(out, approved_unsent=tuple(d for d in out.approved_unsent if d != payload.get("draft_id")),
                      outbound_message_times=(*out.outbound_message_times, at))
        if payload.get("auto") is True:
            out = replace(out, auto_message_times=(*out.auto_message_times, at))
    elif kind == "consent_updated":
        out = _clear_hold(_consent_after(out, event, (before or model).consent_frozen), "opt_out")
        if out.pending_handoff and referral_hold(before or model) is not None and referral_hold(out) is None:
            # A36: the hold on a pending referral ends; its bounce timer starts again, in full, from now
            out = replace(out, pending_handoff={**out.pending_handoff, "resumed_at": at.isoformat()})
    elif kind == "contact_updated":
        out = _clear_hold(replace(out, contact_confirmed_at=at), "wrong_number")
    elif kind == "review_requested" and payload["reason"] == "reroute_exhausted":
        out = replace(out, reroute_exhausted_at=at)
    elif kind == "review_requested" and payload["reason"] == "incomplete_procedure":
        out = replace(out, incomplete_procedure={"evidence_ref": payload["evidence_ref"], "flagged_at": at.isoformat()})
    elif kind == "review_requested" and event.actor == TRIAGE_ACTOR:
        out = replace(out, triage_review_reason=payload["reason"], triage_review_requested_at=at)
        if payload["reason"] in OUTREACH_HOLD_REASONS and payload["reason"] not in out.outreach_holds:
            out = replace(out, outreach_holds=(*out.outreach_holds, payload["reason"]))
    elif kind == "review_requested":  # consent_withdrawn, from system:consent
        out = replace(out, review_reason=payload["reason"], review_requested_at=at)
    return out


def _clear_hold(model: CaseModel, reason: str) -> CaseModel:
    """A person dealt with the flag: `consent_updated` clears an opt-out hold, `contact_updated` a wrong-number hold. The
    triage flag the hold came with goes too, unless something else has since replaced it, or the person's answer was to
    withdraw consent: then the case is in consent review and the reviewer keeps the triage reason that led there."""
    if reason not in model.outreach_holds:
        return model
    out = replace(model, outreach_holds=tuple(h for h in model.outreach_holds if h != reason))
    if out.triage_review_reason == reason and not out.consent_frozen:
        out = replace(out, triage_review_reason=None, triage_review_requested_at=None)
    return out
