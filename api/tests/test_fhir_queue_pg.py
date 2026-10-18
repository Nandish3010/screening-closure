"""Integration (make test-db): the Postgres projection retry queue with backoff and a dead-letter list (SEC-1 fix 4)."""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fhirtest import PoisonFake
from scratchdb import scratch_database

from app import fhir_projection as fp
from app.calendar import default_calendar
from app.config import settings as app_settings
from app.db import create_pool
from app.events import CASE_CREATED, Event
from app.pathway import load_pathway
from app.store_pg import PostgresEventStore, PostgresProjectionQueue

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

PATHWAY = load_pathway(app_settings.pathway_path)
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)


@pytest.fixture
def db():
    with scratch_database("fhirq") as url:
        pool = create_pool(url, max_size=4)
        try:
            yield pool
        finally:
            pool.close()


def new_case(store, patient_ref="Patient/p-1"):
    case_id = uuid4()
    genesis = Event(case_id, CASE_CREATED, AT, "system:intake", {"patient_ref": patient_ref})
    store.create_case(case_id, PATHWAY.name, genesis, PATHWAY.initial_state)
    return case_id, genesis


def next_in_seconds(pool, event_id) -> float:
    with pool.connection() as conn:
        return float(conn.execute(
            "SELECT extract(epoch FROM next_attempt_at - now()) FROM fhir_projection_queue WHERE event_id = %s", (event_id,)
        ).fetchone()[0])


def test_backoff_is_scheduled_by_the_database_and_pending_orders_by_it(db):
    store = PostgresEventStore(db)
    q = PostgresProjectionQueue(db, fp.RetryPolicy(base_seconds=100, cap_seconds=300, max_attempts=10))
    (a, _), (b, _) = new_case(store), new_case(store)
    ea, eb = uuid4(), uuid4()
    q.add(ea, a, "x")
    q.add(eb, b, "x")
    assert abs(next_in_seconds(db, ea)) < 5  # the first failure is due at once
    assert [e for e, _, _ in q.pending()] == [ea, eb]
    assert q.add(ea, a, "again") is False
    assert 90 < next_in_seconds(db, ea) <= 100  # attempts=2: base * 2^0
    q.add(ea, a, "third")
    assert 190 < next_in_seconds(db, ea) <= 200  # attempts=3: base * 2^1
    q.add(ea, a, "fourth")
    q.add(ea, a, "fifth")
    assert 290 < next_in_seconds(db, ea) <= 300  # capped
    assert [e for e, _, _ in q.pending()] == [eb, ea]  # a fell behind b
    assert [e for e, _, _ in q.pending(due_only=True)] == [eb]
    assert [x[2] for x in q.pending()] == [1, 5]


def test_dead_letter_after_max_attempts_and_revive(db):
    store = PostgresEventStore(db)
    q = PostgresProjectionQueue(db, fp.RetryPolicy(1, 2, 3))
    case, _ = new_case(store)
    e = uuid4()
    assert [q.add(e, case, f"err {i}") for i in range(3)] == [False, False, True]
    assert q.count() == 0 and q.dead_count() == 1 and q.pending() == []
    assert q.dead_since() is not None
    with db.connection() as conn:
        assert conn.execute("SELECT attempts, last_error FROM fhir_projection_dead_letter WHERE event_id = %s", (e,)).fetchone() == (3, "err 2")
    assert q.revive_dead() == 1
    assert q.count() == 1 and q.dead_count() == 0 and q.dead_since() is None
    assert q.add(e, case, "again") is False  # a fresh set of attempts


def test_101_poison_rows_and_one_good_row_the_good_row_projects(db):
    store = PostgresEventStore(db)
    queue = PostgresProjectionQueue(db, fp.RetryPolicy(30, 3600, 5))
    fake = PoisonFake(existing={"Patient/p-1"})
    projector = fp.Projector(fake, store, PATHWAY, default_calendar(), queue)
    poison = [new_case(store) for _ in range(101)]
    good_id, good_genesis = new_case(store)
    for case_id, genesis in poison + [(good_id, good_genesis)]:
        queue.add(genesis.event_id, case_id, "HAPI was down")
    fake.poison = {str(c) for c, _ in poison}
    assert queue.count() == 102
    projector.drain(limit=100)
    assert f"Task/task-{good_id}" not in fake.resources
    out = projector.drain(limit=100)
    assert f"Task/task-{good_id}" in fake.resources, "the good row was starved by the poison rows"
    assert out["projected"] == 1 and queue.count() == 101


def test_get_alerts_counts_dead_letters_from_postgres(db):
    store = PostgresEventStore(db)
    queue = PostgresProjectionQueue(db, fp.RetryPolicy(1, 2, 1))  # one attempt: dead at once
    projector = fp.Projector(PoisonFake(), store, PATHWAY, default_calendar(), queue)
    wrapped = fp.ProjectingStore(store, projector)
    assert fp.dead_letter_alert(wrapped) is None
    case_id, genesis = new_case(store)
    assert queue.add(genesis.event_id, case_id, "x") is True
    alert = fp.dead_letter_alert(wrapped)
    assert alert["kind"] == fp.DEAD_LETTER_ALERT and alert["detail"] == {"dead_lettered": 1} and alert["since"]
