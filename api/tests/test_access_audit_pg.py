"""The access audit against Postgres: migration 021, the window, the append-only guard and the least-privilege grant.
Run with `make test-db`. Every assertion is about this test's own cases, so a shared database is never disturbed."""

import os
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest

from api_helpers import login_headers, make_client
from app import access_audit
from app.clock import clock
from app.db import create_pool, run_migrations
from app.routers import cases
from app.store_pg import PostgresEventStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]


@pytest.fixture(scope="module")
def pool():
    p = create_pool(os.environ["DATABASE_URL"], max_size=6)
    run_migrations(p)
    yield p
    p.close()


@pytest.fixture
def client(pool, monkeypatch):
    monkeypatch.setattr(cases, "store", PostgresEventStore(pool))
    monkeypatch.setattr(access_audit, "log_store", access_audit.PostgresAccessLog(pool))
    clock.reset()
    yield make_client()
    clock.reset()


def q(pool, sql, params=()):
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def test_reads_are_logged_once_per_window_and_a_patient_reads_them(client, pool):
    coord = login_headers(client, "coordinator", "Pg Coordinator")
    ref = f"Patient/pg-{uuid4().hex[:8]}"
    pat = login_headers(client, "patient", "Pg Patient", patient_ref=ref)
    cid = client.post("/cases", json={"patient_ref": ref}, headers=coord).json()["case_id"]
    for _ in range(5):
        assert client.get(f"/cases/{cid}", headers=coord).status_code == 200
    assert q(pool, "SELECT count(*) FROM access_audit WHERE case_id = %s", (cid,))[0][0] == 1
    clock.advance(hours=1)
    client.get(f"/cases/{cid}", headers=coord)
    entries = client.get(f"/cases/{cid}/access-log", headers=pat).json()["entries"]
    assert [e["name"] for e in entries] == ["Pg Coordinator", "Pg Coordinator"]
    assert all(set(e) == {"at", "role", "name"} for e in entries)  # nothing but role, name and time


def test_the_table_is_append_only(client, pool):
    coord = login_headers(client, "coordinator", "Pg Coordinator")
    cid = client.post("/cases", json={"patient_ref": f"Patient/pg-{uuid4().hex[:8]}"}, headers=coord).json()["case_id"]
    client.get(f"/cases/{cid}", headers=coord)
    with pytest.raises(psycopg.errors.RaiseException):
        q(pool, "UPDATE access_audit SET role = 'lab' WHERE case_id = %s", (cid,))
    with pytest.raises(psycopg.errors.RaiseException):
        q(pool, "DELETE FROM access_audit WHERE case_id = %s", (cid,))
    assert timedelta(minutes=15) == access_audit.WINDOW


def test_the_application_role_can_add_and_read_but_not_change_rows(pool):
    if not q(pool, "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'closure_app')")[0][0]:
        pytest.skip("closure_app does not exist on this server")
    privileges = {p: q(pool, "SELECT has_table_privilege('closure_app', 'access_audit', %s)", (p,))[0][0]
                  for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")}
    assert privileges == {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": False, "TRUNCATE": False}
