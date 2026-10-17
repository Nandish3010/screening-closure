"""D5 against Postgres (T-D1, T-D5 approval half). Run with `make test-db`.

Checks what the in-memory tests cannot: the approval and `message_approved` commit in one
transaction, concurrent approvals send exactly once, and the database itself refuses a second
decision on a draft. Every assertion is about this test's own cases and drafts.
"""

import hashlib
import os
import threading
from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
import pytest

from api_helpers import actor_of, login_headers, make_client
from app import notify
from app.ai.draft_store import DraftAlreadyDecided, PostgresDraftStore
from app.ai.provider import SYSTEM_PROMPT
from app.clock import clock
from app.config import settings as app_settings
from app.db import create_pool, run_migrations
from app.events import Event
from app.pathway import load_pathway
from app.routers import cases, drafts
from app.store_pg import PostgresEventStore, PostgresNotificationLog

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

PATHWAY = load_pathway(app_settings.pathway_path)
BODY = {"purpose": "result_notification", "channel": "sms", "language": "en"}
OWNER = "Dr Sara Khan"
OWNER_ACTOR = actor_of("primary_care_owner", OWNER)  # A14: opaque id in the actor


@pytest.fixture(scope="module")
def pool():
    p = create_pool(os.environ["DATABASE_URL"], max_size=12)
    run_migrations(p)
    yield p
    p.close()


@pytest.fixture
def client(pool, monkeypatch):
    monkeypatch.setattr(cases, "store", PostgresEventStore(pool))
    monkeypatch.setattr(notify, "log", PostgresNotificationLog(pool))
    monkeypatch.setattr(drafts, "_pg", None)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    return make_client()


def q(pool, sql, params=()):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def H(client, role, name=None, patient_ref=None):
    return login_headers(client, role, name, patient_ref)


def post_event(client, cid, type_, role, payload=None, name=None, patient_ref=None):
    if name is None and role in ("primary_care_owner", "specialist_owner"):
        name = OWNER
    r = client.post(f"/cases/{cid}/events", json={"type": type_, "payload": payload or {}},
                    headers=H(client, role, name, patient_ref))
    assert r.status_code == 201, r.text


def owned_case(client, contact_valid=None):
    body = {"patient_ref": "Patient/pg"}
    if contact_valid is not None:
        body["contact_valid"] = contact_valid
    cid = client.post("/cases", json=body, headers=H(client, "coordinator")).json()["case_id"]
    post_event(client, cid, "acknowledge", "lab")
    post_event(client, cid, "accept_ownership", "primary_care_owner")
    return cid


def make_draft(client, cid, **over):
    r = client.post(f"/cases/{cid}/drafts", json={**BODY, **over}, headers=H(client, "coordinator", "Maryam"))
    assert r.status_code == 201, r.text
    return r.json()["draft_id"]


def approve(client, did, role="primary_care_owner", name=OWNER):
    return client.post(f"/drafts/{did}/approve", headers=H(client, role, name))


def event_rows(pool, cid, type_):
    return q(pool, "SELECT actor, payload FROM events WHERE case_id = %s AND type = %s ORDER BY seq", (cid, type_))


def draft_row(pool, did):
    (row,) = q(pool, "SELECT approved_at, approved_by, rejected_at, rejected_by, rejection_reason FROM drafts"
                     " WHERE draft_id = %s", (did,))
    return row


def test_approve_writes_draft_event_and_notification(client, pool):
    cid = owned_case(client)
    did = make_draft(client, cid)
    r = approve(client, did)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["sent"] is True and out["blocked_reason"] is None and out["approved_by"] == OWNER_ACTOR

    approved_at, approved_by, rejected_at, _rb, _rr = draft_row(pool, did)
    assert approved_by == OWNER_ACTOR and approved_at.isoformat() == out["approved_at"] and rejected_at is None
    ((actor, payload),) = event_rows(pool, cid, "message_approved")
    assert actor == OWNER_ACTOR and payload == {"draft_id": did}
    ((sent_actor, sent_payload),) = event_rows(pool, cid, "message_sent")
    assert sent_actor == "system:notifier" and sent_payload["draft_id"] == did
    ((nid, status, event_id),) = q(pool, "SELECT id, status, event_id FROM notifications WHERE case_id = %s", (cid,))
    assert str(nid) == out["notification_id"] and status == "sent"
    (sent_event_id,) = q(pool, "SELECT event_id FROM events WHERE case_id = %s AND type = 'message_sent'", (cid,))[0]
    assert event_id == sent_event_id
    # sequence: drafted < approved < sent
    order = [t for (t,) in q(pool, "SELECT type FROM events WHERE case_id = %s ORDER BY seq", (cid,))]
    assert order[-3:] == ["message_drafted", "message_approved", "message_sent"]


def test_provenance_survives_the_whole_flow(client, pool):
    cid = owned_case(client)
    did = make_draft(client, cid)
    approve(client, did)
    ((prompt_hash, created_by, approved_by, approved_at),) = q(
        pool, "SELECT prompt_hash, created_by, approved_by, approved_at FROM drafts WHERE draft_id = %s", (did,))
    ((system_prompt, prompt_text),) = q(pool, "SELECT system_prompt, prompt_text FROM prompts WHERE prompt_hash = %s", (prompt_hash,))
    assert system_prompt == SYSTEM_PROMPT
    assert hashlib.sha256((system_prompt + "\n---\n" + prompt_text).encode()).hexdigest() == prompt_hash
    assert created_by == actor_of("coordinator", "Maryam") and approved_by == OWNER_ACTOR
    ((_a, drafted),) = event_rows(pool, cid, "message_drafted")
    assert drafted["prompt_hash"] == prompt_hash and drafted["draft_id"] == did
    (event_at,) = q(pool, "SELECT at FROM events WHERE case_id = %s AND type = 'message_approved'", (cid,))[0]
    assert event_at == approved_at
    listed = client.get(f"/cases/{cid}/drafts", headers=H(client, "coordinator")).json()[0]
    assert listed["approved_by"] == OWNER_ACTOR and listed["prompt_hash"] == prompt_hash


def test_a_draft_cannot_be_approved_twice(client, pool):
    cid = owned_case(client)
    did = make_draft(client, cid)
    assert approve(client, did).status_code == 200
    n = q(pool, "SELECT count(*) FROM events WHERE case_id = %s", (cid,))[0][0]
    assert approve(client, did).status_code == 409
    assert approve(client, did, "supervisor", "sam").status_code == 409
    assert q(pool, "SELECT count(*) FROM events WHERE case_id = %s", (cid,))[0][0] == n
    assert q(pool, "SELECT count(*) FROM notifications WHERE case_id = %s", (cid,))[0][0] == 1


def test_concurrent_approvals_send_exactly_once(client, pool):
    cid = owned_case(client)
    did = make_draft(client, cid)
    headers = H(client, "primary_care_owner", OWNER)
    results, barrier = [], threading.Barrier(8)

    def go():
        c = make_client()
        barrier.wait()
        results.append(c.post(f"/drafts/{did}/approve", headers=headers).status_code)

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [200] + [409] * 7
    assert len(event_rows(pool, cid, "message_approved")) == 1
    assert len(event_rows(pool, cid, "message_sent")) == 1
    assert q(pool, "SELECT count(*) FROM notifications WHERE case_id = %s", (cid,))[0][0] == 1


def test_the_event_and_the_approval_roll_back_together(client, pool):
    cid = owned_case(client)
    did = make_draft(client, cid)
    store = cases.store
    event = Event(UUID(cid), "message_approved", clock.now(), OWNER_ACTOR, {"draft_id": did})

    def boom(_cur):
        raise RuntimeError("the draft update failed")

    n = q(pool, "SELECT count(*) FROM events WHERE case_id = %s", (cid,))[0][0]
    with pytest.raises(RuntimeError):
        store.append(event, PATHWAY, within=boom)
    assert q(pool, "SELECT count(*) FROM events WHERE case_id = %s", (cid,))[0][0] == n
    assert draft_row(pool, did)[0] is None

    # the other direction: the draft already decided (another transaction won) -> no event row
    ds = drafts.draft_store()
    ds.reject(UUID(did), OWNER_ACTOR, clock.now(), "changed my mind")
    with pytest.raises(DraftAlreadyDecided):
        store.append(event, PATHWAY, within=lambda cur: ds.approve(UUID(did), OWNER_ACTOR, clock.now(), cur=cur))
    assert q(pool, "SELECT count(*) FROM events WHERE case_id = %s", (cid,))[0][0] == n
    assert approve(client, did).status_code == 409  # and over HTTP


def test_a_frozen_case_is_409_and_writes_nothing(client, pool):
    cid = owned_case(client)
    did = make_draft(client, cid)
    post_event(client, cid, "consent_updated", "patient", {"withdrawn": True}, patient_ref="Patient/pg")
    n = q(pool, "SELECT count(*) FROM events WHERE case_id = %s", (cid,))[0][0]
    assert approve(client, did).status_code == 409
    assert q(pool, "SELECT count(*) FROM events WHERE case_id = %s", (cid,))[0][0] == n
    assert draft_row(pool, did)[0] is None
    assert q(pool, "SELECT count(*) FROM notifications WHERE case_id = %s", (cid,))[0][0] == 0


def test_a_blocked_channel_logs_a_blocked_row_and_no_message_sent(client, pool):
    cid = owned_case(client)
    post_event(client, cid, "consent_updated", "patient", {"channel_sms": False}, patient_ref="Patient/pg")
    did = make_draft(client, cid)
    out = approve(client, did).json()
    assert out["sent"] is False and out["blocked_reason"] == "consent"
    assert len(event_rows(pool, cid, "message_approved")) == 1 and event_rows(pool, cid, "message_sent") == []
    ((nid, status, event_id),) = q(pool, "SELECT id, status, event_id FROM notifications WHERE case_id = %s", (cid,))
    assert str(nid) == out["notification_id"] and status == "blocked_consent" and event_id is None
    assert draft_row(pool, did)[1] == OWNER_ACTOR  # the approval stands


def test_an_invalid_contact_is_blocked(client, pool):
    cid = owned_case(client, contact_valid={"sms": False})
    out = approve(client, make_draft(client, cid)).json()
    assert out["sent"] is False and out["blocked_reason"] == "contact"
    assert event_rows(pool, cid, "message_sent") == []


def test_reject_is_recorded_on_the_row_and_blocks_approval(client, pool):
    cid = owned_case(client)
    did = make_draft(client, cid)
    n = q(pool, "SELECT count(*) FROM events WHERE case_id = %s", (cid,))[0][0]
    r = client.post(f"/drafts/{did}/reject", json={"reason": "wrong register"}, headers=H(client, "primary_care_owner", OWNER))
    assert r.status_code == 200, r.text
    approved_at, _ab, rejected_at, rejected_by, reason = draft_row(pool, did)
    assert approved_at is None and rejected_by == OWNER_ACTOR and reason == "wrong register"
    assert rejected_at.isoformat() == r.json()["rejected_at"]
    assert q(pool, "SELECT count(*) FROM events WHERE case_id = %s", (cid,))[0][0] == n  # no event
    assert approve(client, did).status_code == 409
    assert client.post(f"/drafts/{did}/reject", json={"reason": "again"}, headers=H(client, "supervisor", "sam")).status_code == 409
    assert event_rows(pool, cid, "message_sent") == []


def test_the_database_allows_one_decision_per_draft(client, pool):
    cid = owned_case(client)
    approved, rejected, blank = make_draft(client, cid), make_draft(client, cid), make_draft(client, cid)
    approve(client, approved)
    client.post(f"/drafts/{rejected}/reject", json={"reason": "no"}, headers=H(client, "primary_care_owner", OWNER))
    for sql, did in (
        ("UPDATE drafts SET rejected_at = now(), rejected_by = 'x', rejection_reason = 'late' WHERE draft_id = %s", approved),
        ("UPDATE drafts SET approved_at = now(), approved_by = 'x' WHERE draft_id = %s", rejected),
        ("UPDATE drafts SET rejection_reason = 'edited' WHERE draft_id = %s", rejected),
        ("UPDATE drafts SET rejected_at = NULL, rejected_by = NULL, rejection_reason = NULL WHERE draft_id = %s", rejected),
        ("UPDATE drafts SET rejected_at = now(), rejected_by = 'x', rejection_reason = '  ' WHERE draft_id = %s", blank),  # blank
        ("UPDATE drafts SET rejected_at = now(), rejected_by = 'x' WHERE draft_id = %s", blank),  # no reason
        ("UPDATE drafts SET approved_at = now(), approved_by = 'x', rejected_at = now(), rejected_by = 'x',"
         " rejection_reason = 'both' WHERE draft_id = %s", blank),  # both decisions
        ("DELETE FROM drafts WHERE draft_id = %s", rejected),
    ):
        with pytest.raises(psycopg.errors.Error):
            with pool.connection() as conn:
                conn.execute(sql, (did,))
    assert draft_row(pool, blank)[:4] == (None, None, None, None)


def test_the_store_refuses_a_second_decision_directly(pool):
    ds = PostgresDraftStore(pool)
    with pytest.raises(Exception) as exc:
        ds.approve(uuid4(), "x", datetime(2026, 1, 5, tzinfo=UTC))
    assert type(exc.value).__name__ == "DraftNotFound"
