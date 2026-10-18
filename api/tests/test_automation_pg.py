"""A21 (ADR-032) against Postgres: the automation's events and its messages are one transaction each, the weekly cap and
the template gate hold on the replay the database does under the case lock, and concurrent scans write each event once.

Run with `make test-db`, or against a scratch database (`DATABASE_URL=.../closure_auto_test`, created and dropped by
hand). Every scan is restricted to the test's own cases (`only=`)."""

import os
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app import automation, evidence, notify, users
from app.calendar import WorkingCalendar
from app.config import settings as app_settings
from app.db import create_pool, run_migrations
from app.events import CASE_CREATED, Event
from app.pathway import load_pathway
from app.state_machine import AUTOMATION_ACTOR, InvalidTransition
from app.store_pg import PostgresEventStore, PostgresNotificationLog

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

PATHWAY = load_pathway(app_settings.pathway_path)
UAE = WorkingCalendar()
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # Monday


@pytest.fixture(scope="module")
def pool():
    p = create_pool(os.environ["DATABASE_URL"], max_size=12)
    run_migrations(p)
    yield p
    p.close()


@pytest.fixture
def store(pool, monkeypatch):
    s = PostgresEventStore(pool)
    monkeypatch.setattr(notify, "log", PostgresNotificationLog(pool))
    monkeypatch.setattr(users, "directory", users.InMemoryUserDirectory())
    monkeypatch.setattr(app_settings, "automation_enabled", True)
    monkeypatch.setattr(evidence, "verifier", evidence.AcceptAllVerifier())
    return s


def new_case(store, **payload):
    cid = uuid4()
    store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, AT, "system:intake", {"patient_ref": "Patient/pg-1", **payload}),
                      PATHWAY.initial_state)
    return cid


def scan(store, cid, now=AT):
    return automation.run_automation_scan(store, PATHWAY, now, UAE, only=[cid])


def accepted(store, cid, name="Dr. Layla Haddad"):
    from app.auth import opaque_user_id

    store.append(Event(cid, "accept_ownership", AT, f"primary_care_owner:{opaque_user_id('primary_care_owner', name)}",
                       {}), PATHWAY)


def types(store, cid):
    return [e.type for e in store.events_for(cid)]


def test_ingest_and_the_intake_scan_write_each_event_once(store):
    cid = new_case(store, registered_provider="Marina Digestive Health")
    automation.on_ingest(store, PATHWAY, cid, {"registered_provider": "Marina Digestive Health"}, AT)
    automation.on_ingest(store, PATHWAY, cid, {"registered_provider": "Marina Digestive Health"}, AT)  # a retried ingest
    assert types(store, cid) == ["case_created", "acknowledge", "assign_ownership"]
    m = store.read_model(cid, PATHWAY)
    assert m.state == "acknowledged" and m.provider == "Marina Digestive Health" and m.owner.startswith("primary_care_owner:")
    assert scan(store, cid).get("assigned") is None and len(store.events_for(cid)) == 3


def test_concurrent_scans_send_one_message_and_write_one_event_each(store):
    cid = new_case(store)
    automation.on_ingest(store, PATHWAY, cid, {}, AT)
    accepted(store, cid)
    barrier = threading.Barrier(6)
    errors = []

    def worker():
        barrier.wait()
        try:
            scan(store, cid)
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    sent = [e for e in store.events_for(cid) if e.type == "message_sent"]
    assert len(sent) == 1 and sent[0].actor == AUTOMATION_ACTOR and sent[0].payload["template_id"] == "result_notification.sms"
    rows = notify.log.for_case(cid)
    assert len(rows) == 1 and rows[0].status == "sent" and rows[0].event_id == sent[0].event_id


def test_the_weekly_cap_and_the_template_gate_hold_in_postgres(store):
    cid = new_case(store)
    automation.on_ingest(store, PATHWAY, cid, {}, AT)
    accepted(store, cid)

    def msg(template_id, channel, purpose, at, version=1):
        return Event(cid, "message_sent", at, AUTOMATION_ACTOR,
                     {"notification_id": str(uuid4()), "channel": channel, "language": "en", "purpose": purpose,
                      "auto": True, "template_id": template_id, "template_version": version})

    store.append(msg("result_notification.sms", "sms", "result_notification", AT), PATHWAY)
    store.append(msg("reminder.sms", "sms", "reminder", AT + timedelta(days=1)), PATHWAY)
    with pytest.raises(InvalidTransition, match="2 automatic messages"):
        store.append(msg("reminder.whatsapp", "whatsapp", "reminder", AT + timedelta(days=2)), PATHWAY)
    with pytest.raises(InvalidTransition, match="not in the approved"):
        store.append(msg("made_up.sms", "sms", "reminder", AT + timedelta(days=9)), PATHWAY)
    with pytest.raises(InvalidTransition, match="version"):
        store.append(msg("reminder.sms", "sms", "reminder", AT + timedelta(days=9), version=7), PATHWAY)
    store.append(msg("reminder.whatsapp", "whatsapp", "reminder", AT + timedelta(days=8)), PATHWAY)
    assert store.read_model(cid, PATHWAY).to_json()["auto_messages_sent"] == 3


def test_a_withdrawal_between_the_scan_and_the_write_stops_the_message_in_postgres(store):
    cid = new_case(store)
    automation.on_ingest(store, PATHWAY, cid, {}, AT)
    accepted(store, cid)
    store.append(Event(cid, "consent_updated", AT, "patient:pg", {"withdrawn": True}), PATHWAY)
    out = scan(store, cid)
    assert out.get("outreach_sent") is None and notify.log.for_case(cid) == []
    assert not [e for e in store.events_for(cid) if e.type == "message_sent"]


def test_reroute_and_exhaustion_through_postgres(store):
    cid = new_case(store)
    automation.on_ingest(store, PATHWAY, cid, {}, AT)
    from app.auth import opaque_user_id

    owner = f"primary_care_owner:{opaque_user_id('primary_care_owner', 'Dr. Layla Haddad')}"
    store.append(Event(cid, "accept_ownership", AT, owner, {}), PATHWAY)
    store.append(Event(cid, "contact_patient", AT, owner, {}), PATHWAY)
    store.append(Event(cid, "patient_acknowledge", AT, "patient:pg", {}), PATHWAY)
    store.append(Event(cid, "handoff_requested", AT, owner, {"to_provider": "Corniche Gastro Unit"}), PATHWAY)
    for _ in range(4):
        if store.read_model(cid, PATHWAY).pending_handoff:
            store.append(Event(cid, "handoff_rejected", AT, "specialist_owner:x1", {"reason": "capacity"}), PATHWAY)
        scan(store, cid)
        scan(store, cid)
    asked = [e.payload["to_provider"] for e in store.events_for(cid) if e.type == "handoff_requested"]
    assert asked == ["Corniche Gastro Unit", "Al Noor Endoscopy Centre", "Marina Digestive Health"]
    flags = [e for e in store.events_for(cid) if e.type == "review_requested"]
    assert len(flags) == 1 and flags[0].payload["reason"] == "reroute_exhausted"
    assert store.read_model(cid, PATHWAY).to_json()["review_reason"] == "reroute_exhausted"


def test_evidence_scan_completes_and_proposes_through_postgres_and_close_stays_human(store):
    cid = new_case(store)
    automation.on_ingest(store, PATHWAY, cid, {}, AT)
    from app.auth import opaque_user_id

    owner = f"primary_care_owner:{opaque_user_id('primary_care_owner', 'Dr. Layla Haddad')}"
    spec = f"specialist_owner:{opaque_user_id('specialist_owner', 'Dr. Karim Nasser')}"
    for who, type_, payload in ((owner, "accept_ownership", {}), (owner, "contact_patient", {}),
                                ("patient:pg", "patient_acknowledge", {}),
                                (owner, "handoff_requested", {"to_provider": "Corniche Gastro Unit"}),
                                (spec, "handoff_accepted", {}), (spec, "book_confirmatory", {"appointment_date": "2026-01-20"})):
        store.append(Event(cid, type_, AT, who, payload), PATHWAY)
    ref = f"Procedure/colonoscopy-{cid}"
    finder = evidence.StaticFinder({cid: [ref]})
    out = automation.scan_evidence(store, PATHWAY, AT, finder=finder, only=[cid])
    assert out["completed"] == 1 and out["closure_proposed"] == 1
    assert automation.scan_evidence(store, PATHWAY, AT, finder=finder, only=[cid]).get("found") is None
    m = store.read_model(cid, PATHWAY)
    assert m.state == "confirmatory_completed" and m.closure_proposal["evidence_ref"] == ref
    with pytest.raises(InvalidTransition):
        store.append(Event(cid, "close", AT, AUTOMATION_ACTOR, {"evidence_ref": ref, "signed_by": AUTOMATION_ACTOR}), PATHWAY)
