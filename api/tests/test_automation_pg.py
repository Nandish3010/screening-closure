"""A21 (ADR-032) against Postgres: the automation's events and its messages are one transaction each, the weekly cap and
the template gate hold on the replay the database does under the case lock, and concurrent scans write each event once.

Run with `make test-db`, or against a scratch database (`DATABASE_URL=.../closure_auto_test`, created and dropped by
hand). Every scan is restricted to the test's own cases (`only=`)."""

import os
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app import automation, evidence, notify, outreach_policy, state_machine, users
from app.calendar import WorkingCalendar
from app.config import settings as app_settings
from app.db import create_pool, run_migrations
from app.events import CASE_CREATED, Event
from app.pathway import load_pathway
from app.state_machine import AUTOMATION_ACTOR, TRIAGE_ACTOR, InvalidTransition, replay_model
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

    def put(event):  # the way the application writes it: the append-time policy guard inside the transaction
        store.append(event, PATHWAY, guard=outreach_policy.auto_message_guard(event))

    put(msg("result_notification.sms", "sms", "result_notification", AT))
    put(msg("reminder.sms", "sms", "reminder", AT + timedelta(days=1)))
    with pytest.raises(InvalidTransition, match="2 outbound messages"):
        put(msg("reminder.whatsapp", "whatsapp", "reminder", AT + timedelta(days=2)))
    with pytest.raises(InvalidTransition, match="not in the approved"):
        put(msg("made_up.sms", "sms", "reminder", AT + timedelta(days=9)))
    with pytest.raises(InvalidTransition, match="version"):
        put(msg("reminder.sms", "sms", "reminder", AT + timedelta(days=9), version=7))
    put(msg("reminder.whatsapp", "whatsapp", "reminder", AT + timedelta(days=8)))
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
    negatives = automation.NegativeCache()
    early = automation.scan_evidence(store, PATHWAY, AT, finder=finder, only=[cid], negatives=negatives)
    assert early["not_yet_due"] == 1 and finder.calls == []  # booked for 20 January: HAPI is not asked on the 5th
    later = AT + timedelta(days=16)
    out = automation.scan_evidence(store, PATHWAY, later, finder=finder, only=[cid], negatives=negatives)
    assert out["completed"] == 1 and out["closure_proposed"] == 1
    assert automation.scan_evidence(store, PATHWAY, later, finder=finder, only=[cid], negatives=negatives).get("found") is None
    m = store.read_model(cid, PATHWAY)
    assert m.state == "confirmatory_completed" and m.closure_proposal["evidence_ref"] == ref
    with pytest.raises(InvalidTransition):
        store.append(Event(cid, "close", AT, AUTOMATION_ACTOR, {"evidence_ref": ref, "signed_by": AUTOMATION_ACTOR}), PATHWAY)


def test_a_flag_between_the_scan_and_the_write_stops_the_message_in_postgres(store, monkeypatch):
    """T-A49: the hold is checked by the state machine under the case's row lock on the replayed log, so a flag written
    after the scan read the case (a stale model) still stops the message."""
    monkeypatch.setitem(state_machine.ACTOR_EVENTS, TRIAGE_ACTOR, frozenset({"review_requested"}))
    cid = new_case(store)
    automation.on_ingest(store, PATHWAY, cid, {}, AT)
    accepted(store, cid)
    store.append(Event(cid, "review_requested", AT, TRIAGE_ACTOR,
                       {"reason": "opt_out", "source_event_id": str(uuid4())}), PATHWAY)
    with pytest.raises(InvalidTransition, match="held"):
        notify.send(cid, "sms", "en", "result_notification", "x", at=AT, store=store, pathway=PATHWAY,
                    auto={"template_id": "result_notification.sms", "template_version": 1})
    out = scan(store, cid)
    assert out["outreach_held"] == 1 and not [e for e in store.events_for(cid) if e.type == "message_sent"]
    store.append(Event(cid, "consent_updated", AT, "patient:pg", {"channel_sms": True}), PATHWAY)
    assert scan(store, cid)["outreach_sent"] == 1


def test_replay_from_the_database_does_not_depend_on_the_policy_file(store, monkeypatch, tmp_path):
    """T-A48: the log is read and replayed by the database store under the case lock; a policy that has changed or no
    longer loads must not make a case with automatic messages fail to replay (and so drop out of the sweep)."""
    cid = new_case(store)
    automation.on_ingest(store, PATHWAY, cid, {}, AT)
    accepted(store, cid)
    assert scan(store, cid)["outreach_sent"] == 1
    baseline = store.read_model(cid, PATHWAY).to_json()
    broken = tmp_path / "broken.yaml"
    broken.write_text("not: [valid")
    for path in (broken, tmp_path / "missing.yaml"):
        outreach_policy._cached.cache_clear()
        monkeypatch.setattr(app_settings, "outreach_policy_path", str(path))
        assert store.read_model(cid, PATHWAY).to_json() == baseline
        assert replay_model(store.events_for(cid), PATHWAY).to_json() == baseline
        (row,) = [r for r in store.iter_open_case_models(PATHWAY, only=[cid])]
        assert row[1] is not None and row[2] is None
    outreach_policy._cached.cache_clear()
    monkeypatch.setattr(app_settings, "outreach_policy_path", str(broken))
    # an append replays the whole log under the case lock: a case with an automatic message still takes events
    from app.auth import opaque_user_id

    owner = f"primary_care_owner:{opaque_user_id('primary_care_owner', 'Dr. Layla Haddad')}"
    store.append(Event(cid, "contact_patient", AT, owner, {}), PATHWAY)
    assert store.read_model(cid, PATHWAY).state == "patient_contacted"
    outreach_policy._cached.cache_clear()


def test_the_state_filter_reads_only_the_cases_in_those_states(store):
    a = new_case(store)
    automation.on_ingest(store, PATHWAY, a, {}, AT)  # acknowledged, owner named
    b = new_case(store)  # result_received
    c = new_case(store)
    automation.on_ingest(store, PATHWAY, c, {}, AT)
    accepted(store, c)  # owner_accepted
    ids = [a, b, c]
    got = {cid: m.state for cid, m, _ in store.iter_open_case_models(PATHWAY, only=ids, states=("result_received", "owner_accepted"))}
    assert got == {b: "result_received", c: "owner_accepted"}
    assert {cid for cid, _, _ in store.iter_open_case_models(PATHWAY, only=ids, states=[])} == set()
    assert {cid for cid, _, _ in store.iter_open_case_models(PATHWAY, only=ids)} == set(ids)
    assert {cid for cid, _, _ in store.iter_open_case_models(PATHWAY, only=ids, states=["acknowledged"], shard=(0, 1))} == {a}
    out = automation.run_automation_scan(store, PATHWAY, AT, UAE, only=ids)
    assert out["cases_evaluated"] == 3  # a, b and c are all in states the scan acts on
