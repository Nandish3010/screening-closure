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

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

NOTE_MAX_LENGTH = 200

# ---- taxonomies: the only values a `reason` field may take ------------------------------------
REOPEN_REASONS = ("new_finding", "evidence_inadequate", "closed_in_error", "patient_request", "other")
CALLBACK_REASONS = ("patient_does_not_understand", "wants_to_speak_to_clinician", "cannot_attend", "other")
WATCHDOG_REASONS = ("idle", "scheduler_down")
REVIEW_REASONS = ("consent_withdrawn",)
# handoff_rejected: the human taxonomy is in the pathway YAML (handoff_reject_reasons) and is
# enforced by the state machine; this model only requires a short code, so the YAML stays the
# single source for it.
CHANNELS = ("sms", "whatsapp", "phone", "letter")
LANGUAGES = ("en", "ar")

# ---- value shapes: ids and codes, never prose ---------------------------------------------------
_REF = r"^[A-Za-z0-9][A-Za-z0-9/._-]{0,99}$"  # FHIR-style reference or id: no spaces
Ref = Annotated[str, StringConstraints(pattern=_REF)]
Code = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]  # a taxonomy value or state name
Provider = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._@/-]{0,99}$", strip_whitespace=True)]
ActorId = Annotated[str, StringConstraints(pattern=r"^[a-z_]{3,30}:[A-Za-z0-9_-]{1,64}$")]
Uuid = Annotated[str, StringConstraints(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
# Closure evidence is a FHIR report or procedure, by reference (A15).
EvidenceRef = Annotated[str, StringConstraints(pattern=r"^(DiagnosticReport|Procedure)/[A-Za-z0-9.-]{1,64}$")]
# The clinician who signs is an actor (`role:user_id`) or the Practitioner mapped to it (A15).
Signer = Annotated[str, StringConstraints(pattern=r"^(Practitioner/[A-Za-z0-9.-]{1,64}|[a-z_]{3,30}:[A-Za-z0-9_-]{1,64})$")]
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
    patient_ref: Ref | None = None
    result_ref: Ref | None = None
    contact_valid: bool | dict[Literal["sms", "whatsapp", "phone", "letter"], bool] | None = None
    pathway: Code | None = None


class Acknowledge(_Human):
    pass


class AcceptOwnership(_Human):
    provider: Provider | None = None


class ContactPatient(_Human):
    channel: Literal["sms", "whatsapp", "phone", "letter"] | None = None
    draft_id: Uuid | None = None


class PatientAcknowledge(_Human):
    lang: Literal["en", "ar"] | None = None


class BookConfirmatory(_Human):
    appointment_ref: Ref | None = None


class CompleteConfirmatory(_Human):
    result_ref: Ref | None = None


class ClinicianReview(_Human):
    signed_by: Signer | None = None


class Close(_Human):
    evidence_ref: EvidenceRef
    signed_by: Signer


class Reopen(_Human):
    reason: Literal["new_finding", "evidence_inadequate", "closed_in_error", "patient_request", "other"]


class MarkOverdue(_Timed):
    state: Code
    due_at: IsoTime
    sla_event: Code | None = None
    sla_working_days: int | None = Field(default=None, ge=0, le=3650)


class Escalate(_Timed):
    rung: Code
    state: Code
    level: int | None = Field(default=None, ge=0, le=20)
    target: Code | None = None
    action: Code | None = None
    due_at: IsoTime | None = None
    rung_due_at: IsoTime | None = None
    note: Note | None = None  # a coordinator or supervisor escalating by hand may add one


class EscalationAccepted(_Human):
    rung: Code | None = None


class HandoffRequested(_Human):
    to_provider: Provider


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
    owner: ActorId
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


class ReviewRequested(_Payload):
    reason: Literal["consent_withdrawn"]
    source_event_id: Uuid


class MessageDrafted(_Payload):
    """No text: the draft lives in the `drafts` table; the log holds its id and hash."""

    draft_id: Uuid
    provider: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")]
    fallback_used: bool
    prompt_hash: Sha256
    language: Literal["en", "ar"]


class MessageApproved(_Human):
    draft_id: Uuid


class MessageSent(_Payload):
    notification_id: Uuid
    channel: Literal["sms", "whatsapp", "phone", "letter"]
    language: Literal["en", "ar"]
    purpose: Code
    draft_id: Uuid | None = None


class CallbackRequested(_Human):
    reason: Literal["patient_does_not_understand", "wants_to_speak_to_clinician", "cannot_attend", "other"] | None = None
    lang: Literal["en", "ar"] | None = None


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
    "reassign": Reassign,
    "consent_updated": ConsentUpdated,
    "mark_lost_contact": MarkLostContact,
    "review_requested": ReviewRequested,
    "message_drafted": MessageDrafted,
    "message_approved": MessageApproved,
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
