"""Postgres connection pool and the in-app migration runner."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import psycopg
from psycopg_pool import ConnectionPool

log = logging.getLogger("app.db")

# api/app/db.py -> api/migrations (also /app/migrations in the container image)
DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

APP_ROLE = "closure_app"  # the unprivileged role the API connects as (migration 009)

# Arbitrary constant: serialises concurrent migration runners (e.g. several api replicas).
_MIGRATION_LOCK_KEY = 727_001


def create_pool(database_url: str, *, min_size: int = 1, max_size: int = 10) -> ConnectionPool:
    pool = ConnectionPool(database_url, min_size=min_size, max_size=max_size, open=True)
    pool.wait(timeout=30)
    return pool


def migration_files(migrations_dir: Path | None = None) -> list[Path]:
    directory = migrations_dir or Path(os.environ.get("MIGRATIONS_DIR", DEFAULT_MIGRATIONS_DIR))
    return sorted(directory.glob("*.sql"), key=lambda p: p.name)


class MigrationChecksumError(RuntimeError):
    """An applied migration file no longer matches what was applied (SEC-1)."""


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ensure_schema_migrations(conn) -> None:
    """Create `schema_migrations` (or add its `checksum` column to a table made before checksums existed).
    Called only while holding the advisory lock, so two runners cannot race on the DDL. Existing, complete tables
    are left alone without any DDL, so a role that may not create tables can still check them."""
    if conn.execute("SELECT to_regclass('schema_migrations')").fetchone()[0] is None:
        conn.execute(
            "CREATE TABLE schema_migrations ("
            " version text PRIMARY KEY,"
            " checksum text,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )
        return
    has_checksum = conn.execute(
        "SELECT 1 FROM information_schema.columns"
        " WHERE table_schema = current_schema() AND table_name = 'schema_migrations' AND column_name = 'checksum'"
    ).fetchone()
    if not has_checksum:
        conn.execute("ALTER TABLE schema_migrations ADD COLUMN checksum text")


def run_migrations(pool: ConnectionPool, migrations_dir: Path | None = None) -> list[str]:
    """Apply each *.sql file, in filename order, once. Returns the versions applied now.

    Every applied file's sha256 is stored with it, and a file that no longer matches what was applied stops the
    run with `MigrationChecksumError` before anything else is applied: an applied migration is history, and editing
    it would leave databases that applied the old text and databases that apply the new one with different
    schemas. (A table from before checksums existed is filled in from the files as they are now, once, with a
    warning: the original text is unknowable.) The table is created, and everything is checked and applied, while
    holding an advisory lock, so several API replicas starting together cannot race."""
    files = migration_files(migrations_dir)
    contents = {p.name: p.read_bytes() for p in files}
    applied: list[str] = []

    with pool.connection() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_KEY,))
        _ensure_schema_migrations(conn)
        _verify(conn, contents)

    for path in files:
        version = path.name
        with pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_KEY,))
            row = conn.execute(
                "SELECT checksum FROM schema_migrations WHERE version = %s", (version,)
            ).fetchone()
            if row:
                _check_one(version, row[0], contents[version])  # applied by a runner that held the lock before us
                continue
            try:
                conn.execute(contents[version].decode("utf-8"))  # no params: multi-statement script is allowed
            except psycopg.errors.InsufficientPrivilege as exc:
                raise RuntimeError(
                    f"migration {version} is pending and this database role may not change the schema ({exc}). "
                    "Apply migrations as the owner: `python -m app.migrate` with the owner's DATABASE_URL "
                    "(compose: the `migrate` service)."
                ) from exc
            conn.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                (version, checksum(contents[version])),
            )
            applied.append(version)
            log.info("migration applied: %s", version)
    if not applied:
        log.info("migrations: nothing to apply (%d already recorded)", len(files))
    return applied


def _check_one(version: str, recorded: str | None, data: bytes) -> None:
    if recorded is not None and recorded != checksum(data):
        raise MigrationChecksumError(
            f"migration {version} was changed after it was applied: the database recorded sha256 {recorded}, "
            f"the file is now {checksum(data)}. Never edit an applied migration; add a new numbered one "
            "(or, in a throwaway database, drop it and start again)."
        )


def _verify(conn, contents: dict[str, bytes]) -> None:
    rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    legacy = []
    for version, recorded in rows:
        if version not in contents:
            log.warning("migration %s is recorded in the database but has no file here (database newer than code?)", version)
            continue
        if recorded is None:
            legacy.append(version)
        else:
            _check_one(version, recorded, contents[version])
    if legacy:
        try:
            with conn.transaction():  # a savepoint: a role without UPDATE rights must not poison the transaction
                for version in legacy:
                    conn.execute(
                        "UPDATE schema_migrations SET checksum = %s WHERE version = %s AND checksum IS NULL",
                        (checksum(contents[version]), version),
                    )
            log.warning("migrations %s had no recorded checksum; recorded the current files' sha256 now", ", ".join(legacy))
        except psycopg.errors.InsufficientPrivilege:
            log.warning("migrations %s have no recorded checksum and this role cannot record one", ", ".join(legacy))
