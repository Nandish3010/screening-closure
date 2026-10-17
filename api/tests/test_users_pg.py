"""T-A36 against Postgres (`make test-db`): display names live in the `users` table (migration 004)."""

import os

import pytest

from app.auth import Identity, opaque_user_id
from app.db import create_pool, run_migrations
from app.store_pg import PostgresUserDirectory
from app.users import UserRecord

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]


@pytest.fixture(scope="module")
def pool():
    p = create_pool(os.environ["DATABASE_URL"], max_size=4)
    run_migrations(p)
    yield p
    p.close()


def test_upsert_get_and_refresh_of_a_display_name(pool):
    d = PostgresUserDirectory(pool)
    uid = opaque_user_id("lab", "Pg Test Lab Person")
    d.upsert(UserRecord(uid, "lab", "Pg Test Lab Person", None))
    assert d.get(uid) == UserRecord(uid, "lab", "Pg Test Lab Person", None)
    d.upsert(UserRecord(uid, "lab", "Pg Test Lab Person", "unit-1"))
    assert d.get(uid).provider == "unit-1"
    assert d.get("nobody0000000") is None


def test_ensure_writes_a_missing_row_once_and_the_person_can_be_erased(pool):
    d = PostgresUserDirectory(pool)
    ident = Identity("user", "coordinator", "Pg Ensure Person")
    with pool.connection() as conn:
        conn.execute("DELETE FROM users WHERE user_id = %s", (ident.user_id,))
    assert d.get(ident.user_id) is None
    d.ensure(ident)
    assert d.get(ident.user_id).display_name == "Pg Ensure Person"
    # erasure is one row here; the event log holds only the opaque id
    with pool.connection() as conn:
        conn.execute("DELETE FROM users WHERE user_id = %s", (ident.user_id,))
    assert d.get(ident.user_id) is None


def test_the_users_table_has_no_event_or_case_columns(pool):
    with pool.connection() as conn:
        cols = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'users'").fetchall()}
    assert cols == {"user_id", "role", "display_name", "provider", "created_at", "updated_at"}
