"""Integration (make test-db): the migration runner locks around its own DDL and refuses edited migrations (SEC-1 fix 6)."""

import hashlib
import os
import shutil
import threading
from pathlib import Path

import pytest
from scratchdb import scratch_database

from app import db
from app.db import MigrationChecksumError, create_pool, migration_files, run_migrations

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

REAL = Path(db.DEFAULT_MIGRATIONS_DIR)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def pool():
    with scratch_database("mig") as url:  # already migrated by scratch_database
        p = create_pool(url, max_size=8)
        try:
            yield p
        finally:
            p.close()


@pytest.fixture
def copy_of_migrations(tmp_path):
    target = tmp_path / "migrations"
    shutil.copytree(REAL, target)
    return target


@pytest.fixture
def empty_pool():
    """A bare database: nothing applied, not even schema_migrations."""
    import psycopg
    import uuid
    from urllib.parse import urlsplit, urlunsplit

    base = os.environ["DATABASE_URL"]
    name = f"closure_mig_{uuid.uuid4().hex[:8]}_test"
    admin = urlunsplit(urlsplit(base)._replace(path="/postgres"))
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    url = urlunsplit(urlsplit(base)._replace(path="/" + name))
    p = create_pool(url, max_size=8)
    try:
        yield p
    finally:
        p.close()
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def recorded(pool) -> dict[str, str | None]:
    with pool.connection() as conn:
        return dict(conn.execute("SELECT version, checksum FROM schema_migrations").fetchall())


def test_every_applied_migration_stores_its_sha256(pool):
    assert recorded(pool) == {p.name: sha(p) for p in migration_files()}


def test_a_second_run_applies_nothing_and_changes_nothing(pool):
    before = recorded(pool)
    assert run_migrations(pool) == []
    assert recorded(pool) == before


def test_an_edited_applied_migration_is_refused_with_a_clear_error(pool, copy_of_migrations):
    run_migrations(pool, copy_of_migrations)  # a copy of the same files: nothing to apply, all hashes agree
    victim = copy_of_migrations / "004_users.sql"
    original = sha(victim)
    victim.write_text(victim.read_text() + "\n-- a harmless looking edit\n")
    with pytest.raises(MigrationChecksumError) as exc:
        run_migrations(pool, copy_of_migrations)
    message = str(exc.value)
    assert "004_users.sql" in message and original in message and sha(victim) in message
    assert "add a new numbered one" in message


def test_nothing_is_applied_when_any_earlier_migration_was_edited(pool, copy_of_migrations):
    (copy_of_migrations / "001_events.sql").write_text((copy_of_migrations / "001_events.sql").read_text() + "\n-- edit\n")
    (copy_of_migrations / "999_new.sql").write_text("CREATE TABLE should_not_exist (id int);")
    with pytest.raises(MigrationChecksumError):
        run_migrations(pool, copy_of_migrations)
    with pool.connection() as conn:
        assert conn.execute("SELECT to_regclass('should_not_exist')").fetchone()[0] is None
    assert "999_new.sql" not in recorded(pool)


def test_a_new_migration_is_applied_once_with_its_checksum(pool, copy_of_migrations):
    (copy_of_migrations / "999_new.sql").write_text("CREATE TABLE sec1_probe (id int);")
    assert run_migrations(pool, copy_of_migrations) == ["999_new.sql"]
    assert recorded(pool)["999_new.sql"] == sha(copy_of_migrations / "999_new.sql")
    assert run_migrations(pool, copy_of_migrations) == []


def test_a_table_from_before_checksums_is_filled_in_once(pool, copy_of_migrations):
    with pool.connection() as conn:
        conn.execute("ALTER TABLE schema_migrations DROP COLUMN checksum")
    assert run_migrations(pool, copy_of_migrations) == []
    assert recorded(pool) == {p.name: sha(p) for p in migration_files(copy_of_migrations)}
    with pool.connection() as conn:  # only some rows lack a checksum (an interrupted backfill): those are filled in
        conn.execute("UPDATE schema_migrations SET checksum = NULL WHERE version = '002_sim_clock.sql'")
    run_migrations(pool, copy_of_migrations)
    assert recorded(pool)["002_sim_clock.sql"] == sha(copy_of_migrations / "002_sim_clock.sql")


def test_schema_migrations_is_created_inside_the_lock_so_concurrent_starts_cannot_race(empty_pool):
    """Six API replicas starting on an empty database: no error, every migration applied exactly once."""
    results, errors = [], []
    barrier = threading.Barrier(6)

    def start():
        try:
            barrier.wait()
            results.append(run_migrations(empty_pool))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=start) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    names = [p.name for p in migration_files()]
    applied = [v for r in results for v in r]
    assert sorted(applied) == sorted(names)  # each exactly once across all runners
    assert set(recorded(empty_pool)) == set(names)


def test_checksum_is_the_sha256_of_the_file_bytes():
    assert db.checksum(b"abc") == hashlib.sha256(b"abc").hexdigest()
