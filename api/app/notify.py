"""Notification mock (A7, ADR-018): SMS / WhatsApp / phone / letter messages are written to a
log and never sent. This module opens no network connection and imports nothing that could.

`send` is the only code path that writes `message_sent` (no route can post it: EVENT_POLICY is
empty for it, and the state machine accepts it only from `system:notifier`). D5's approval gate
calls `send` after `message_approved`; nothing else should.

For each call it:
1. checks consent for that channel on the case's read model (withdrawn, frozen or channel
   switched off: `blocked_consent`);
2. checks the patient's contact validity flag if the case_created payload carries one
   (`contact_valid: false`, or `{sms: false, ...}` per channel: `blocked_contact`);
3. otherwise appends `message_sent` from `system:notifier` and logs it as `sent`.

A blocked message is logged too (with the reason) but writes no `message_sent`: nothing went out.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.clock import clock
from app.consent import CHANNELS, channel_allowed
from app.events import CaseNotFound, Event
from app.state_machine import NOTIFIER_ACTOR

LANGUAGES = ("en", "ar")
STATUSES = ("queued", "sent", "blocked_consent", "blocked_contact")
# Fixed namespace for the message_sent event id (never change: it would break idempotency).
NOTIFY_NAMESPACE = uuid.UUID("7d1c5d0e-3b0b-4e0c-8a52-9b1f4a2c6e31")


@dataclass(frozen=True)
class Notification:
    id: UUID
    case_id: UUID
    channel: str
    language: str
    purpose: str
    body: str
    status: str
    reason: str | None
    event_id: UUID | None  # the message_sent event (None unless status == "sent")
    created_at: datetime  # simulation time

    def to_json(self) -> dict:
        return {
            "id": str(self.id),
            "case_id": str(self.case_id),
            "channel": self.channel,
            "language": self.language,
            "purpose": self.purpose,
            "body": self.body,
            "status": self.status,
            "reason": self.reason,
            "event_id": str(self.event_id) if self.event_id else None,
            "created_at": self.created_at.isoformat(),
        }


class NotificationLog(Protocol):
    def add(self, notification: Notification) -> None: ...

    def for_case(self, case_id: UUID) -> list[Notification]:
        """Oldest first."""
        ...


class InMemoryNotificationLog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: list[Notification] = []

    def add(self, notification: Notification) -> None:
        with self._lock:
            self._rows.append(notification)

    def for_case(self, case_id: UUID) -> list[Notification]:
        with self._lock:
            return [n for n in self._rows if n.case_id == case_id]


# Bound by app.main at start-up to the Postgres log when DATABASE_URL is set.
log: NotificationLog = InMemoryNotificationLog()


def contact_blocked(genesis_payload: dict, channel: str) -> str | None:
    """Why the patient cannot be reached on `channel`, from the case_created payload, else None.

    `contact_valid` is `false` (no valid contact details at all), or a mapping of channel to
    bool (`{"sms": false}`: the number on file is wrong). Absent or null means "no known problem".
    """
    flag = genesis_payload.get("contact_valid")
    if flag is False:
        return "contact details flagged invalid"
    if isinstance(flag, dict) and flag.get(channel) is False:
        return f"{channel} contact details flagged invalid"
    return None


def emit_message_sent(store, pathway, case_id: UUID, notification_id: UUID, channel: str,
                      language: str, purpose: str, at: datetime, draft_id: str | None = None) -> Event:
    """Append `message_sent` as `system:notifier`. Python-level on purpose: there is no route."""
    payload = {"notification_id": str(notification_id), "channel": channel,
               "language": language, "purpose": purpose}
    if draft_id is not None:
        payload["draft_id"] = draft_id
    event = Event(case_id, "message_sent", at, NOTIFIER_ACTOR, payload,
                  uuid.uuid5(NOTIFY_NAMESPACE, str(notification_id)))
    store.append(event, pathway)  # InvalidTransition if the case cannot take it (e.g. closed)
    return event


def send(
    case_id: UUID,
    channel: str,
    language: str,
    purpose: str,
    body: str,
    *,
    draft_id: str | None = None,
    at: datetime | None = None,
    store=None,
    pathway=None,
    notification_log: NotificationLog | None = None,
) -> Notification:
    """Mock-send one message. Returns the logged `Notification` (status says what happened)."""
    case_id = UUID(str(case_id))
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {CHANNELS}, got {channel!r}")
    if language not in LANGUAGES:
        raise ValueError(f"language must be one of {LANGUAGES}, got {language!r}")
    if not purpose or not body:
        raise ValueError("purpose and body are required")
    if store is None or pathway is None:
        from app.routers import cases  # resolved late: tests and main rebind these

        store = store if store is not None else cases.store
        pathway = pathway if pathway is not None else cases.pathway
    out = notification_log if notification_log is not None else log
    at = at or clock.now()

    model = store.read_model(case_id, pathway)
    if model is None:
        raise CaseNotFound(str(case_id))
    notification_id = uuid.uuid4()

    allowed, why = channel_allowed(model, channel)
    status, reason = ("blocked_consent", why) if not allowed else ("sent", None)
    if allowed:
        genesis = store.events_for(case_id)[0].payload
        problem = contact_blocked(genesis, channel)
        if problem:
            status, reason = "blocked_contact", problem

    event_id = None
    if status == "sent":
        event_id = emit_message_sent(store, pathway, case_id, notification_id, channel, language,
                                     purpose, at, draft_id).event_id
    row = Notification(notification_id, case_id, channel, language, purpose, body, status,
                       reason, event_id, at)
    out.add(row)
    return row
