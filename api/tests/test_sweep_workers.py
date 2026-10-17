"""A17 without a database: SWEEP_WORKERS parsing and the in-memory store keeping the single-process sweep."""

import os

import pytest

from api_helpers import make_client
from app.routers import scheduler
from app.sweep import SweepPool, default_sweep_workers, sweep_workers_from_env

def test_default_is_min_4_and_cpus(monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 16)
    assert default_sweep_workers() == 4
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    assert default_sweep_workers() == 2
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert default_sweep_workers() == 1


@pytest.mark.parametrize("raw,want", [("1", 1), ("3", 3), (" 6 ", 6)])
def test_explicit_workers(raw, want):
    assert sweep_workers_from_env({"SWEEP_WORKERS": raw}) == want


@pytest.mark.parametrize("raw", ["", "0", "-2", "four", "2.5"])
def test_unusable_values_fall_back_to_the_default(raw):
    assert sweep_workers_from_env({"SWEEP_WORKERS": raw}) == default_sweep_workers()
    assert sweep_workers_from_env({}) == default_sweep_workers()


def test_pool_rejects_zero_workers():
    with pytest.raises(ValueError):
        SweepPool(0)


def test_in_memory_store_sweeps_in_process_even_when_workers_are_configured(monkeypatch):
    from app.config import settings as app_settings

    monkeypatch.setenv("SWEEP_WORKERS", "4")
    scheduler.reset_pool()
    try:
        client = make_client()
        out = client.post("/scheduler/sweep", headers={"Authorization": f"Bearer {app_settings.scheduler_service_token}"})
        assert out.status_code == 200
        assert out.json()["workers"] == 1 and out.json()["errors"] == []
        assert scheduler.get_pool().workers == 4 and not scheduler.get_pool().started
    finally:
        scheduler.reset_pool()


def test_events_appended_by_workers_are_handed_to_the_fhir_projector(monkeypatch):
    """Workers write through plain stores, so the route queues what they appended for projection."""
    import time
    from datetime import UTC, datetime
    from uuid import uuid4

    from app import fhir_projection as fp
    from app.calendar import default_calendar
    from app.config import settings as app_settings
    from app.events import CASE_CREATED, Event, InMemoryEventStore
    from app.pathway import load_pathway
    from fhirtest import FakeFhirClient

    pathway = load_pathway(app_settings.pathway_path)
    inner = InMemoryEventStore()
    fake = FakeFhirClient(existing=set())
    queue = fp.InMemoryProjectionQueue()
    store = fp.ProjectingStore(inner, fp.Projector(fake, inner, pathway, default_calendar(), queue))
    case_id = uuid4()
    genesis = Event(case_id, CASE_CREATED, datetime(2026, 1, 5, 8, tzinfo=UTC), "system:intake", {})
    inner.create_case(case_id, pathway.name, genesis, pathway.initial_state)  # as a worker would: no projection
    assert not fake.resources

    scheduler._project_emitted(store, [(str(genesis.event_id), str(case_id))])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and queue.count():
        time.sleep(0.05)
    assert queue.count() == 0 and fake.resources, "the event was not projected"
    scheduler._project_emitted(inner, [(str(genesis.event_id), str(case_id))])  # no projector: a no-op
