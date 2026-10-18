"""Notification mock (A7, ADR-018): SMS / WhatsApp / phone / letter messages are written to a
log and never sent. This module opens no network connection and imports nothing that could.

`send` is the only code path that writes `message_sent` (no route can post it: EVENT_POLICY is
empty for it, and the state machine accepts it only from `system:notifier`). The state machine also
holds the approval gate (SEC-2, ADR-030): it accepts a `message_sent` only for a draft a human approved
on that case and that has not gone out, on a channel the patient consents to at that moment. Approving a
draft writes an outbox row (`app.outbox`) in the same transaction as the approval; `drain_outbox` calls
`send` for each pending row, and the approve route and the scheduler loop call the drain. Nothing else
should call `send`.

For each call it:
1. checks consent for that channel on the case's read model (withdrawn, frozen or channel
   switched off: `blocked_consent`);
2. checks the patient's contact validity flag if the case_created payload carries one
   (`contact_valid: false`, or `{sms: false, ...}` per channel: `blocked_contact`);
3. otherwise appends `message_sent` from `system:notifier` and logs it as `sent`, the event and the
   notification row in ONE transaction.

A blocked message is logged too (with the reason) but writes no `message_sent`: nothing went out.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app import payloads
from app.clock import clock
from app.consent import CHANNELS, channel_allowed
from app.events import CaseNotFound, Event
from app.state_machine import NOTIFIER_ACTOR, InvalidTransition

log_ = logging.getLogger("app.notify")

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
    def add(self, notification: Notification, cur=None) -> None:
        """Append the row (idempotent on its id). `cur`: the caller's Postgres cursor, so the row
        commits or rolls back with the `message_sent` event written in the same transaction."""
        ...

    def for_case(self, case_id: UUID) -> list[Notification]:
        """Oldest first."""
        ...


class InMemoryNotificationLog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: list[Notification] = []

    def add(self, notification: Notification, cur=None) -> None:
        with self._lock:
            if all(r.id != notification.id for r in self._rows):
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


def outbox_notification_id(draft_id) -> UUID:
    """The notification id of an outbox send: a function of the draft, so a drain that runs again after a
    crash produces the same notification row and the same `message_sent` event id (idempotent)."""
    return uuid.uuid5(NOTIFY_NAMESPACE, f"outbox|{draft_id}")


def emit_message_sent(store, pathway, case_id: UUID, notification_id: UUID, channel: str,
                      language: str, purpose: str, at: datetime, draft_id: str | None = None,
                      within=None) -> Event:
    """Append `message_sent` as `system:notifier`. Python-level on purpose: there is no route.

    `within(cur)` runs in the same transaction as the event (the notification row and the outbox
    mark go there). The state machine accepts the event only for a draft a human approved on this
    case that has not gone out, on a channel the patient consents to at this moment (ADR-030)."""
    payload = {"notification_id": str(notification_id), "channel": channel,
               "language": language, "purpose": purpose}
    if draft_id is not None:
        payload["draft_id"] = draft_id
    event = Event(case_id, "message_sent", at, NOTIFIER_ACTOR, payload,
                  uuid.uuid5(NOTIFY_NAMESPACE, str(notification_id)))
    payloads.validate(event.type, event.payload)
    store.append(event, pathway, within=within)  # InvalidTransition if the case cannot take it
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
    notification_id: UUID | None = None,
    also=None,
) -> Notification:
    """Mock-send one message. Returns the logged `Notification` (status says what happened).

    A message is sent only for an approved draft (`draft_id`): the state machine refuses the
    `message_sent` otherwise (InvalidTransition). The `message_sent` event and the notification row are
    written in ONE transaction (SEC-2): a failure between them leaves neither. `also(cur, row)`, if
    given, runs in that same transaction (the outbox drain marks its row there; `cur` is None where the
    store has no transaction to share, or for a blocked message, which has no event). `notification_id`
    makes the send idempotent for the outbox drain."""
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
    notification_id = notification_id or uuid.uuid4()

    allowed, why = channel_allowed(model, channel)
    status, reason = ("blocked_consent", why) if not allowed else ("sent", None)
    if allowed:
        genesis = store.events_for(case_id)[0].payload
        problem = contact_blocked(genesis, channel)
        if problem:
            status, reason = "blocked_contact", problem

    if status != "sent":  # nothing goes out, so there is no event to be atomic with
        row = Notification(notification_id, case_id, channel, language, purpose, body, status, reason, None, at)
        out.add(row)
        if also is not None:
            also(None, row)
        return row

    event_id = uuid.uuid5(NOTIFY_NAMESPACE, str(notification_id))
    row = Notification(notification_id, case_id, channel, language, purpose, body, status, reason, event_id, at)
    wrote: list[bool] = []

    def together(cur) -> None:
        out.add(row, cur=cur)
        if also is not None:
            also(cur, row)
        wrote.append(True)

    emit_message_sent(store, pathway, case_id, notification_id, channel, language, purpose, at, draft_id,
                      within=together)
    if not wrote:  # the event was already in the log (a retry): make sure its row and mark are too
        out.add(row)
        if also is not None:
            also(None, row)
    return row


# ---- the outbox drain (ADR-030) ------------------------------------------------------------------------


@dataclass(frozen=True)
class DrainOutcome:
    """What one drain attempt did to one outbox row."""

    draft_id: UUID
    status: str  # outbox.SENT, outbox.BLOCKED or outbox.PENDING (left for the next drain)
    reason: str | None = None  # blocked: the notification status (blocked_consent...) or `case_state`
    notification_id: UUID | None = None


def drain_outbox(outbox, draft_text, *, limit: int = 100, only=None, store=None, pathway=None,
                 notification_log: NotificationLog | None = None) -> list[DrainOutcome]:
    """Deliver every pending outbox row (or only the drafts in `only`) to the notification mock.

    Idempotent and crash-safe: the notification id and the `message_sent` event id are functions of the
    draft; the event, the notification row and the outbox mark commit together; and the state machine
    accepts one `message_sent` per approved draft. A drain that dies anywhere leaves the row pending and
    the next one finishes it; two drains at once send once. A row that cannot be sent for a reason that
    will not change (consent withdrawn or the channel off, contact details invalid, the case closed) is
    marked blocked with its reason and nothing is sent. A failure that may pass (the database) leaves it
    pending, counted, and is never raised: the approval it belongs to has already committed.
    `draft_text(draft_id)` returns the draft's text, or None: the drafts table is the only place the
    text lives."""
    from app import outbox as ob

    results: list[DrainOutcome] = []
    for item in outbox.pending(limit, only=only):
        text = draft_text(item.draft_id)
        if text is None:
            outbox.complete(item.draft_id, ob.BLOCKED, "draft_missing")
            results.append(DrainOutcome(item.draft_id, ob.BLOCKED, "draft_missing"))
            continue

        def mark(cur, row, _item=item):
            if row.status == "sent":
                outbox.complete(_item.draft_id, ob.SENT, None, row.id, cur=cur)
            else:
                outbox.complete(_item.draft_id, ob.BLOCKED, row.status, row.id, cur=cur)

        try:
            row = send(
                item.case_id, item.channel, item.language, item.purpose, text,
                draft_id=str(item.draft_id), at=item.approved_at, store=store, pathway=pathway,
                notification_log=notification_log, notification_id=outbox_notification_id(item.draft_id),
                also=mark,
            )
        except (InvalidTransition, CaseNotFound) as exc:
            # The case moved on between the approval and the send (closed, consent withdrawn or the
            # channel switched off in that instant): refused by the state machine at apply time.
            outbox.complete(item.draft_id, ob.BLOCKED, "case_state")
            log_.warning("approved draft %s was not sent: %s", item.draft_id, exc)
            results.append(DrainOutcome(item.draft_id, ob.BLOCKED, "case_state"))
            continue
        except Exception as exc:  # noqa: BLE001 - a transient failure must not lose the approval
            outbox.record_failure(item.draft_id, f"{type(exc).__name__}: {exc}")
            log_.warning("outbox send of draft %s failed, will retry: %s", item.draft_id, exc)
            results.append(DrainOutcome(item.draft_id, ob.PENDING, type(exc).__name__))
            continue
        results.append(DrainOutcome(
            item.draft_id, ob.SENT if row.status == "sent" else ob.BLOCKED,
            None if row.status == "sent" else row.status, row.id))
    return results
