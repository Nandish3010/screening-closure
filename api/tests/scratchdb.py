"""Scratch databases for integration tests that need a whole database to themselves (A17, F4).

`scratch_database()` creates `closure_<tag>_<random>_test` next to the test database named in
DATABASE_URL (the name ends in `_test`, so the F2 guard accepts it), migrates it, yields its URL and
drops it afterwards (`DROP DATABASE ... WITH (FORCE)`). It refuses to run unless DATABASE_URL itself
passes the test-database guard, and it only ever drops the database it created. Sweeping a scratch
database cannot disturb other tests, which a sweep of the shared `closure_test` could.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

from testdb import assert_test_database

AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # Monday


def _with_db(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path="/" + name))


@contextmanager
def scratch_database(tag: str = "scratch") -> Iterator[str]:
    base = os.environ["DATABASE_URL"]
    assert_test_database(base)
    name = f"closure_{tag}_{uuid.uuid4().hex[:8]}_test"
    assert_test_database(_with_db(base, name))
    admin = _with_db(base, "postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = _with_db(base, name)
    try:
        from app.db import create_pool, run_migrations

        pool = create_pool(url, max_size=2)
        try:
            run_migrations(pool)
        finally:
            pool.close()
        yield url
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def seed_open_cases(store, pathway, count: int, *, salt: str = "a17", created: datetime = AT, workers: int = 8) -> list[uuid.UUID]:
    """`count` fresh cases in the initial state with deterministic ids (uuid5 of salt and index), so
    two databases seeded with the same salt hold identical logs. Created concurrently."""
    from concurrent.futures import ThreadPoolExecutor

    from app.events import CASE_CREATED, Event

    ids = [uuid.uuid5(uuid.NAMESPACE_URL, f"{salt}/{i}") for i in range(count)]

    def make(case_id):
        store.create_case(
            case_id, pathway.name,
            Event(case_id, CASE_CREATED, created, "system:intake", {}, uuid.uuid5(case_id, "genesis")),
            pathway.initial_state,
        )

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(make, ids))
    return ids
