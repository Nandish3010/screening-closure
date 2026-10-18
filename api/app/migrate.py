"""`python -m app.migrate`: apply the migrations as the database owner and set the application role's password.

The API connects as the unprivileged `closure_app` (migration 009) and cannot create tables, so the schema is
changed by this separate step, run with the owner's `DATABASE_URL` (compose: the one-shot `migrate` service, which
the API waits for). It is idempotent, checks the stored checksums like the API does (app.db.run_migrations), and
then sets the password of `closure_app` from APP_DB_PASSWORD so the role can log in. With APP_DB_PASSWORD unset the
role keeps whatever password it has (none, on a new database: nobody can log in as it).

Environment: DATABASE_URL (the owner / superuser), APP_DB_PASSWORD (optional).
"""

from __future__ import annotations

import logging
import os
import sys

from psycopg import sql

from app.db import APP_ROLE, create_pool, run_migrations

log = logging.getLogger("app.migrate")


def set_app_role_password(pool, password: str) -> None:
    """ALTER ROLE does not take bind parameters, so the literal is composed with psycopg's escaping."""
    with pool.connection() as conn:
        conn.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(sql.Identifier(APP_ROLE), sql.Literal(password))
        )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("app.migrate: DATABASE_URL (the owner's) is required", file=sys.stderr)
        return 2
    pool = create_pool(url, max_size=2)
    try:
        applied = run_migrations(pool)
        log.info("migrate: %d applied now", len(applied))
        password = os.environ.get("APP_DB_PASSWORD")
        if password:
            set_app_role_password(pool, password)
            log.info("migrate: password of role %s set from APP_DB_PASSWORD", APP_ROLE)
        else:
            log.warning("migrate: APP_DB_PASSWORD is not set; role %s keeps its current password", APP_ROLE)
    finally:
        pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
