"""Postgres connection pool and the in-app migration runner."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from psycopg_pool import ConnectionPool

log = logging.getLogger("app.db")

# api/app/db.py -> api/migrations (also /app/migrations in the container image)
DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

# Arbitrary constant: serialises concurrent migration runners (e.g. several api replicas).
_MIGRATION_LOCK_KEY = 727_001


def create_pool(database_url: str, *, min_size: int = 1, max_size: int = 10) -> ConnectionPool:
    pool = ConnectionPool(database_url, min_size=min_size, max_size=max_size, open=True)
    pool.wait(timeout=30)
    return pool


def migration_files(migrations_dir: Path | None = None) -> list[Path]:
    directory = migrations_dir or Path(os.environ.get("MIGRATIONS_DIR", DEFAULT_MIGRATIONS_DIR))
    return sorted(directory.glob("*.sql"), key=lambda p: p.name)


def run_migrations(pool: ConnectionPool, migrations_dir: Path | None = None) -> list[str]:
    """Apply each *.sql file, in filename order, once. Returns the versions applied now."""
    files = migration_files(migrations_dir)
    applied: list[str] = []
    with pool.connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version text PRIMARY KEY,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )
    for path in files:
        version = path.name
        with pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_KEY,))
            row = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = %s", (version,)
            ).fetchone()
            if row:
                continue
            conn.execute(path.read_text())  # no params: multi-statement script is allowed
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            applied.append(version)
            log.info("migration applied: %s", version)
    if not applied:
        log.info("migrations: nothing to apply (%d already recorded)", len(files))
    return applied
