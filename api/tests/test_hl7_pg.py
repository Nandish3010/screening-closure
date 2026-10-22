"""A28 (ADR-039), integration: the HL7 adapter against Postgres, in a scratch `_test` database of its own (it cannot clash
with another test session on closure_test). A positive FIT creates a case, a booking is an event by `system:hl7`, both
survive a replay from the log, and concurrent redelivery of one message makes one case. Run with `make test-db`."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from api_helpers import login_headers
from app import confirmatory
from app.config import settings
from app.db import create_pool
from app.events import replay_model
from app.hl7 import contacts
from app.hl7 import parser as p
from app.main import app, pathway
from app.routers import cases
from app.store_pg import PostgresEventStore
from scratchdb import scratch_database
from test_hl7_handler import adt, colonoscopy, fit, siu

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]


@pytest.fixture(scope="module")
def scratch_store():
    with scratch_database("hl7") as url:
        pool = create_pool(url, max_size=8)
        try:
            yield PostgresEventStore(pool)
        finally:
            pool.close()


@pytest.fixture(autouse=True)
def world(scratch_store, monkeypatch):
    monkeypatch.setattr(cases, "store", scratch_store)
    monkeypatch.setattr(cases, "pathway", pathway)
    recorder = confirmatory.StaticRecorder()
    monkeypatch.setattr(confirmatory, "recorder", recorder)
    contacts.reset()
    return recorder


def send(client, text):
    resp = client.post("/hl7", content=text.encode(), headers={"Authorization": f"Bearer {settings.hl7_service_token}"})
    assert resp.status_code == 200, resp.text
    return p.parse(resp.text).first("MSA").get(1)


def test_an_oru_creates_a_case_a_siu_books_it_and_the_log_replays(scratch_store, world):
    client = TestClient(app)
    patient = "pg-9001"
    pid = f"PID|1||{patient}^^^SCREENING-CLOSURE^MR||SYNTHETIC^{patient}||||||||^PRN^CP^^971^50^0000000"
    assert send(client, fit("PG-F1", pid_line=pid)) == "AA"
    (lc,) = [c for c in scratch_store.load_cases(pathway) if c.patient_ref == f"Patient/{patient}"]
    assert lc.events[0].actor == "system:intake" and lc.model.state == "result_received"

    for etype, role, payload in [("acknowledge", "lab", {}), ("accept_ownership", "primary_care_owner", {}),
                                 ("contact_patient", "primary_care_owner", {"channel": "sms"}), ("patient_acknowledge", "coordinator", {})]:
        r = client.post(f"/cases/{lc.case_id}/events", json={"type": etype, "payload": payload}, headers=login_headers(client, role))
        assert r.status_code == 201, r.text
    booking = siu("S12", "PG-S1", patient=patient)
    assert send(client, booking) == "AA" and send(client, booking) == "AA"          # the second is a duplicate
    events = scratch_store.events_for(lc.case_id)
    booked = [e for e in events if e.type == "book_confirmatory"]
    assert len(booked) == 1 and booked[0].actor == "system:hl7"
    assert replay_model(events, pathway).state == "confirmatory_booked" == scratch_store.current_state(lc.case_id)

    assert send(client, colonoscopy("PG-C1", "incomplete", patient=patient)) == "AA"
    assert world.completeness == ["incomplete"] and len(scratch_store.events_for(lc.case_id)) == len(events)
    assert send(client, siu("S15", "PG-S2", patient=patient)) == "AE"
    assert send(client, adt("A08", "PG-A1", patient=patient)) == "AA"


def test_concurrent_redelivery_of_one_message_makes_one_case(scratch_store):
    client = TestClient(app)
    text = fit("PG-F2", pid_line="PID|1||pg-9002^^^SCREENING-CLOSURE^MR||X")
    with ThreadPoolExecutor(6) as ex:
        codes = list(ex.map(lambda _: send(client, text), range(6)))
    assert codes == ["AA"] * 6
    assert len([c for c in scratch_store.load_cases(pathway) if c.patient_ref == "Patient/pg-9002"]) == 1
