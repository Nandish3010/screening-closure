"""Integration (make test-db): the API's database role `closure_app` can do the application's work and nothing more
(SEC-1 fix 2, api/migrations/009_app_role.sql).

The role is exercised with `SET ROLE closure_app` inside a superuser connection, so these tests neither need nor change
the role's password (which the compose stack set for its own API). A real login as the role is covered by the compose
sanity run (`make up`, /health, `make demo-seed`) in docs/results or the SEC-1 report.
"""

import os
import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg import errors
from scratchdb import scratch_database

from app.db import APP_ROLE, create_pool, run_migrations

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def url():
    with scratch_database("approle") as u:
        yield u


@pytest.fixture
def conn(url):
    """A superuser connection that has switched to the application role (autocommit: one statement, one outcome)."""
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(f"SET ROLE {APP_ROLE}")
        yield c


def seed_case(url) -> uuid.UUID:
    """A case and one event, written as the owner (the migration role), so the role has rows to try things on."""
    case_id = uuid.uuid4()
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("INSERT INTO cases (id, pathway, current_state) VALUES (%s, 'p', 'result_received')", (case_id,))
        c.execute(
            "INSERT INTO events (event_id, case_id, seq, type, at, actor) VALUES (%s, %s, 1, 'case_created', %s, 'system:intake')",
            (uuid.uuid4(), case_id, AT),
        )
    return case_id


def test_the_role_is_not_privileged(url):
    with psycopg.connect(url) as c:
        row = c.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolcanlogin"
            " FROM pg_roles WHERE rolname = %s", (APP_ROLE,)
        ).fetchone()
    assert row == (False, False, False, False, False, True)


def test_it_owns_nothing(url):
    with psycopg.connect(url) as c:
        owned = c.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner WHERE r.rolname = %s", (APP_ROLE,)
        ).fetchone()[0]
        tables_by_owner = c.execute(
            "SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    assert owned == 0
    assert APP_ROLE not in {r[0] for r in tables_by_owner}  # the tables belong to the superuser that migrated


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE events ADD COLUMN sneaky int",
        "ALTER TABLE events DISABLE TRIGGER ALL",
        "ALTER TABLE events DISABLE TRIGGER events_no_update_delete",
        "ALTER TABLE events DISABLE TRIGGER USER",
        "DROP TRIGGER events_no_update_delete ON events",
        "ALTER TABLE events OWNER TO closure_app",
        "ALTER TABLE events RENAME TO events_old",
        "DROP TABLE events",
        "DROP TABLE cases CASCADE",
        "TRUNCATE events",
        "TRUNCATE notifications",
        "CREATE TABLE sneaky (id int)",
        "CREATE FUNCTION sneaky() RETURNS int LANGUAGE sql AS 'SELECT 1'",
        "CREATE TRIGGER sneaky BEFORE INSERT ON events FOR EACH ROW EXECUTE FUNCTION events_forbid_mutation()",
        "CREATE OR REPLACE FUNCTION events_forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RETURN NEW; END'",
        "CREATE INDEX sneaky ON events (type)",
        "CREATE ROLE sneaky",
        "ALTER ROLE closure_app SUPERUSER",
        "SET session_replication_role = replica",  # would switch every trigger off for the session
        "CREATE SCHEMA sneaky",
        "COPY events TO PROGRAM 'true'",
    ],
)
def test_the_role_cannot_change_the_schema_or_switch_triggers_off(conn, statement):
    with pytest.raises((errors.InsufficientPrivilege, errors.ObjectNotInPrerequisiteState)):
        conn.execute(statement)


def test_it_can_add_events_and_read_them_but_not_change_or_remove_them(url, conn):
    case_id = seed_case(url)
    conn.execute(
        "INSERT INTO events (event_id, case_id, seq, type, at, actor) VALUES (%s, %s, 2, 'acknowledge', %s, 'lab:abc')",
        (uuid.uuid4(), case_id, AT),
    )
    assert conn.execute("SELECT count(*) FROM events WHERE case_id = %s", (case_id,)).fetchone()[0] == 2
    for statement in (
        "UPDATE events SET actor = 'x'",
        "DELETE FROM events",
        "UPDATE notifications SET body = 'x'",
        "DELETE FROM notifications",
        "DELETE FROM prompts",
        "UPDATE prompts SET system_prompt = 'x'",
        "DELETE FROM drafts",
        "DELETE FROM cases",
        "DELETE FROM users",
    ):
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(statement)  # refused by the privilege itself, before any trigger is reached


def test_it_can_do_the_applications_other_work(url, conn):
    case_id = seed_case(url)
    conn.execute("UPDATE cases SET current_state = 'acknowledged', updated_at = now() WHERE id = %s", (case_id,))
    assert conn.execute("SELECT current_state FROM cases WHERE id = %s FOR UPDATE", (case_id,)).fetchone()[0] == "acknowledged"
    conn.execute("INSERT INTO users (user_id, role, display_name) VALUES ('u1', 'lab', 'A') ON CONFLICT (user_id) DO UPDATE SET display_name = 'B'")
    conn.execute("INSERT INTO sim_clock (clock_id, start_at, now_at) VALUES ('t1', %s, %s)", (AT, AT))
    conn.execute("UPDATE sim_clock SET now_at = %s WHERE clock_id = 't1'", (AT,))
    conn.execute("INSERT INTO heartbeats (service, info) VALUES ('scheduler', '{}') ON CONFLICT (service) DO UPDATE SET beat_at = now()")
    conn.execute("INSERT INTO system_alerts (kind) VALUES ('scheduler_down')")
    conn.execute("UPDATE system_alerts SET cleared_at = now() WHERE kind = 'scheduler_down'")
    ev = uuid.uuid4()
    conn.execute("INSERT INTO fhir_projection_queue (event_id, case_id) VALUES (%s, %s)", (ev, case_id))
    conn.execute("UPDATE fhir_projection_queue SET attempts = 2 WHERE event_id = %s", (ev,))
    conn.execute("DELETE FROM fhir_projection_queue WHERE event_id = %s", (ev,))
    conn.execute("INSERT INTO fhir_projection_dead_letter (event_id, case_id, attempts, queued_at) VALUES (%s, %s, 3, now())", (ev, case_id))
    conn.execute("DELETE FROM fhir_projection_dead_letter WHERE event_id = %s", (ev,))
    prompt = "a" * 64
    conn.execute("INSERT INTO prompts (prompt_hash, system_prompt, prompt_text) VALUES (%s, 's', 't') ON CONFLICT DO NOTHING", (prompt,))
    draft = uuid.uuid4()
    conn.execute(
        "INSERT INTO drafts (draft_id, case_id, purpose, channel, language, text, provider, model_id, fallback_used, prompt_hash,"
        " created_by, created_at) VALUES (%s, %s, 'reminder', 'sms', 'en', 't', 'template', 'm', false, %s, 'coordinator:x', %s)",
        (draft, case_id, prompt, AT),
    )
    conn.execute("UPDATE drafts SET approved_at = %s, approved_by = 'primary_care_owner:z' WHERE draft_id = %s", (AT, draft))
    conn.execute(
        "INSERT INTO notifications (id, case_id, channel, language, purpose, body, status, created_at)"
        " VALUES (%s, %s, 'sms', 'en', 'reminder', 'b', 'blocked_consent', %s)", (uuid.uuid4(), case_id, AT),
    )
    assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] >= 10


def test_the_triggers_still_stop_a_superuser_but_the_role_never_gets_that_far(url):
    """Defence in depth: the append-only triggers are untouched by 009."""
    case_id = seed_case(url)
    with psycopg.connect(url, autocommit=True) as c:
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE events SET actor = 'x' WHERE case_id = %s", (case_id,))


def test_the_role_cannot_write_the_migration_bookkeeping(conn):
    for statement in ("INSERT INTO schema_migrations (version) VALUES ('999')", "UPDATE schema_migrations SET checksum = 'x'",
                      "DELETE FROM schema_migrations"):
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(statement)


def as_app_pool(url):
    """A pool whose every connection is switched to the role, the way a login as closure_app would be."""
    from psycopg_pool import ConnectionPool

    def configure(c):
        c.execute(f"SET ROLE {APP_ROLE}")
        c.commit()

    pool = ConnectionPool(url, min_size=1, max_size=1, configure=configure, open=True)
    pool.wait(timeout=10)
    return pool


def test_the_role_can_run_the_migration_check_when_nothing_is_pending(url):
    """What the API does at start-up as closure_app: verify checksums, find nothing to apply, create nothing."""
    pool = as_app_pool(url)
    try:
        assert run_migrations(pool) == []
    finally:
        pool.close()


def test_a_pending_migration_as_the_role_fails_with_a_pointer_to_the_owner_step(url, tmp_path):
    import shutil

    from app import db

    migrations = tmp_path / "m"
    shutil.copytree(db.DEFAULT_MIGRATIONS_DIR, migrations)
    (migrations / "999_new.sql").write_text("CREATE TABLE sec1_new (id int);")
    pool = as_app_pool(url)
    try:
        with pytest.raises(RuntimeError, match="python -m app.migrate"):
            run_migrations(pool, migrations)
    finally:
        pool.close()


def test_migrate_composes_the_password_statement_with_escaping_and_never_logs_it(caplog):
    """app.migrate sets the cluster-wide role's password, so this test only looks at the statement it would run."""
    from app import migrate

    executed = []

    class Conn:
        def execute(self, query):
            executed.append(query)

    class Pool:
        def connection(self):
            from contextlib import contextmanager

            @contextmanager
            def cm():
                yield Conn()

            return cm()

    migrate.set_app_role_password(Pool(), "it's a \"tricky\" pass;word")
    (query,) = executed
    text = query.as_string(None)
    assert text.startswith('ALTER ROLE "closure_app" WITH LOGIN PASSWORD ')
    assert "'it''s a \"tricky\" pass;word'" in text  # the quote is doubled: the literal cannot be broken out of
    assert "tricky" not in caplog.text
