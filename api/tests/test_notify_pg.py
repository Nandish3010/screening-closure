"""T-A25 against Postgres (`make test-db`): the notifications table is an append-only log whose
sent rows point at their message_sent event."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.config import settings as app_settings
from app.db import create_pool, run_migrations
from app.events import CASE_CREATED, Event
from app.notify import Notification, send
from app.pathway import load_pathway
from app.state_machine import InvalidTransition
from app.store_pg import PostgresEventStore, PostgresNotificationLog

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

PATHWAY = load_pathway(app_settings.pathway_path)
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
OWNER = "primary_care_owner:dr_a"


def at(day=0, hours=0):
    return AT + timedelta(days=day, hours=hours)


@pytest.fixture(scope="module")
def pool():
    p = create_pool(os.environ["DATABASE_URL"], max_size=4)
    run_migrations(p)
    yield p
    p.close()


@pytest.fixture(scope="module")
def store(pool):
    return PostgresEventStore(pool)


@pytest.fixture(scope="module")
def log(pool):
    return PostgresNotificationLog(pool)


def contacted_case(store, genesis=None):
    cid = uuid4()
    store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, AT, "system:intake", genesis or {"patient_ref": "Patient/1"}),
                      PATHWAY.initial_state)
    for i, (type_, actor) in enumerate([("acknowledge", "lab:l"), ("accept_ownership", OWNER),
                                        ("contact_patient", OWNER)], start=1):
        store.append(Event(cid, type_, at(0, i), actor), PATHWAY)
    return cid


def do_send(store, log, cid, channel="sms", when=at(1), body="Hello"):
    draft = str(uuid4())  # ADR-030: a message_sent needs a draft a human approved first
    try:
        store.append(Event(cid, "message_approved", when, "primary_care_owner:dr_a", {"draft_id": draft}), PATHWAY)
    except InvalidTransition:
        pass  # consent withdrawn: the approval is refused too, and the send reports blocked_consent
    return send(cid, channel, "en", "reminder", body, store=store, pathway=PATHWAY, notification_log=log, at=when,
                draft_id=draft)


def test_sent_rows_round_trip_and_point_at_their_event(store, log, pool):
    cid = contacted_case(store)
    a = do_send(store, log, cid, "sms", body="one")
    b = do_send(store, log, cid, "whatsapp", at(2), body="two")
    assert [n.body for n in log.for_case(cid)] == ["one", "two"]
    assert log.for_case(cid) == [a, b]
    assert a.status == b.status == "sent" and a.created_at == at(1)
    sent = {e.event_id: e for e in store.events_for(cid) if e.type == "message_sent"}
    assert set(sent) == {a.event_id, b.event_id}
    assert sent[a.event_id].payload["notification_id"] == str(a.id) and sent[a.event_id].actor == "system:notifier"


def test_blocked_rows_have_no_event_and_the_mock_sends_nothing_after_withdrawal(store, log):
    cid = contacted_case(store, {"patient_ref": "Patient/1", "contact_valid": {"letter": False}})
    store.append(Event(cid, "consent_updated", at(1), "patient:p", {"channel_phone": False}), PATHWAY)
    assert do_send(store, log, cid, "phone").status == "blocked_consent"
    assert do_send(store, log, cid, "letter").status == "blocked_contact"
    store.append(Event(cid, "consent_updated", at(2), "patient:p", {"withdrawn": True}), PATHWAY)
    assert do_send(store, log, cid, "sms", at(3)).status == "blocked_consent"
    rows = log.for_case(cid)
    assert [n.status for n in rows] == ["blocked_consent", "blocked_contact", "blocked_consent"]
    assert all(n.event_id is None for n in rows)
    assert [e.type for e in store.events_for(cid)].count("message_sent") == 0


def test_the_table_is_append_only_and_status_must_match_event(store, log, pool):
    cid = contacted_case(store)
    n = do_send(store, log, cid)
    for sql in ("UPDATE notifications SET body='x' WHERE id=%s", "DELETE FROM notifications WHERE id=%s"):
        with pool.connection() as conn, pytest.raises(Exception, match="append-only"):
            conn.execute(sql, (n.id,))
    with pool.connection() as conn, pytest.raises(Exception, match="append-only"):
        conn.execute("TRUNCATE notifications")
    with pool.connection() as conn:  # the event it names is in the log
        assert conn.execute("SELECT count(*) FROM events WHERE event_id=%s", (n.event_id,)).fetchone()[0] == 1
    bad = [
        Notification(uuid4(), cid, "sms", "en", "p", "b", "sent", None, None, at(1)),  # sent without an event
        Notification(uuid4(), cid, "sms", "en", "p", "b", "blocked_consent", "x", n.event_id, at(1)),  # blocked with one
        Notification(uuid4(), cid, "sms", "en", "p", "b", "mystery", None, None, at(1)),
        Notification(uuid4(), cid, "fax", "en", "p", "b", "blocked_consent", "x", None, at(1)),
        Notification(uuid4(), cid, "sms", "fr", "p", "b", "blocked_consent", "x", None, at(1)),
    ]
    for row in bad:
        with pytest.raises(Exception):
            log.add(row)
    assert len(log.for_case(cid)) == 1
