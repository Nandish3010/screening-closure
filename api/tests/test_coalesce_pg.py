"""A19, ADR-031 against Postgres (`make test-db`): coalesced and attached escalations through the real store,
concurrent sweeps, the stored read model against a fresh replay, and the JSON round trip of `also_for`.
Sweeps are restricted to the test's own cases (`only=`)."""

import os
import threading
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.calendar import WorkingCalendar
from app.config import settings as app_settings
from app.db import create_pool, run_migrations
from app.events import CASE_CREATED, Event, replay_model
from app.pathway import load_pathway
from app.risk.features import summarise_events
from app.store_pg import PostgresEventStore
from app.sweep import run_sweep, sweep_event_id

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

PATHWAY = load_pathway(app_settings.pathway_path)
UAE = WorkingCalendar()
AT = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # Monday; the referral milestone is due Mon 26 Jan 08:00Z
PCO = "primary_care_owner:dr_k"
REFERRAL = "referral_requested"


def ts(day, hour=8):
    return datetime(2026, 1, day, hour, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def pool():
    p = create_pool(os.environ["DATABASE_URL"], max_size=12)
    run_migrations(p)
    yield p
    p.close()


@pytest.fixture(scope="module")
def store(pool):
    return PostgresEventStore(pool)


def new_case(store, ack_at):
    """A case in `patient_acknowledged` since `ack_at`: its book_confirmatory SLA ends 5 working days later."""
    cid = uuid4()
    store.create_case(cid, PATHWAY.name, Event(cid, CASE_CREATED, AT, "system:intake"), PATHWAY.initial_state)
    for kind, actor, when in (
        ("acknowledge", "lab:l", AT + timedelta(days=1)),
        ("accept_ownership", PCO, AT + timedelta(days=2)),
        ("contact_patient", PCO, AT + timedelta(days=3)),
        ("patient_acknowledge", "patient:p", ack_at),
    ):
        store.append(Event(cid, kind, when, actor, {}), PATHWAY)
    return cid


def escalations(store, cid):
    return [e for e in store.events_for(cid) if e.type == "escalate"]


def test_one_sweep_makes_one_escalation_for_both_ladders_and_one_acceptance_accepts_both(store):
    cid = new_case(store, ts(19))  # the state SLA and the milestone both end Mon 26 Jan 08:00Z
    result = run_sweep(store, PATHWAY, UAE, ts(26, 9), only=[cid])
    assert result.errors == [] and result.rejected == 0 and result.mark_overdue == 2 and result.escalate == 1
    [esc] = escalations(store, cid)
    assert [x["milestone"] for x in esc.payload["also_for"]] == [REFERRAL] and "attached_to" not in esc.payload
    m = store.read_model(cid, PATHWAY)
    assert m.escalation_event_id == m.milestones[REFERRAL].escalation_event_id == esc.event_id
    assert m == replay_model(store.events_for(cid), PATHWAY)  # the stored model equals a fresh replay
    store.append(Event(cid, "escalation_accepted", ts(26, 10), PCO, {}), PATHWAY)
    m = store.read_model(cid, PATHWAY)
    assert m.escalation_accepted and m.milestones[REFERRAL].escalation_accepted
    again = run_sweep(store, PATHWAY, UAE, ts(26) + timedelta(days=60), only=[cid])
    assert again.escalate == 0 and len(escalations(store, cid)) == 1


def test_a_later_sweep_the_same_working_day_attaches_and_a_rerun_adds_nothing(store):
    cid = new_case(store, ts(19, 14))  # the state SLA ends at 14:00Z, six hours after the milestone's
    run_sweep(store, PATHWAY, UAE, ts(26, 10), only=[cid])
    [first] = escalations(store, cid)
    result = run_sweep(store, PATHWAY, UAE, ts(26, 15), only=[cid])
    assert result.errors == [] and result.rejected == 0 and result.escalate == 1
    attached = escalations(store, cid)[-1]
    m = store.read_model(cid, PATHWAY)
    assert attached.payload["attached_to"] == str(first.event_id) and attached.payload["state"] == "patient_acknowledged"
    assert attached.event_id == sweep_event_id(cid, m.clock_started_event_id, "escalate:reminder")
    assert m.escalation_event_id == first.event_id and m == replay_model(store.events_for(cid), PATHWAY)
    assert summarise_events(store.events_for(cid)).prior_escalations == 1
    before = len(store.events_for(cid))
    again = run_sweep(store, PATHWAY, UAE, ts(26, 15), only=[cid])
    assert again.mark_overdue == again.escalate == 0 and len(store.events_for(cid)) == before


def test_breaches_on_different_working_days_are_two_escalations(store):
    cid = new_case(store, ts(20, 14))  # the state SLA ends Tue 27 Jan 14:00Z
    run_sweep(store, PATHWAY, UAE, ts(26, 10), only=[cid])
    run_sweep(store, PATHWAY, UAE, ts(27, 15), only=[cid])
    owner = [e for e in escalations(store, cid) if e.payload["target"] == "owner"]
    assert len(owner) == 2 and not any(e.payload.get("attached_to") or e.payload.get("also_for") for e in owner)


def test_concurrent_sweeps_write_the_coalesced_and_the_attached_escalation_once(store):
    cid = new_case(store, ts(19, 14))
    run_sweep(store, PATHWAY, UAE, ts(26, 10), only=[cid])
    barrier = threading.Barrier(8)
    errors = []

    def worker():
        barrier.wait()
        try:
            run_sweep(store, PATHWAY, UAE, ts(26, 15), only=[cid])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    escs = escalations(store, cid)
    assert [bool(e.payload.get("attached_to")) for e in escs] == [False, True]
    seqs = [e.seq for e in store.events_for(cid)]
    assert seqs == list(range(1, len(seqs) + 1))  # no gaps, no duplicates
    assert store.read_model(cid, PATHWAY) == replay_model(store.events_for(cid), PATHWAY)


def test_the_outage_catch_up_is_stored_and_replayed_in_order(store):
    cid = new_case(store, ts(19))
    result = run_sweep(store, PATHWAY, UAE, ts(26) + timedelta(days=60), only=[cid])
    assert result.errors == [] and result.rejected == 0
    escs = escalations(store, cid)
    assert [e.payload["rung"] for e in escs] == ["reminder", "supervisor", "coordinator"]
    assert [x["level"] for e in escs for x in e.payload["also_for"]] == [1, 2, 3]
    m = store.read_model(cid, PATHWAY)
    assert m.escalation_level == m.milestones[REFERRAL].escalation_level == 3
    assert UUID(str(escs[-1].event_id)) == m.escalation_event_id
