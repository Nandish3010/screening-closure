"""D13 against Postgres: migration 013, the least-privilege grants, the reply route end to end. Run with `make test-db`.

Every assertion is about this test's own cases, so a shared database is never disturbed.
"""

import json
import os
from uuid import UUID, uuid4

import psycopg
import pytest

from api_helpers import login_headers, make_client
from app.db import create_pool, run_migrations
from app.routers import cases, replies
from app.state_machine import TRIAGE_ACTOR
from app.store_pg import PostgresEventStore
from app.triage import classify as triage
from app.triage.labels import INTENTS
from app.triage.reply_store import PostgresReplyStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

CAL = triage.Calibration(1.0, 0.8, 0.5, "fake-model-1")


class Scorer:
    def score(self, text):
        want = next((i for i in INTENTS if f"intent:{i}" in text), "other")
        return {"logits": [10.0 if i == want else 0.0 for i in INTENTS], "mass": 0.99}


@pytest.fixture(scope="module")
def pool():
    p = create_pool(os.environ["DATABASE_URL"], max_size=6)
    run_migrations(p)
    yield p
    p.close()


@pytest.fixture
def client(pool, monkeypatch):
    monkeypatch.setattr(cases, "store", PostgresEventStore(pool))
    monkeypatch.setattr(replies, "_pg", None)
    triage.set_backend(Scorer(), CAL)
    yield make_client()
    triage.reset()


def q(pool, sql, params=()):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def H(client, role="coordinator"):
    return login_headers(client, role)


def new_case(client):
    return client.post("/cases", json={"patient_ref": "Patient/pg"}, headers=H(client)).json()["case_id"]


def reply(client, cid, text, **extra):
    r = client.post(f"/cases/{cid}/replies", json={"channel": "whatsapp", "text": text, **extra}, headers=H(client))
    assert r.status_code == 201, r.text
    return r.json()


def test_the_postgres_store_is_selected_when_the_event_store_is_postgres(client):
    assert isinstance(replies.reply_store(), PostgresReplyStore)


def test_row_and_events_are_written_and_the_log_holds_no_text(client, pool):
    cid = new_case(client)
    text = "intent:reschedule من فضلكم أجلوا الموعد SENTINEL-TEXT"
    out = reply(client, cid, text, language_hint="ar")
    (row,) = q(pool, "SELECT case_id, channel, text, language_hint, intent, needs_human, model_id, logged, received_by"
                     " FROM patient_replies WHERE reply_id = %s", (out["reply_id"],))
    assert row[:5] == (UUID(cid), "whatsapp", text, "ar", "reschedule") and row[5] is False and row[6] == "fake-model-1" and row[7] is True
    assert row[8].startswith("coordinator:")
    events = cases.store.events_for(UUID(cid))
    assert [e.type for e in events][1:] == ["reply_received", "reply_classified", "callback_requested"]
    assert {e.actor for e in events[1:]} == {TRIAGE_ACTOR}
    in_db = q(pool, "SELECT payload::text FROM events WHERE case_id = %s", (UUID(cid),))
    assert "SENTINEL-TEXT" not in json.dumps(in_db) and "الموعد" not in json.dumps(in_db, ensure_ascii=False)
    listed = client.get(f"/cases/{cid}/replies", headers=H(client)).json()
    assert [r["text"] for r in listed] == [text]


def test_a_retry_with_the_same_reply_id_writes_one_row_and_one_set_of_events(client, pool):
    cid = new_case(client)
    rid = str(uuid4())
    first = reply(client, cid, "intent:cannot_attend sorry", reply_id=rid)
    again = reply(client, cid, "intent:cannot_attend sorry", reply_id=rid)
    assert again == first
    assert q(pool, "SELECT count(*) FROM patient_replies WHERE case_id = %s", (UUID(cid),)) == [(1,)]
    assert len(cases.store.events_for(UUID(cid))) == 4
    other = new_case(client)
    r = client.post(f"/cases/{other}/replies", json={"channel": "sms", "text": "x", "reply_id": rid}, headers=H(client))
    assert r.status_code == 409


def test_the_table_refuses_a_bad_intent_channel_or_confidence(client, pool):
    cid = UUID(new_case(client))
    base = dict(reply_id=uuid4(), case_id=cid, channel="sms", text="hi", intent="other", confidence=0.5)
    for bad in ({"intent": "diagnosis"}, {"channel": "pigeon"}, {"confidence": 1.5}, {"text": ""}):
        row = {**base, **bad, "reply_id": uuid4()}
        with pytest.raises(psycopg.errors.CheckViolation):
            with pool.connection() as conn:
                conn.execute("INSERT INTO patient_replies (reply_id, case_id, channel, text, intent, confidence, model_id,"
                             " needs_human, logged, received_at, received_by)"
                             " VALUES (%(reply_id)s, %(case_id)s, %(channel)s, %(text)s, %(intent)s, %(confidence)s,"
                             " 'm', true, true, now(), 'x')", row)


def test_the_application_role_can_add_and_read_replies_but_not_change_or_erase_them(pool):
    rows = q(pool, "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'closure_app')")
    if not rows[0][0]:
        pytest.skip("closure_app does not exist on this server")
    privileges = {p: q(pool, "SELECT has_table_privilege('closure_app', 'patient_replies', %s)", (p,))[0][0]
                  for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")}
    assert privileges == {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": False, "TRUNCATE": False}
