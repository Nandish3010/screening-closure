"""SEC-2 fix 4: the approval gate lives in the state machine, and approval writes an outbox row.

* the machine (`CaseModel.approved_unsent`): a `message_sent` is accepted only for a draft a human approved on
  that case and that has not gone out, on a channel with consent at apply time;
* the outbox: approval writes the row in the same transaction; a drain (end of approve, scheduler loop) sends
  idempotently and marks it; a crash after the approval commit is finished by the drain, exactly once.
Memory and Postgres (the Postgres half proves the transaction, the lock and the concurrency)."""

import os
import threading
from uuid import UUID, uuid4

import pytest

from api_helpers import actor_of, login_headers, make_client
from app import notify, outbox
from app.ai.draft_store import InMemoryDraftStore
from app.clock import clock
from app.config import settings as app_settings
from app.events import CASE_CREATED, Event, InMemoryEventStore
from app.pathway import load_pathway
from app.routers import cases, drafts
from app.state_machine import NOTIFIER_ACTOR, InvalidTransition, replay_model, step
from test_handoff import at
from test_state_machine import OWNER, TO_CONTACTED, drive, make_event

PATHWAY = load_pathway(app_settings.pathway_path)
HAS_DB = bool(os.environ.get("DATABASE_URL"))
client = make_client()
BODY = {"purpose": "result_notification", "channel": "sms", "language": "en"}
SCHED = {"Authorization": f"Bearer {app_settings.scheduler_service_token}"}
NAME = "Dr Outbox"
D1, D2 = "5b6c1f3e-8d2a-4c47-9e10-2a7f4b9d3c11", "6c7d2a4f-9e3b-4d58-8f21-3b8a5c0e4d22"


# ---- the machine ---------------------------------------------------------------------------------------------


def sent(draft_id, channel="sms", when=5):
    return make_event("message_sent", NOTIFIER_ACTOR, {"notification_id": str(uuid4()), "channel": channel,
                                                      "language": "en", "purpose": "reminder", "draft_id": draft_id}, at(when))


def approved(draft_id, when=4):
    return make_event("message_approved", OWNER, {"draft_id": draft_id}, at(when))


def test_a_message_sent_without_an_approval_is_refused_by_the_machine():
    m = drive(*TO_CONTACTED)
    with pytest.raises(InvalidTransition, match="not an approved, unsent draft"):
        step(m, sent(D1), PATHWAY)
    no_draft = make_event("message_sent", NOTIFIER_ACTOR, {"notification_id": str(uuid4()), "channel": "sms",
                                                           "language": "en", "purpose": "reminder"}, at(5))
    with pytest.raises(InvalidTransition, match="not an approved, unsent draft"):
        step(m, no_draft, PATHWAY)


def test_an_approved_draft_is_sent_once_and_only_that_draft():
    m = step(drive(*TO_CONTACTED), approved(D1), PATHWAY)
    assert m.approved_unsent == (D1,) and m.to_json()["approved_unsent_drafts"] == [D1]
    with pytest.raises(InvalidTransition, match="not an approved"):
        step(m, sent(D2), PATHWAY)  # another draft: nobody approved it
    done = step(m, sent(D1), PATHWAY)
    assert done.approved_unsent == () and done.state == m.state
    with pytest.raises(InvalidTransition, match="not an approved"):
        step(done, sent(D1, when=6), PATHWAY)  # once


def test_a_draft_cannot_be_approved_twice():
    m = step(drive(*TO_CONTACTED), approved(D1), PATHWAY)
    with pytest.raises(InvalidTransition, match="already approved"):
        step(m, approved(D1, when=5), PATHWAY)


def test_approvals_are_per_case():
    a, b = drive(*TO_CONTACTED), drive(*TO_CONTACTED)
    a = step(a, approved(D1), PATHWAY)
    with pytest.raises(InvalidTransition, match="not an approved"):
        step(b, sent(D1), PATHWAY)  # the other case's model has no such approval
    assert step(a, sent(D1), PATHWAY).approved_unsent == ()


def test_the_channel_is_judged_at_apply_time_not_at_approval_time():
    m = step(drive(*TO_CONTACTED), approved(D1), PATHWAY)
    m = step(m, make_event("consent_updated", "patient:p", {"channel_sms": False}, at(5)), PATHWAY)
    with pytest.raises(InvalidTransition, match="not consented to contact by sms"):
        step(m, sent(D1, "sms", 6), PATHWAY)
    assert step(m, sent(D1, "whatsapp", 6), PATHWAY).approved_unsent == ()  # another channel with consent: fine
    m = step(m, make_event("consent_updated", "patient:p", {"channel_sms": True}, at(6)), PATHWAY)
    assert step(m, sent(D1, "sms", 7), PATHWAY).approved_unsent == ()  # consent came back before the send


def test_withdrawal_after_the_approval_still_stops_the_send_and_leaves_the_approval_pending():
    m = step(drive(*TO_CONTACTED), approved(D1), PATHWAY)
    m = step(m, make_event("consent_updated", "patient:p", {"withdrawn": True}, at(5)), PATHWAY)
    with pytest.raises(InvalidTransition, match="consent withdrawn"):
        step(m, sent(D1, "sms", 6), PATHWAY)
    assert m.approved_unsent == (D1,)


def test_the_gate_holds_through_the_store_and_a_replay():
    store = InMemoryEventStore()
    cid = uuid4()
    store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, at(0), "system:intake", {}), PATHWAY.initial_state)
    for i, (t, a) in enumerate([("acknowledge", "lab:l"), ("accept_ownership", OWNER), ("contact_patient", OWNER)], 1):
        store.append(Event(cid, t, at(i), a, {}), PATHWAY)
    with pytest.raises(InvalidTransition):
        store.append(Event(cid, "message_sent", at(5), NOTIFIER_ACTOR, sent(D1).payload), PATHWAY)
    store.append(Event(cid, "message_approved", at(5), OWNER, {"draft_id": D1}), PATHWAY)
    store.append(Event(cid, "message_sent", at(6), NOTIFIER_ACTOR, sent(D1).payload), PATHWAY)
    assert replay_model(store.events_for(cid), PATHWAY) == store.read_model(cid, PATHWAY)
    assert store.read_model(cid, PATHWAY).approved_unsent == ()


# ---- the outbox, over HTTP -----------------------------------------------------------------------------------


@pytest.fixture(params=["memory", pytest.param("postgres", marks=[pytest.mark.integration, pytest.mark.skipif(not HAS_DB, reason="DATABASE_URL not set")])])
def world(request, monkeypatch):
    """Memory, or a scratch Postgres database of its own (a drain is global: it must not see the pending rows
    other tests leave behind)."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    pool = scratch = None
    if request.param == "memory":
        monkeypatch.setattr(cases, "store", InMemoryEventStore())
        monkeypatch.setattr(drafts, "store", InMemoryDraftStore())
        monkeypatch.setattr(notify, "log", notify.InMemoryNotificationLog())
    else:
        from app.db import create_pool
        from app.store_pg import PostgresEventStore, PostgresNotificationLog
        from scratchdb import scratch_database

        scratch = scratch_database("outbox")
        url = scratch.__enter__()
        pool = create_pool(url, max_size=10)
        monkeypatch.setattr(cases, "store", PostgresEventStore(pool))
        monkeypatch.setattr(notify, "log", PostgresNotificationLog(pool))
        monkeypatch.setattr(drafts, "_pg", None)
        monkeypatch.setattr(drafts, "_pg_outbox", None)
    clock.reset()
    yield request.param
    clock.reset()
    if pool is not None:
        pool.close()
        scratch.__exit__(None, None, None)


def H(role, name=None, patient_ref=None):
    return login_headers(client, role, name, patient_ref)


def post(cid, type_, role, payload=None, name=None, patient_ref=None):
    r = client.post(f"/cases/{cid}/events", json={"type": type_, "payload": payload or {}}, headers=H(role, name, patient_ref))
    assert r.status_code == 201, r.text


def owned_case(patient_ref=None):
    patient_ref = patient_ref or f"Patient/ob-{uuid4().hex[:8]}"
    cid = client.post("/cases", json={"patient_ref": patient_ref}, headers=H("coordinator")).json()["case_id"]
    post(cid, "acknowledge", "lab")
    post(cid, "accept_ownership", "primary_care_owner", name=NAME)
    return cid, patient_ref


def make_draft(cid, **over):
    r = client.post(f"/cases/{cid}/drafts", json={**BODY, **over}, headers=H("coordinator", "Maryam"))
    assert r.status_code == 201, r.text
    return r.json()["draft_id"]


def approve(did):
    return client.post(f"/drafts/{did}/approve", headers=H("primary_care_owner", NAME))


def types(cid):
    return [e.type for e in cases.store.events_for(UUID(cid))]


def rows(cid):
    return notify.log.for_case(UUID(cid))


class Crash(RuntimeError):
    """Stands in for the process dying after the approval committed."""


class Restore:
    def __init__(self, monkeypatch, real):
        self._mp, self.real = monkeypatch, real

    def __call__(self):
        """The process is back: the real drain is what runs from now on."""
        self._mp.setattr(drafts, "drain_outbox", self.real)


@pytest.fixture
def crashing_drain(monkeypatch):
    """The drain at the end of the approve request never runs (the API died right after the commit).
    Call the fixture to bring the process back."""
    real = drafts.drain_outbox

    def die(*args, **kwargs):
        raise Crash("killed after the approval committed")

    monkeypatch.setattr(drafts, "drain_outbox", die)
    return Restore(monkeypatch, real)


def test_approval_writes_the_outbox_row_and_the_drain_marks_it_sent(world):
    cid, _ = owned_case()
    did = make_draft(cid)
    out = approve(did).json()
    assert out["sent"] is True and out["blocked_reason"] is None
    item = drafts.outbox_store().get(UUID(did))
    assert item.status == outbox.SENT and str(item.notification_id) == out["notification_id"]
    assert types(cid)[-3:] == ["message_drafted", "message_approved", "message_sent"]
    assert [n.status for n in rows(cid)] == ["sent"]
    assert drafts.draft_store().get(UUID(did)).approved_at is not None


def test_a_crash_after_the_approval_commit_then_the_drain_sends_exactly_once(world, crashing_drain):
    cid, _ = owned_case()
    did = make_draft(cid)
    r = approve(did)
    assert r.status_code == 200 and r.json()["sent"] is False and r.json()["blocked_reason"] == "pending"
    # the approval committed, with its outbox row; nothing went out
    assert "message_approved" in types(cid) and "message_sent" not in types(cid) and rows(cid) == []
    assert drafts.outbox_store().get(UUID(did)).status == outbox.PENDING
    assert cases.store.read_model(UUID(cid), PATHWAY).approved_unsent == (did,)

    crashing_drain()  # the API is back
    # the scheduler's drain finishes it ...
    assert client.post("/scheduler/outbox/drain", headers=SCHED).json() == {"drained": 1, "sent": 1, "blocked": 0, "pending": 0}
    assert types(cid).count("message_sent") == 1 and [n.status for n in rows(cid)] == ["sent"]
    # ... and a second drain, or two at once, send nothing more
    assert client.post("/scheduler/outbox/drain", headers=SCHED).json()["drained"] == 0
    assert drafts.drain_outbox(only=[UUID(did)]) == []
    assert types(cid).count("message_sent") == 1 and len(rows(cid)) == 1
    assert drafts.outbox_store().get(UUID(did)).status == outbox.SENT


def test_a_crash_inside_the_send_leaves_the_row_pending_and_the_next_drain_sends_once(world, monkeypatch):
    cid, _ = owned_case()
    did = make_draft(cid)
    store = drafts.outbox_store()
    real_complete, calls = store.complete, []

    def dies_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise Crash("died between the message_sent insert and the outbox mark")
        return real_complete(*args, **kwargs)

    monkeypatch.setattr(store, "complete", dies_once)
    r = approve(did)
    assert r.status_code == 200 and r.json()["blocked_reason"] == "pending"  # never a 500 after the commit
    assert store.get(UUID(did)).status == outbox.PENDING and store.get(UUID(did)).attempts == 1
    if world == "postgres":  # one transaction: neither the event nor the notification row survived
        assert "message_sent" not in types(cid) and rows(cid) == []
    out = drafts.drain_outbox()
    assert [(o.status) for o in out] == [outbox.SENT]
    assert types(cid).count("message_sent") == 1 and [n.status for n in rows(cid)] == ["sent"]
    assert drafts.drain_outbox() == []


def test_channel_opt_out_between_the_approval_and_the_send_writes_no_message_sent(world, crashing_drain):
    cid, patient_ref = owned_case()
    did = make_draft(cid)
    assert approve(did).json()["blocked_reason"] == "pending"
    post(cid, "consent_updated", "patient", {"channel_sms": False}, name="ob pat", patient_ref=patient_ref)  # the opt-out
    crashing_drain()
    out = drafts.drain_outbox()
    assert [(o.status, o.reason) for o in out] == [(outbox.BLOCKED, "blocked_consent")]
    assert "message_sent" not in types(cid)
    ((row,),) = [[n] for n in rows(cid)]
    assert row.status == "blocked_consent" and row.event_id is None and "sms" in row.reason
    item = drafts.outbox_store().get(UUID(did))
    assert item.status == outbox.BLOCKED and item.reason == "blocked_consent"
    assert drafts.drain_outbox() == [] and "message_sent" not in types(cid)  # final: not retried later


def test_a_withdrawal_after_the_approval_blocks_the_send(world, crashing_drain):
    cid, patient_ref = owned_case()
    did = make_draft(cid)
    approve(did)
    post(cid, "consent_updated", "patient", {"withdrawn": True}, name="ob pat", patient_ref=patient_ref)
    crashing_drain()
    assert [o.status for o in drafts.drain_outbox()] == [outbox.BLOCKED]
    assert "message_sent" not in types(cid)


def test_a_case_that_closed_after_the_approval_blocks_the_send(world, crashing_drain):
    cid, _ = owned_case()
    did = make_draft(cid)
    approve(did)
    # the state machine refuses the message_sent on a closed case; model the closure at the store level
    case_id = UUID(cid)
    steps = [("contact_patient", "primary_care_owner:x", {}), ("patient_acknowledge", "patient:p", {}),
             ("book_confirmatory", "primary_care_owner:x", {}), ("complete_confirmatory", "specialist_owner:s", {}),
             ("clinician_review", "primary_care_owner:x", {}),
             ("close", "primary_care_owner:x", {"evidence_ref": "DiagnosticReport/1", "signed_by": "Practitioner/9"})]
    for t, a, p in steps:
        cases.store.append(Event(case_id, t, clock.now(), a, p), PATHWAY)
    assert cases.store.current_state(case_id) == "closed"
    crashing_drain()
    out = drafts.drain_outbox()
    assert [(o.status, o.reason) for o in out] == [(outbox.BLOCKED, "case_state")]
    assert "message_sent" not in types(cid)


def test_a_flagged_contact_is_blocked_and_final(world):
    cid = client.post("/cases", json={"patient_ref": "Patient/ob-flag", "contact_valid": {"sms": False}}, headers=H("coordinator")).json()["case_id"]
    post(cid, "acknowledge", "lab")
    post(cid, "accept_ownership", "primary_care_owner", name=NAME)
    did = make_draft(cid)
    out = approve(did).json()
    assert out["sent"] is False and out["blocked_reason"] == "contact"
    assert "message_sent" not in types(cid) and [n.status for n in rows(cid)] == ["blocked_contact"]


def test_the_scheduler_route_is_the_schedulers_alone(world):
    for role in ("lab", "primary_care_owner", "specialist_owner", "coordinator", "patient", "supervisor"):
        assert client.post("/scheduler/outbox/drain", headers=H(role, "x", "Patient/1" if role == "patient" else None)).status_code == 403
    assert client.post("/scheduler/outbox/drain").status_code == 401
    wd = {"Authorization": f"Bearer {app_settings.watchdog_service_token}"}
    assert client.post("/scheduler/outbox/drain", headers=wd).status_code == 403
    assert client.post("/scheduler/outbox/drain", headers=SCHED).json() == {"drained": 0, "sent": 0, "blocked": 0, "pending": 0}


def test_a_stray_row_for_a_missing_draft_is_blocked_not_retried_forever(world):
    cid, _ = owned_case()
    store = drafts.outbox_store()
    if world == "postgres":
        pytest.skip("the foreign key already forbids it")
    store.enqueue(outbox.OutboxItem(uuid4(), UUID(cid), "sms", "en", "reminder", clock.now()))
    assert [(o.status, o.reason) for o in drafts.drain_outbox()] == [(outbox.BLOCKED, "draft_missing")]


# ---- Postgres only: one transaction, one lock -----------------------------------------------------------------


@pytest.mark.parametrize("n", [8])
@pytest.mark.integration
@pytest.mark.skipif(not HAS_DB, reason="DATABASE_URL not set")
def test_concurrent_drains_send_exactly_once(world, crashing_drain, n):
    if world != "postgres":
        pytest.skip("needs real transactions")
    cid, _ = owned_case()
    did = make_draft(cid)
    approve(did)
    crashing_drain()
    barrier, results = threading.Barrier(n), []

    def go():
        barrier.wait()
        results.append(drafts.drain_outbox(only=[UUID(did)]))

    threads = [threading.Thread(target=go) for _ in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert types(cid).count("message_sent") == 1 and len(rows(cid)) == 1
    assert drafts.outbox_store().get(UUID(did)).status == outbox.SENT


@pytest.mark.integration
@pytest.mark.skipif(not HAS_DB, reason="DATABASE_URL not set")
def test_the_outbox_row_commits_with_the_approval_or_not_at_all(world, monkeypatch):
    if world != "postgres":
        pytest.skip("needs real transactions")
    cid, _ = owned_case()
    did = make_draft(cid)
    store = drafts.outbox_store()

    real_enqueue = store.enqueue

    def boom(*args, **kwargs):
        raise Crash("the outbox insert failed")

    monkeypatch.setattr(store, "enqueue", boom)
    n = len(types(cid))
    with pytest.raises(Crash):
        client_raising = make_client()
        client_raising.post(f"/drafts/{did}/approve", headers=H("primary_care_owner", NAME))
    assert len(types(cid)) == n and "message_approved" not in types(cid)
    assert drafts.draft_store().get(UUID(did)).approved_at is None  # the approval rolled back with it
    monkeypatch.setattr(store, "enqueue", real_enqueue)
    assert approve(did).json()["sent"] is True  # and the draft is still approvable


@pytest.mark.integration
@pytest.mark.skipif(not HAS_DB, reason="DATABASE_URL not set")
def test_the_database_keeps_a_completed_outbox_row_final(world):
    if world != "postgres":
        pytest.skip("needs Postgres")
    import psycopg

    cid, _ = owned_case()
    did = make_draft(cid)
    approve(did)
    pool = cases.store._pool
    with pytest.raises(psycopg.Error, match="already sent"):
        with pool.connection() as conn:
            conn.execute("UPDATE outbox SET status = 'pending', completed_at = NULL WHERE draft_id = %s", (did,))
    with pytest.raises(psycopg.Error, match="never deleted"):
        with pool.connection() as conn:
            conn.execute("DELETE FROM outbox WHERE draft_id = %s", (did,))
