"""D4/D11 against Postgres (T-D5, T-D15 integration half). Run with `make test-db`.

Every assertion is about this test's own cases and drafts (or a before/after difference), so
the shared dev database's seeded data is never disturbed.
"""

import hashlib
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
import pytest

from api_helpers import actor_of, login_headers, make_client
from app.ai.draft_store import PostgresDraftStore, PromptRecord
from app.ai.provider import SYSTEM_PROMPT
from app.db import create_pool, run_migrations
from app.routers import cases, drafts
from app.store_pg import PostgresEventStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

BODY = {"purpose": "result_notification", "channel": "sms", "language": "en"}
NOW = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def pool():
    p = create_pool(os.environ["DATABASE_URL"], max_size=6)
    run_migrations(p)
    yield p
    p.close()


@pytest.fixture
def client(pool, monkeypatch):
    monkeypatch.setattr(cases, "store", PostgresEventStore(pool))
    monkeypatch.setattr(drafts, "_pg", None)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    return make_client()


def q(pool, sql, params=()):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def H(client, role, name=None):
    return login_headers(client, role, name)


def owned_case(client):
    cid = client.post("/cases", json={"patient_ref": "Patient/pg"}, headers=H(client, "coordinator")).json()["case_id"]
    for type_, role, name in (("acknowledge", "lab", None), ("accept_ownership", "primary_care_owner", "Dr Sara Khan")):
        r = client.post(f"/cases/{cid}/events", json={"type": type_}, headers=H(client, role, name))
        assert r.status_code == 201, r.text
    return cid


def draft(client, cid, **over):
    r = client.post(f"/cases/{cid}/drafts", json={**BODY, **over}, headers=H(client, "coordinator", "Maryam"))
    assert r.status_code == 201, r.text
    return r.json()


def test_the_postgres_store_is_selected_when_the_event_store_is_postgres(client):
    assert isinstance(drafts.draft_store(), PostgresDraftStore)


def test_draft_row_prompt_row_and_event_are_all_written(client, pool):
    cid = owned_case(client)
    out = draft(client, cid)
    (row,) = q(pool,
        "SELECT case_id, purpose, channel, language, text, provider, model_id, fallback_used,"
        " abstention_reason, abstained_at, prompt_hash, created_by, created_at, approved_at, approved_by"
        " FROM drafts WHERE draft_id = %s", (out["draft_id"],))
    assert row[0] == UUID(cid) and row[1:4] == ("result_notification", "sms", "en")
    assert row[4] == out["text"] and row[5] == "template" and row[7] is False
    assert row[8] is None and row[9] is None and row[11] == actor_of("coordinator", "Maryam")
    assert row[13] is None and row[14] is None
    store = drafts.draft_store()
    prompt = store.get_prompt(out["prompt_hash"])
    assert prompt.system_prompt == SYSTEM_PROMPT
    assert hashlib.sha256((prompt.system_prompt + "\n---\n" + prompt.prompt_text).encode()).hexdigest() == out["prompt_hash"]
    # the same prompt is stored once, however many drafts use it
    draft(client, cid)
    assert q(pool, "SELECT count(*) FROM prompts WHERE prompt_hash = %s", (out["prompt_hash"],)) == [(1,)]
    events = cases.store.events_for(UUID(cid))
    drafted = [e for e in events if e.type == "message_drafted"]
    assert len(drafted) == 2 and drafted[0].actor == "model:template"
    assert drafted[0].payload["draft_id"] == out["draft_id"]
    assert [d.draft_id for d in store.list_for_case(UUID(cid))] == [UUID(e.payload["draft_id"]) for e in drafted]


def test_list_route_reads_from_postgres(client):
    cid = owned_case(client)
    a, b = draft(client, cid)["draft_id"], draft(client, cid, language="ar")["draft_id"]
    listed = client.get(f"/cases/{cid}/drafts", headers=H(client, "primary_care_owner")).json()
    assert [d["draft_id"] for d in listed] == [a, b]
    assert listed[1]["text_ar"] == listed[1]["text"] and listed[1]["text_en"] is None


def test_consent_frozen_is_409_and_writes_nothing(client, pool):
    cid = owned_case(client)
    r = client.post(f"/cases/{cid}/events", json={"type": "consent_updated", "payload": {"withdrawn": True}},
                    headers=H(client, "coordinator"))
    assert r.status_code == 201
    r = client.post(f"/cases/{cid}/drafts", json=BODY, headers=H(client, "coordinator"))
    assert r.status_code == 409
    assert drafts.draft_store().list_for_case(UUID(cid)) == []


def _insert(conn, **over):
    prompt = PromptRecord("a" * 64, "s", "p")
    conn.execute(
        "INSERT INTO prompts (prompt_hash, system_prompt, prompt_text) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        (prompt.prompt_hash, prompt.system_prompt, prompt.prompt_text),
    )
    cols = dict(draft_id=uuid4(), case_id=None, purpose="reminder", channel="sms", language="en", text="t",
                provider="template", model_id="m", fallback_used=False, abstention_reason=None, abstained_at=None,
                prompt_hash="a" * 64, created_by="coordinator:x", created_at=NOW)
    cols.update(over)
    conn.execute(f"INSERT INTO drafts ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", list(cols.values()))
    return cols["draft_id"]


@pytest.fixture
def case_id(client):
    return UUID(owned_case(client))


def test_database_constraints(pool, case_id):
    for bad in (
        dict(abstention_reason="cache_miss"),  # reason without a time
        dict(abstained_at=NOW),  # time without a reason
        dict(fallback_used=False, abstention_reason="cache_miss", abstained_at=NOW),  # abstention but no fallback
        dict(approved_at=NOW),  # approval without approver
        dict(prompt_hash="b" * 64),  # prompt must exist
        dict(case_id=uuid4()),  # case must exist
    ):
        with pytest.raises(psycopg.errors.Error):
            with pool.connection() as conn:
                _insert(conn, **{"case_id": case_id, **bad})


def test_a_draft_changes_only_by_being_approved_once(pool, case_id):
    with pool.connection() as conn:
        d = _insert(conn, case_id=case_id)
    for sql in (
        "UPDATE drafts SET text = 'edited' WHERE draft_id = %s",
        "UPDATE drafts SET abstention_reason = 'x', abstained_at = now() WHERE draft_id = %s",
        "UPDATE drafts SET approved_at = now() WHERE draft_id = %s",  # approver missing (check constraint)
        "DELETE FROM drafts WHERE draft_id = %s",
    ):
        with pytest.raises(psycopg.errors.Error):
            with pool.connection() as conn:
                conn.execute(sql, (d,))
    with pool.connection() as conn:
        conn.execute("UPDATE drafts SET approved_at = now(), approved_by = 'primary_care_owner:x' WHERE draft_id = %s", (d,))
    for sql in (
        "UPDATE drafts SET approved_by = 'someone_else' WHERE draft_id = %s",
        "UPDATE drafts SET approved_at = NULL, approved_by = NULL WHERE draft_id = %s",
    ):
        with pytest.raises(psycopg.errors.Error):
            with pool.connection() as conn:
                conn.execute(sql, (d,))


def test_prompts_are_append_only(pool, case_id):
    with pool.connection() as conn:
        _insert(conn, case_id=case_id)
    for sql in ("UPDATE prompts SET prompt_text = 'x' WHERE prompt_hash = %s",
                "DELETE FROM prompts WHERE prompt_hash = %s"):
        with pytest.raises(psycopg.errors.Error):
            with pool.connection() as conn:
                conn.execute(sql, ("a" * 64,))
