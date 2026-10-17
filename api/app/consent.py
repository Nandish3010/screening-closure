"""Consent ledger helpers (A6, ADR-017).

The ledger itself is the event log: every `consent_updated` is a row, and the current consent
(`consent`, `consent_history`, `consent_frozen`) is derived by replay in `app.state_machine`.
This module holds the two things that sit around it:

* `channel_allowed` - may we contact the patient on this channel? (used by the notification mock)
* `ensure_review_requested` - when consent is withdrawn the API itself flags the case for human
  review with a `review_requested` event from `system:consent`. That actor may emit nothing
  else (the state machine enforces it) and no route can post the event.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from uuid import UUID

from app import payloads
from app.events import Event, EventStore
from app.pathway import Pathway
from app.state_machine import CONSENT_ACTOR, CaseModel

# Fixed namespace for deterministic event ids (never change: it would break idempotency).
CONSENT_NAMESPACE = uuid.UUID("0b0f3b86-5a63-4f0a-9a43-3c1d3f5e7a10")
CHANNELS = ("sms", "whatsapp", "phone", "letter")


def channel_allowed(model: CaseModel, channel: str) -> tuple[bool, str]:
    """(allowed, reason). Not allowed when consent is withdrawn or the channel is switched off."""
    if model.consent_frozen or model.consent.get("withdrawn"):
        return False, "consent withdrawn"
    if channel not in CHANNELS:
        return False, f"unknown channel {channel!r}"
    if not model.consent.get(f"channel_{channel}", False):
        return False, f"patient has not consented to contact by {channel}"
    return True, "ok"


def review_event_id(trigger_event_id: UUID) -> UUID:
    return uuid.uuid5(CONSENT_NAMESPACE, f"{trigger_event_id}|review_requested")


def ensure_review_requested(
    store: EventStore, pathway: Pathway, case_id: UUID, trigger_event_id: UUID, at: datetime
) -> Event | None:
    """If the case is frozen and no review has been requested, append `review_requested`.

    Idempotent: the id derives from the withdrawing event, so a client retry of that event
    re-runs this and heals a flag that failed to write the first time. Returns the event it
    appended (None if a review was already flagged or the case is not frozen).
    """
    model = store.read_model(case_id, pathway)
    if model is None or not model.consent_frozen or model.review_reason is not None:
        return None
    flag = Event(
        case_id,
        "review_requested",
        at,
        CONSENT_ACTOR,
        {"reason": "consent_withdrawn", "source_event_id": str(trigger_event_id)},
        review_event_id(trigger_event_id),
    )
    payloads.validate(flag.type, flag.payload)
    store.append(flag, pathway)
    return flag
