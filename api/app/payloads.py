"""Per-event-type payload schemas (A14, ADR-019): what may enter the immutable event log.

The event log cannot be edited or erased, so free text must not reach it. Every event type has
a strict pydantic model here (`extra="forbid"`, strict types): a key that is not in the model is
rejected, a string with a shape that could carry a name is rejected by its pattern, and the only
free text anywhere is

* `reason`, which is always a member of a fixed taxonomy (never prose), and
* `note`, at most 200 characters, on the events a human posts. This is the one deliberate hole;
  the documentation tells staff not to put patient identifiers or clinical detail in it.

Draft text, message bodies and display names live in erasable stores (`drafts`, `notifications`,
`users`), and the log holds only their ids and hashes. `message_drafted` therefore carries
`draft_id, provider, fallback_used, prompt_hash, language` and no text.

`validate(event_type, payload)` raises `PayloadSchemaError`. The API calls it before anything is
written (HTTP 422), and the in-process emitters (sweep, consent, notify) call it too, so a bug
there cannot put free text in the log either. The state machine's own `validate_payload` keeps
the semantic rules (taxonomy membership for handoff reasons, the scheduler-only bounce, ...).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator, model_validator

NOTE_MAX_LENGTH = 200

# ---- taxonomies: the only values a `reason` field may take ------------------------------------
REOPEN_REASONS = ("new_finding", "evidence_inadequate", "closed_in_error", "patient_request", "other")
CALLBACK_REASONS = ("patient_does_not_understand", "wants_to_speak_to_clinician", "cannot_attend", "reschedule", "other")
WATCHDOG_REASONS = ("idle", "scheduler_down")
# review_requested, one emitter per reason (state_machine.REVIEW_EMITTER): consent_withdrawn by system:consent (A6);
# reroute_exhausted by system:automation (A21); opt_out, wrong_number, triage_uncertain and already_done by system:triage
# (D13) from a patient's inbound reply. An opt_out or wrong_number holds automatic messages until a person clears it.
# incomplete_procedure by system:automation (A24, ADR-036): the evidence scan found the case's colonoscopy record in HAPI
# and it is not closure evidence (incomplete, stopped, not final, or no final report): a person decides what comes next.
REVIEW_REASONS = ("consent_withdrawn", "reroute_exhausted", "opt_out", "wrong_number", "triage_uncertain", "already_done",
                  "incomplete_procedure")
REPLY_INTENTS = ("confirm", "reschedule", "cannot_attend", "wrong_number", "dont_understand", "opt_out", "question",
                 "already_done", "other")
# handoff_rejected: the human taxonomy is in the pathway YAML (handoff_reject_reasons) and is
# enforced by the state machine; this model only requires a short code, so the YAML stays the
# single source for it.
# message_rejected (A16): why a human refused an AI-drafted message. `other_with_note` carries its note on
# the draft row (erasable), never in the log.
MESSAGE_REJECT_REASONS = ("inaccurate", "tone", "wrong_language", "not_needed", "other_with_note")
CHANNELS = ("sms", "whatsapp", "phone", "letter")
LANGUAGES = ("en", "ar")

# ---- value shapes: ids and codes, never prose ---------------------------------------------------
def _no_dot_segment(value: str) -> str:
    """No path segment of a reference may be made only of dots (`.`, `..`): such an id turns into a
    traversal when it is put in a URL (SEC-1). The regex engine here has no lookahead, so it is a validator."""
    if any(segment == "" or set(segment) == {"."} for segment in value.split("/")):
        raise ValueError("a reference may not contain an empty or dots-only segment")
    return value


_REF = r"^[A-Za-z0-9][A-Za-z0-9/._-]{0,99}$"  # a generic id or reference (an appointment): no spaces
Ref = Annotated[str, StringConstraints(pattern=_REF), AfterValidator(_no_dot_segment)]
# The two references that are looked up in HAPI and put in request URLs (SEC-1): the resource type is fixed and
# the id has no dots at all, so `Patient/..` and `DiagnosticReport/../x` cannot be written, let alone requested.
PatientRef = Annotated[str, StringConstraints(pattern=r"^Patient/[A-Za-z0-9-]{1,64}$")]
ResultRef = Annotated[str, StringConstraints(pattern=r"^DiagnosticReport/[A-Za-z0-9-]{1,64}$")]
# complete_confirmatory carries the confirmatory result, which the recorder (E8) returns as the colonoscopy Procedure
# (the same reference `close` later cites) or its DiagnosticReport; the same dot-free id rule.
ConfirmatoryResultRef = Annotated[str, StringConstraints(pattern=r"^(DiagnosticReport|Procedure)/[A-Za-z0-9-]{1,64}$")]
Code = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]  # a taxonomy value or state name
Provider = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._@/-]{0,99}$", strip_whitespace=True)]
ActorId = Annotated[str, StringConstraints(pattern=r"^[a-z_]{3,30}:[A-Za-z0-9_-]{1,64}$")]
Uuid = Annotated[str, StringConstraints(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
# A booked appointment's date for the patient message (D4): an ISO-8601 date or date-time, nothing else.
IsoDate = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}(T[0-9:.+Zz-]{1,40})?$")]
# Who a case can be reassigned to (SEC-1): a person acting in one of the staff roles, `role:user_id`. Never a patient
# (a patient does not own a case) and never an automated actor (system, scheduler, watchdog, model: none of them can
# log in, so the case would have an owner nobody can be). tests/test_payloads.py keeps the role list equal to
# app.auth.USER_ROLES minus the patient.
OwnerActor = Annotated[
    str,
    StringConstraints(pattern=r"^(lab|primary_care_owner|specialist_owner|coordinator|supervisor):[A-Za-z0-9_-]{1,64}$"),
]
# Closure evidence is a FHIR report or procedure, by reference (A15).
EvidenceRef = Annotated[
    str, StringConstraints(pattern=r"^(DiagnosticReport|Procedure)/[A-Za-z0-9.-]{1,64}$"), AfterValidator(_no_dot_segment)
]
# The clinician who signs is an actor (`role:user_id`) or the Practitioner mapped to it (A15).
Signer = Annotated[
    str,
    StringConstraints(pattern=r"^(Practitioner/[A-Za-z0-9.-]{1,64}|[a-z_]{3,30}:[A-Za-z0-9_-]{1,64})$"),
    AfterValidator(_no_dot_segment),
]
Note = Annotated[str, StringConstraints(max_length=NOTE_MAX_LENGTH)]


class PayloadSchemaError(ValueError):
    """The payload has a key or a value the event type's schema does not allow."""


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Human(_Payload):
    """Events a person posts: they may add a short note (the one free-text field)."""

    note: Note | None = None


def _iso(value: str) -> str:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("must be an ISO-8601 timestamp") from None
    return value


IsoTime = Annotated[str, StringConstraints(max_length=40)]


class _Timed(_Payload):
    @field_validator("*", mode="after")
    @classmethod
    def _check_times(cls, value, info):
        if info.field_name in ("due_at", "rung_due_at", "requested_at", "last_activity_at") and value is not None:
            _iso(value)
        return value


# ---- the models ---------------------------------------------------------------------------------


class CaseCreated(_Payload):
    patient_ref: PatientRef | None = None
    result_ref: ResultRef | None = None
    contact_valid: bool | dict[Literal["sms", "whatsapp", "phone", "letter"], bool] | None = None
    pathway: Code | None = None
    # A21: the primary-care provider the patient is registered with (config/providers.yaml maps it to the default owner)
    registered_provider: Provider | None = None


class Acknowledge(_Human):
    auto: Literal[True] | None = None  # A21: set only by system:automation (the state machine checks who)


class AcceptOwnership(_Human):
    provider: Provider | None = None


class ContactPatient(_Human):
    channel: Literal["sms", "whatsapp", "phone", "letter"] | None = None
    draft_id: Uuid | None = None


class PatientAcknowledge(_Human):
    lang: Literal["en", "ar"] | None = None


class BookConfirmatory(_Human):
    appointment_ref: Ref | None = None
    appointment_date: IsoDate | None = None  # read by the drafts route (D4) for confirmations
    appointment_at: IsoDate | None = None


class CompleteConfirmatory(_Human):
    result_ref: ConfirmatoryResultRef | None = None
    auto: Literal[True] | None = None  # A21: the evidence scan found the colonoscopy in HAPI


class ClinicianReview(_Human):
    signed_by: Signer | None = None


class Close(_Human):
    evidence_ref: EvidenceRef
    signed_by: Signer


class Reopen(_Human):
    reason: Literal["new_finding", "evidence_inadequate", "closed_in_error", "patient_request", "other"]


def _state_xor_milestone(model: "_Payload") -> "_Payload":
    """A18: a breach is of the current state's SLA (`state`) or of a milestone (`milestone`), never both and never
    neither. A milestone spans states, so a milestone breach does not name one."""
    if (model.state is None) == (model.milestone is None):  # type: ignore[attr-defined]
        raise ValueError("exactly one of 'state' and 'milestone' is required")
    return model


class MarkOverdue(_Timed):
    state: Code | None = None
    milestone: Code | None = None  # A18: a milestone breach (referral_requested, confirmatory_completed)
    due_at: IsoTime
    sla_event: Code | None = None
    sla_working_days: int | None = Field(default=None, ge=0, le=3650)

    _one_subject = model_validator(mode="after")(_state_xor_milestone)


class AlsoFor(_Timed):
    """A ladder whose rung an `escalate` covers as well (A19, ADR-031): the escalation is one message to one role for
    several breaches. The ladder is named like the escalation names its own (`state` or `milestone`, exactly one)."""

    state: Code | None = None
    milestone: Code | None = None
    rung: Code
    level: int = Field(ge=1, le=20)
    due_at: IsoTime
    rung_due_at: IsoTime

    _one_subject = model_validator(mode="after")(_state_xor_milestone)


class Escalate(_Timed):
    rung: Code
    state: Code | None = None
    milestone: Code | None = None  # A18: the rung is on a milestone's ladder
    level: int | None = Field(default=None, ge=0, le=20)
    target: Code | None = None
    action: Code | None = None
    due_at: IsoTime | None = None
    rung_due_at: IsoTime | None = None
    note: Note | None = None  # a coordinator or supervisor escalating by hand may add one
    # A19: ladders whose same-target rung falls in the same sweep ride on this one escalation, one entry each ...
    also_for: Annotated[list[AlsoFor], Field(min_length=1, max_length=10)] | None = None
    # ... and a rung to a role that another ladder of the case already escalated on the same working day is
    # recorded as attached to that escalation's event: it notifies nobody and is not counted as a new escalation
    attached_to: Uuid | None = None

    _one_subject = model_validator(mode="after")(_state_xor_milestone)

    @model_validator(mode="after")
    def _not_both(self):
        if self.also_for is not None and self.attached_to is not None:
            raise ValueError("'also_for' and 'attached_to' cannot be combined")
        return self


class EscalationAccepted(_Human):
    rung: Code | None = None
    milestone: Code | None = None  # A18: which ladder, when more than one has a rung


class HandoffRequested(_Human):
    to_provider: Provider
    auto: Literal[True] | None = None  # A21: an automatic reroute after a rejected or bounced referral


class AssignOwnership(_Payload):
    """A21: the automation names the case's owner from config/providers.yaml, without the owner's acceptance. No
    note: it is not a human's event. `owner` is the opaque `primary_care_owner:<id>` actor, never a name."""

    owner: Annotated[str, StringConstraints(pattern=r"^primary_care_owner:[A-Za-z0-9_-]{1,64}$")]
    provider: Provider | None = None
    auto: Literal[True]


class ClosureProposed(_Payload):
    """A21: the evidence scan found closure evidence for the case. A proposal only: the clinician reviews and signs."""

    evidence_ref: EvidenceRef
    auto: Literal[True]


class HandoffAccepted(_Human):
    provider: Provider | None = None


class HandoffRejected(_Timed):
    reason: Code
    note: Note | None = None
    # present on the scheduler's bounce only (the state machine checks who may send them)
    bounced: bool | None = None
    to_provider: Provider | None = None
    returned_to: ActorId | None = None
    requested_at: IsoTime | None = None
    due_at: IsoTime | None = None
    working_days: int | None = Field(default=None, ge=0, le=3650)


class Reassign(_Human):
    owner: OwnerActor
    provider: Provider | None = None


class ConsentUpdated(_Human):
    channel_sms: bool | None = None
    channel_whatsapp: bool | None = None
    channel_phone: bool | None = None
    channel_letter: bool | None = None
    share_with_specialist: bool | None = None
    withdrawn: bool | None = None


class MarkLostContact(_Human):
    failed_attempts: int = Field(ge=0, le=1000)


class ContactUpdated(_Human):
    """A person confirmed the patient's contact details (the number or address was wrong and has been put right, or was
    checked and is fine). It carries no number and no address: those live outside the log. `contact_valid` is always true;
    it clears a `wrong_number` hold and lifts a `contact_valid: false` on the case_created payload."""

    contact_valid: Literal[True]


class ReviewRequested(_Payload):
    reason: Literal["consent_withdrawn", "reroute_exhausted", "opt_out", "wrong_number", "triage_uncertain", "already_done",
                    "incomplete_procedure"]
    # the withdrawal (consent), the rejection the reroute ran out after (automation), the `reply_classified` event
    # (triage) that caused the flag, or, for incomplete_procedure, the event whose clock the evidence was awaited under
    source_event_id: Uuid
    # incomplete_procedure only (A24): the colonoscopy record in HAPI that is not closure evidence, by reference.
    evidence_ref: EvidenceRef | None = None

    @model_validator(mode="after")
    def _evidence_ref_iff_incomplete(self) -> "ReviewRequested":
        if (self.reason == "incomplete_procedure") != (self.evidence_ref is not None):
            raise ValueError("evidence_ref belongs to reason incomplete_procedure, and that reason needs it")
        return self


class ReplyReceived(_Payload):
    """A patient wrote back (D13). The text is NOT here: it is in the erasable `patient_replies` table, and the log
    holds only the id that finds it (`text_ref`) and the channel it came in on."""

    channel: Literal["sms", "whatsapp", "phone", "letter"]
    text_ref: Uuid


class ReplyClassified(_Payload):
    """What the reply triage made of a reply (D13): intent, confidence, which model, and whether a person must look.
    No text. `text_ref` ties it to the `reply_received` event and the stored reply."""

    intent: Literal["confirm", "reschedule", "cannot_attend", "wrong_number", "dont_understand", "opt_out",
                    "question", "already_done", "other"]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    model_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,63}$")]
    needs_human: bool
    text_ref: Uuid


class MessageDrafted(_Payload):
    """No text: the draft lives in the `drafts` table; the log holds its id and hash."""

    draft_id: Uuid
    provider: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")]
    fallback_used: bool
    prompt_hash: Sha256
    language: Literal["en", "ar"]


class MessageApproved(_Human):
    draft_id: Uuid


class MessageRejected(_Payload):
    """A human refused a draft (A16). Reason from the fixed taxonomy and no text: the note for
    `other_with_note` lives on the draft row, like the draft text."""

    draft_id: Uuid
    reason: Literal["inaccurate", "tone", "wrong_language", "not_needed", "other_with_note"]


class MessageSent(_Payload):
    notification_id: Uuid
    channel: Literal["sms", "whatsapp", "phone", "letter"]
    language: Literal["en", "ar"]
    purpose: Code
    draft_id: Uuid | None = None
    # A21: a pre-approved deterministic template sent by system:automation, with no draft. The state machine accepts
    # these only for a template in config/outreach_policy.yaml at the version listed there.
    auto: Literal[True] | None = None
    template_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}\.[a-z]{2,16}$")] | None = None
    template_version: int | None = Field(default=None, ge=1, le=10000)


class CallbackRequested(_Human):
    reason: Literal["patient_does_not_understand", "wants_to_speak_to_clinician", "cannot_attend", "reschedule", "other"] | None = None
    lang: Literal["en", "ar"] | None = None
    source_event_id: Uuid | None = None  # D13: the `reply_classified` event a triage-relayed request came from


class WatchdogFlagged(_Timed):
    reason: Literal["idle", "scheduler_down"]
    idle_days: int | None = Field(default=None, ge=0, le=36500)
    last_activity_at: IsoTime | None = None


SCHEMAS: dict[str, type[BaseModel]] = {
    "case_created": CaseCreated,
    "acknowledge": Acknowledge,
    "accept_ownership": AcceptOwnership,
    "contact_patient": ContactPatient,
    "patient_acknowledge": PatientAcknowledge,
    "book_confirmatory": BookConfirmatory,
    "complete_confirmatory": CompleteConfirmatory,
    "clinician_review": ClinicianReview,
    "close": Close,
    "reopen": Reopen,
    "mark_overdue": MarkOverdue,
    "escalate": Escalate,
    "escalation_accepted": EscalationAccepted,
    "handoff_requested": HandoffRequested,
    "handoff_accepted": HandoffAccepted,
    "handoff_rejected": HandoffRejected,
    "assign_ownership": AssignOwnership,
    "closure_proposed": ClosureProposed,
    "reassign": Reassign,
    "consent_updated": ConsentUpdated,
    "contact_updated": ContactUpdated,
    "mark_lost_contact": MarkLostContact,
    "review_requested": ReviewRequested,
    "reply_received": ReplyReceived,
    "reply_classified": ReplyClassified,
    "message_drafted": MessageDrafted,
    "message_approved": MessageApproved,
    "message_rejected": MessageRejected,
    "message_sent": MessageSent,
    "callback_requested": CallbackRequested,
    "watchdog_flagged": WatchdogFlagged,
}


def validate(event_type: str, payload: dict) -> None:
    """Raise PayloadSchemaError unless `payload` is exactly what `event_type` allows."""
    model = SCHEMAS.get(event_type)
    if model is None:
        raise PayloadSchemaError(f"no payload schema for event type {event_type!r}")
    if not isinstance(payload, dict):
        raise PayloadSchemaError("payload must be a JSON object")
    try:
        model.model_validate(payload)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'payload'}: {err['msg']}" for err in exc.errors()
        )
        raise PayloadSchemaError(f"{event_type} payload rejected: {problems}") from None


def allowed_keys(event_type: str) -> tuple[str, ...]:
    """The keys an event type's payload may carry (for documentation and tests)."""
    return tuple(SCHEMAS[event_type].model_fields)
