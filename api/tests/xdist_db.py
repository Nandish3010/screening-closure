"""One database per pytest-xdist worker for the integration tests (F15).

Run serially (`-n0`, `make test-chaos`, a bare `pytest`), the integration tests use the database named in DATABASE_URL
(`closure_test`) exactly as before. Run under xdist (`make test-db` uses `-n auto --dist loadfile`), every worker repoints
DATABASE_URL at its own database, `<name>_gw0`, `<name>_gw1`, ..., created from a migrated template database `<name>_tpl`
with `CREATE DATABASE ... TEMPLATE`. Test files that share a database today (they never clean up) still share one inside a
worker, and `--dist loadfile` keeps a file on one worker, so the tests see what they always saw; two workers never
share rows. Every name keeps the `_test` guard of ADR-015 (testdb.WORKER_DB_RE), the dev database is never touched, and only
databases this module named are created or dropped.

The template is checked under an advisory lock on the maintenance database, so workers starting together take turns; it is
rebuilt whenever its recorded migrations differ from the files (a branch with newer migrations used it last) or fail their
checksums, and no session ever connects to it except to migrate it (a template with a connection cannot be copied).
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

from testdb import assert_test_database, database_name

_LOCK_KEY = 0x46313554  # "F15T"


def _with_db(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path="/" + name))


def worker_id(config) -> str | None:
    """`gw0`, `gw1`, ... inside an xdist worker, None in the controller or a serial run. (Looked up on the pytest config,
    not the environment: a pytest started by a test inherits PYTEST_XDIST_WORKER but is not a worker.)"""
    return getattr(config, "workerinput", {}).get("workerid")


def _template_is_current(admin: str, tpl_url: str) -> bool:
    from app.db import migration_files

    want = [p.name for p in migration_files()]
    try:
        with psycopg.connect(tpl_url, autocommit=True) as c:
            have = [r[0] for r in c.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
    except psycopg.Error:
        return False
    return have == want


def _build_template(admin: str, tpl: str, tpl_url: str) -> None:
    from app.db import create_pool, run_migrations

    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(tpl)))
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(tpl)))
    pool = create_pool(tpl_url, max_size=2)
    try:
        run_migrations(pool)
    finally:
        pool.close()


def prepare_worker_database(base_url: str, worker: str) -> str:
    """Create (replacing any leftover) `<base>_<worker>` from the migrated template; return its URL."""
    base = assert_test_database(base_url)
    tpl, mine = f"{base}_tpl", f"{base}_{worker}"
    tpl_url, my_url = _with_db(base_url, tpl), _with_db(base_url, mine)
    assert_test_database(tpl_url)
    assert_test_database(my_url)
    admin = _with_db(base_url, "postgres")
    with psycopg.connect(admin, autocommit=True) as lock_conn:
        lock_conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
        try:
            if not _template_is_current(admin, tpl_url):
                _build_template(admin, tpl, tpl_url)
            lock_conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(mine)))
            lock_conn.execute(sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(sql.Identifier(mine), sql.Identifier(tpl)))
        finally:
            lock_conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
    return my_url


def drop_worker_database(url: str) -> None:
    name = database_name(url)
    assert_test_database(url)
    with psycopg.connect(_with_db(url, "postgres"), autocommit=True) as c:
        c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
