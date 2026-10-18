"""SEC-2 fix 10: /metrics/closure counts and names the cases whose log does not replay instead of dropping
them without a word (the closure rate was silently computed over fewer cases than exist)."""

import logging
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from psycopg.types.json import Jsonb

from api_helpers import login_headers, make_client
from app.clock import clock
from app.config import settings as app_settings
from app.events import CASE_CREATED, Event, InMemoryEventStore, LoadedCase
from app.pathway import load_pathway
from app.routers import cases

PATHWAY = load_pathway(app_settings.pathway_path)
HAS_DB = bool(os.environ.get("DATABASE_URL"))
client = make_client()
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)


def coord():
    return login_headers(client, "coordinator", "mf coord")


class StoreWithBrokenCases(InMemoryEventStore):
    """An in-memory store that also holds `broken` cases, as load_cases reports a log that does not replay."""

    def __init__(self, broken):
        super().__init__()
        self.broken = broken

    def load_cases(self, pathway, state=None):
        out = super().load_cases(pathway, state)
        if state is None:
            out += [LoadedCase(cid, pathway.name, AT, [], None, error) for cid, error in self.broken]
        return out


def make_case(store, name):
    cid = uuid4()
    store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, AT, "system:intake", {"patient_ref": f"Patient/{name}"}),
                      PATHWAY.initial_state)
    return cid


def test_a_clean_store_reports_zero_failures(monkeypatch):
    store = InMemoryEventStore()
    monkeypatch.setattr(cases, "store", store)
    make_case(store, "a")
    body = client.get("/metrics/closure", headers=coord()).json()
    assert body["replay_failures"] == {"count": 0, "cases": []} and body["cases_total"] == 1


def test_cases_that_do_not_replay_are_counted_named_and_logged(monkeypatch, caplog):
    bad = [(uuid4(), "event 'close' not allowed from state 'result_received'"), (uuid4(), "unknown state")]
    store = StoreWithBrokenCases(bad)
    monkeypatch.setattr(cases, "store", store)
    make_case(store, "good-1")
    make_case(store, "good-2")
    with caplog.at_level(logging.ERROR, logger="app.metrics"):
        body = client.get("/metrics/closure", headers=coord()).json()
    assert body["cases_total"] == 2  # the measured ones
    assert body["replay_failures"]["count"] == 2
    assert {c["case_id"]: c["error"] for c in body["replay_failures"]["cases"]} == {str(cid): err for cid, err in bad}
    assert any("do not replay" in r.message for r in caplog.records)


def test_the_named_list_is_capped_but_the_count_is_exact(monkeypatch):
    from app.routers import metrics

    store = StoreWithBrokenCases([(uuid4(), "broken") for _ in range(metrics.MAX_LISTED_FAILURES + 7)])
    monkeypatch.setattr(cases, "store", store)
    body = client.get("/metrics/closure", headers=coord()).json()
    assert body["replay_failures"]["count"] == metrics.MAX_LISTED_FAILURES + 7
    assert len(body["replay_failures"]["cases"]) == metrics.MAX_LISTED_FAILURES


@pytest.mark.integration
@pytest.mark.skipif(not HAS_DB, reason="DATABASE_URL not set")
def test_postgres_a_log_that_no_longer_replays_is_reported(monkeypatch):
    from app.db import create_pool, run_migrations
    from app.store_pg import PostgresEventStore
    from scratchdb import scratch_database

    with scratch_database("metricsfail") as url:
        pool = create_pool(url, max_size=3)
        try:
            store = PostgresEventStore(pool)
            monkeypatch.setattr(cases, "store", store)
            good, bad = make_case(store, "pg-good"), make_case(store, "pg-bad")
            # a `close` straight after creation: written behind the machine's back (as an old rule set could have)
            with pool.connection() as conn:
                conn.execute(
                    "INSERT INTO events (event_id, case_id, seq, type, at, actor, payload) VALUES (%s, %s, 2, 'close', %s, %s, %s)",
                    (uuid4(), bad, AT, "primary_care_owner:x", Jsonb({"evidence_ref": "DiagnosticReport/1", "signed_by": "Practitioner/9"})))
            body = client.get("/metrics/closure", headers=coord()).json()
            assert body["cases_total"] == 1
            assert body["replay_failures"]["count"] == 1
            assert body["replay_failures"]["cases"][0]["case_id"] == str(bad)
            assert good != bad and body["replay_failures"]["cases"][0]["error"]
        finally:
            pool.close()
