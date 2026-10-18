"""Integration (make test-db): the Postgres store answers `has_events_after` (SEC-1 fix 3)."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.config import settings as app_settings
from app.db import create_pool
from app.events import CASE_CREATED, Event
from app.pathway import load_pathway
from app.store_pg import PostgresEventStore
from scratchdb import scratch_database

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

PATHWAY = load_pathway(app_settings.pathway_path)


def test_has_events_after_is_strict_and_sees_the_latest_event():
    with scratch_database("clockpg") as url:  # its own database: the fixed date below must not leak into other tests
        pool = create_pool(url, max_size=2)
        try:
            store = PostgresEventStore(pool)
            when = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
            assert store.has_events_after(when - timedelta(days=365)) is False  # an empty log
            case_id = uuid4()
            store.create_case(case_id, PATHWAY.name, Event(case_id, CASE_CREATED, when, "system:intake", {}), PATHWAY.initial_state)
            assert store.has_events_after(when) is False  # strictly after
            assert store.has_events_after(when - timedelta(seconds=1)) is True
        finally:
            pool.close()
