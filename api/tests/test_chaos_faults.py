"""F3 chaos tests with the fault-injection harness (api/tests/faults.py): T-F3 duplicate delivery,
T-F4 clock skew, and a store failure between the event insert and its FHIR projection.

Run with `make test-chaos` (Postgres on the compose stack; every Postgres test gets its own scratch
database, api/tests/scratchdb.py, and nothing touches the dev database). The harness only reads
`FAULT_INJECT` when `APP_ENV=test`, so each test switches both on with monkeypatch and the first
tests prove that without `APP_ENV=test` nothing happens.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

import faults
from api_helpers import login_headers
from app import fhir_projection as fp
from app import notify, ops
from app.calendar import default_calendar
from app.clock import clock
from app.config import settings as app_settings
from app.db import create_pool
from app.events import InMemoryEventStore, replay_model
from app.main import app
from app.pathway import load_pathway
from app.routers import cases, scheduler
from app.store_pg import PostgresEventStore
from app.sweep import SCHEDULER_ACTOR, plan_case, run_sweep
from fhirtest import FakeFhirClient
from scratchdb import AT, scratch_database, seed_open_cases

pytestmark = pytest.mark.chaos
needs_pg = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set (make test-chaos sets it)")

PATHWAY = load_pathway(app_settings.pathway_path)
SCHED = {"Authorization": f"Bearer {app_settings.scheduler_service_token}"}


# ---------------------------------------------------------------- helpers


def log_hash(events) -> str:
    """sha256 over the whole log in sequence order: what a replay is built from."""
    rows = [(e.seq, str(e.event_id), e.type, e.at.isoformat(), e.actor, e.payload) for e in sorted(events, key=lambda e: e.seq)]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()


def model_hash(events) -> str:
    return hashlib.sha256(json.dumps(replay_model(events, PATHWAY).to_json(), sort_keys=True, default=str).encode()).hexdigest()


def faults_on(monkeypatch, spec: str) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("FAULT_INJECT", spec)


@contextmanager
def world(store):
    """The API over `store` (no lifespan), sim clock reset, in-memory side stores."""
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(cases, "store", store)
        mp.setattr(cases, "pathway", PATHWAY)
        mp.setattr(notify, "log", notify.InMemoryNotificationLog())
        mp.setattr(ops, "store", ops.InMemoryOpsStore())
        clock.reset()
        try:
            yield TestClient(app, raise_server_exceptions=False)
        finally:
            clock.reset()


def new_case(client) -> str:
    coord = login_headers(client, "coordinator", "chaos coordinator")
    resp = client.post("/cases", json={"patient_ref": "Patient/chaos-1"}, headers=coord)
    assert resp.status_code == 201, resp.text
    return resp.json()["case_id"]


LAB = None


def post(client, case_id, type_, role="lab", payload=None, event_id=None, at=None, name=None):
    body = {"type": type_, "payload": payload or {}}
    if event_id:
        body["event_id"] = str(event_id)
    if at:
        body["at"] = at.isoformat()
    return client.post(f"/cases/{case_id}/events", json=body, headers=login_headers(client, role, name or f"chaos {role}"))


def events_of(store, case_id):
    return store.events_for(UUID(str(case_id)))


# ---------------------------------------------------------------- the gate


def test_faults_are_ignored_unless_app_env_is_test(monkeypatch):
    monkeypatch.setenv("FAULT_INJECT", "duplicate_delivery=4,fail_after_insert=1,stale_clock=3600")
    for env in (None, "dev", "prod", "Test", ""):
        if env is None:
            monkeypatch.delenv("APP_ENV", raising=False)
        else:
            monkeypatch.setenv("APP_ENV", env)
        assert faults.active() == {}
        store = InMemoryEventStore()
        assert faults.wrap_store(store) is store
        assert faults.skew(AT) == AT
    monkeypatch.setenv("APP_ENV", "test")
    assert faults.active() == {"duplicate_delivery": 4, "fail_after_insert": 1, "stale_clock": 3600}
    assert isinstance(faults.wrap_store(InMemoryEventStore()), faults.FaultyStore)
    assert faults.skew(AT) == AT - timedelta(hours=1)


def test_unknown_fault_names_are_an_error_not_silently_ignored(monkeypatch):
    faults_on(monkeypatch, "duplicate_delivery=2,flood")
    with pytest.raises(ValueError, match="unknown fault"):
        faults.active()


# ---------------------------------------------------------------- T-F3: duplicate delivery


def test_t_f3_a_duplicated_delivery_has_the_effect_of_one_in_memory(monkeypatch):
    faults_on(monkeypatch, "duplicate_delivery=12")
    inner = InMemoryEventStore()
    store = faults.wrap_store(inner)
    with world(store) as client:
        cid = new_case(client)
        key = uuid4()
        resp = post(client, cid, "acknowledge", "lab", event_id=key)
        assert resp.status_code == 201, resp.text
        events = events_of(inner, cid)
        assert [e.type for e in events] == ["case_created", "acknowledge"] and [e.seq for e in events] == [1, 2]
        assert resp.json()["state"] == "acknowledged"
        assert store.delivered >= 12  # the fault really delivered the event many times
        assert all(kind == "ok" for kind, _ in store.outcomes)


@needs_pg
def test_t_f3_duplicated_delivery_through_a_walk_of_the_pathway_postgres(monkeypatch):
    """Every write of a longer walk is delivered 16 times at once; the log equals the undisturbed walk."""
    def walk(store):
        with world(store) as client:
            cid = UUID(new_case(client))
            ids = [uuid5_for(cid, i) for i in range(4)]
            steps = [("acknowledge", "lab", {}), ("accept_ownership", "primary_care_owner", {}),
                     ("contact_patient", "primary_care_owner", {}), ("patient_acknowledge", "patient", {})]
            for (type_, role, payload), key in zip(steps, ids):
                if role == "patient":
                    resp = client.post(f"/cases/{cid}/events", json={"type": type_, "payload": payload, "event_id": str(key)},
                                       headers=login_headers(client, "patient", "chaos patient", patient_ref="Patient/chaos-1"))
                else:
                    resp = post(client, cid, type_, role, payload, event_id=key)
                assert resp.status_code == 201, (type_, resp.text)
            return cid

    from uuid import uuid5, NAMESPACE_URL

    def uuid5_for(cid, i):
        return uuid5(NAMESPACE_URL, f"{cid}/{i}")

    with scratch_database("chaosdup") as url:
        pool = create_pool(url, max_size=24)
        try:
            inner = PostgresEventStore(pool)
            faults_on(monkeypatch, "duplicate_delivery=16")
            store = faults.wrap_store(inner)
            cid = walk(store)
            events = inner.events_for(cid)
            assert [e.type for e in events] == ["case_created", "acknowledge", "accept_ownership", "contact_patient", "patient_acknowledge"]
            assert [e.seq for e in events] == [1, 2, 3, 4, 5]
            assert len({e.event_id for e in events}) == 5
            assert store.delivered >= 16 * 4
            assert inner.current_state(cid) == "patient_acknowledged"
        finally:
            pool.close()


@needs_pg
def test_t_f3_the_same_event_posted_by_many_clients_at_once_is_one_row_postgres():
    with scratch_database("chaosconc") as url:
        pool = create_pool(url, max_size=32)
        try:
            store = PostgresEventStore(pool)
            with world(store) as client:
                cid = new_case(client)
                headers = login_headers(client, "lab", "chaos lab")
                key = uuid4()
                barrier = threading.Barrier(24)
                codes: list[int] = []

                def go():
                    barrier.wait()
                    r = client.post(f"/cases/{cid}/events", json={"type": "acknowledge", "payload": {}, "event_id": str(key)},
                                    headers=headers)
                    codes.append(r.status_code)

                threads = [threading.Thread(target=go) for _ in range(24)]
                [t.start() for t in threads]
                [t.join() for t in threads]
                assert set(codes) == {201}, codes  # idempotent: every retry is told it worked
                with pool.connection() as conn:
                    rows = conn.execute("SELECT count(*) FROM events WHERE event_id = %s", (key,)).fetchone()[0]
                    ack = conn.execute("SELECT count(*) FROM events WHERE case_id = %s AND type = 'acknowledge'", (UUID(cid),)).fetchone()[0]
                assert rows == 1 and ack == 1
                assert [e.seq for e in events_of(store, cid)] == [1, 2]
                # a late duplicate, long after the state has moved on, is still a no-op
                assert post(client, cid, "accept_ownership", "primary_care_owner").status_code == 201
                before = log_hash(events_of(store, cid))
                assert post(client, cid, "acknowledge", "lab", event_id=key).status_code == 201
                assert log_hash(events_of(store, cid)) == before
                # the same id on another case is refused, not merged
                other = new_case(client)
                assert post(client, other, "acknowledge", "lab", event_id=key).status_code == 409
        finally:
            pool.close()


@needs_pg
def test_t_f3_replay_of_a_duplicated_log_equals_replay_of_a_clean_one(monkeypatch):
    """Same walk with and without duplicate delivery: same events, same replayed model."""
    def run(spec: str | None):
        with scratch_database("chaosreplay") as url:
            pool = create_pool(url, max_size=24)
            try:
                inner = PostgresEventStore(pool)
                if spec:
                    faults_on(monkeypatch, spec)
                store = faults.wrap_store(inner)
                with world(store) as client:
                    cid = uuid4()
                    assert client.post("/cases", json={"case_id": str(cid), "patient_ref": "Patient/chaos-1"},
                                       headers=login_headers(client, "coordinator", "chaos coordinator")).status_code == 201
                    for i, (type_, role) in enumerate([("acknowledge", "lab"), ("accept_ownership", "primary_care_owner"),
                                                         ("contact_patient", "primary_care_owner")]):
                        assert post(client, cid, type_, role, event_id=UUID(int=i + 1)).status_code == 201
                events = inner.events_for(cid)
                # genesis time and ids differ between the runs only through uuid4 ids the route generates
                return [(e.seq, e.type, e.actor) for e in events], model_hash(events)
            finally:
                pool.close()

    clean = run(None)
    monkeypatch.delenv("FAULT_INJECT", raising=False)
    duplicated = run("duplicate_delivery=10")
    assert clean[0] == duplicated[0]
    assert clean[1] == duplicated[1]


# ---------------------------------------------------------------- T-F4: clock skew


def test_t_f4_an_event_dated_after_the_sim_clock_is_refused_and_changes_nothing():
    inner = InMemoryEventStore()
    with world(inner) as client:
        cid = new_case(client)
        assert post(client, cid, "acknowledge", "lab").status_code == 201
        before_log, before_model = log_hash(events_of(inner, cid)), model_hash(events_of(inner, cid))
        for ahead in (timedelta(seconds=1), timedelta(days=1), timedelta(days=36500)):
            resp = post(client, cid, "accept_ownership", "primary_care_owner", at=clock.now() + ahead)
            assert resp.status_code == 422 and "later than the simulation clock" in resp.json()["detail"], resp.text
        assert log_hash(events_of(inner, cid)) == before_log and model_hash(events_of(inner, cid)) == before_model


def test_t_f4_a_service_actors_late_event_is_accepted_flagged_and_replays_deterministically():
    """ADR-027 (amended by SEC-2): a user's backdated event is refused (422, nothing written). A service actor
    that legitimately posts late (the scheduler read `now` before a user's later event) is accepted but flagged:
    the timeline gets a `backdated` item and the read model counts it. The log is ordered by `seq`, never by `at`,
    so insertion order is the only order and the replay is a pure function of it."""
    inner = InMemoryEventStore()
    with world(inner) as client:
        cid = new_case(client)
        clock.advance(days=3)
        assert post(client, cid, "acknowledge", "lab").status_code == 201
        before = replay_model(events_of(inner, cid), PATHWAY)
        assert before.backdated_events == 0
        user = post(client, cid, "accept_ownership", "primary_care_owner", at=clock.now() - timedelta(days=2))
        assert user.status_code == 422 and "backdated" in user.json()["detail"]
        assert len(events_of(inner, cid)) == 2  # nothing written
        # the breach the scheduler found was due before a user's later annotation
        due = default_calendar().add_working_days(before.clock_started_at, 2)
        clock.advance(days=7)
        assert post(client, cid, "callback_requested", "coordinator").status_code == 201
        late = due + timedelta(hours=1)
        resp = client.post(f"/cases/{cid}/events", headers=SCHED, json={
            "type": "mark_overdue", "at": late.isoformat(),
            "payload": {"state": "acknowledged", "due_at": due.isoformat()}})
        events = events_of(inner, cid)
        assert resp.status_code == 201, resp.text
        assert [e.seq for e in events] == [1, 2, 3, 4]
        assert events[3].at < events[2].at  # accepted out of time order, kept in insertion order
        # flagged: counted in the read model (the API's and a fresh replay's) ...
        assert resp.json()["read_model"]["backdated_events"] == 1
        assert replay_model(events, PATHWAY).backdated_events == 1
        # ... and shown on the timeline, exactly once
        tl = client.get(f"/cases/{cid}/timeline", headers=login_headers(client, "coordinator", "chaos coordinator")).json()
        flagged = [i for i in tl["items"] if i["kind"] == "backdated"]
        assert [i["event_id"] for i in flagged] == [str(events[3].event_id)]
        # replay is a pure function of the log in sequence order: repeated replays and the store's own model agree
        assert model_hash(events) == model_hash(events_of(inner, cid))
        assert replay_model(events, PATHWAY).to_json() == inner.read_model(UUID(cid), PATHWAY).to_json()


@needs_pg
def test_t_f4_a_scheduler_with_a_stale_clock_emits_nothing_new_and_never_dates_before_the_log(monkeypatch):
    now = AT + timedelta(days=21)
    with scratch_database("chaosskew") as url:
        pool = create_pool(url, max_size=8)
        try:
            store = PostgresEventStore(pool)
            ids = seed_open_cases(store, PATHWAY, 120, salt="skew")
            cal = default_calendar()

            def scheduler_events():
                with pool.connection() as conn:
                    return conn.execute("SELECT event_id, case_id, seq, type, at FROM events WHERE actor = %s ORDER BY case_id, seq",
                                        (SCHEDULER_ACTOR,)).fetchall()

            faults_on(monkeypatch, "stale_clock=1209600")  # a clock two weeks behind: 7 Jan, just past the first SLA
            stale = faults.skew(now)
            assert stale < now
            early = run_sweep(store, PATHWAY, cal, stale)
            first = scheduler_events()
            # whatever was due at the stale time is emitted; nothing is dated before the case's own log
            assert early.errors == [] and early.rejected == 0
            assert early.mark_overdue > 0, "the stale sweep did nothing, so this test would prove nothing"
            assert len(first) == early.mark_overdue + early.escalate
            for case_id in ids:
                evs = store.events_for(case_id)
                assert [e.at for e in evs] == sorted(e.at for e in evs)
            # the correct clock arrives: the rest is emitted, nothing is emitted twice
            late = run_sweep(store, PATHWAY, cal, now)
            assert late.errors == [] and late.rejected == 0
            assert late.mark_overdue + late.escalate > 0 and late.mark_overdue == len(ids) - early.mark_overdue
            both = scheduler_events()
            assert len({r[0] for r in both}) == len(both)
            assert set(first) <= set(both)
            # the same sweeps in the right order give the same event ids and payload types
            with scratch_database("chaosskew2") as url2:
                pool2 = create_pool(url2, max_size=8)
                try:
                    store2 = PostgresEventStore(pool2)
                    seed_open_cases(store2, PATHWAY, 120, salt="skew")
                    run_sweep(store2, PATHWAY, cal, now)
                    with pool2.connection() as conn:
                        ref = conn.execute("SELECT event_id FROM events WHERE actor = %s", (SCHEDULER_ACTOR,)).fetchall()
                    assert {r[0] for r in ref} == {r[0] for r in both}
                finally:
                    pool2.close()
            # a clock that goes backwards after all that (reset, or a stale replica) changes nothing
            hashes = {c: log_hash(store.events_for(c)) for c in ids}
            back = run_sweep(store, PATHWAY, cal, AT)
            assert back.mark_overdue == back.escalate == back.handoff_bounced == 0
            assert {c: log_hash(store.events_for(c)) for c in ids} == hashes
            # the pure planner agrees: from a stale `now` it plans nothing for a case already past those rungs
            for case_id, model, _ in store.iter_open_case_models(PATHWAY, only=ids[:10]):
                assert plan_case(case_id, model, PATHWAY, cal, AT) == []
        finally:
            pool.close()


@needs_pg
def test_t_f4_a_fast_scheduler_clock_cannot_double_up_when_the_right_time_arrives(monkeypatch):
    """Event ids come from (case, state, due date, rung), not from the clock, so a sweep run with a
    clock that is ahead and the same sweep later at the real time write each event once."""
    real = AT + timedelta(days=10)
    with scratch_database("chaosfast") as url:
        pool = create_pool(url, max_size=8)
        try:
            store = PostgresEventStore(pool)
            seed_open_cases(store, PATHWAY, 60, salt="fast")
            cal = default_calendar()
            faults_on(monkeypatch, "clock_ahead=864000")
            fast = faults.skew(real)
            assert fast == real + timedelta(days=10)
            ahead = run_sweep(store, PATHWAY, cal, fast)
            assert ahead.errors == [] and ahead.mark_overdue == 60
            with pool.connection() as conn:
                before = conn.execute("SELECT count(*) FROM events WHERE actor = %s", (SCHEDULER_ACTOR,)).fetchone()[0]
            again = run_sweep(store, PATHWAY, cal, real)
            with pool.connection() as conn:
                after = conn.execute("SELECT count(*) FROM events WHERE actor = %s", (SCHEDULER_ACTOR,)).fetchone()[0]
                dup = conn.execute("SELECT count(*) FROM (SELECT event_id FROM events GROUP BY 1 HAVING count(*) > 1) t").fetchone()[0]
            assert again.mark_overdue == 0 and after == before and dup == 0
        finally:
            pool.close()


def test_t_f4_the_scheduler_route_sweeps_at_the_api_clock_not_at_a_time_the_caller_supplies():
    """A scheduler cannot hand the API its own (possibly skewed) clock: the route takes no time, and an
    extra body field changes nothing."""
    inner = InMemoryEventStore()
    with world(inner) as client:
        clock.advance(days=3)
        resp = client.post("/scheduler/sweep", headers=SCHED, json={"now": "2099-01-01T00:00:00Z"})
        assert resp.status_code == 200
        assert resp.json()["sim_time"] == clock.now().isoformat()


# ---------------------------------------------------------------- store failure after the insert


def projecting_stack(monkeypatch, inner, spec):
    """ProjectingStore(FaultyStore(inner)): the fault fires after the insert commits and before the projector runs."""
    faults_on(monkeypatch, spec)
    fake = FakeFhirClient(existing={"Patient/chaos-1"})
    queue = fp.InMemoryProjectionQueue()
    faulty = faults.wrap_store(inner)
    projector = fp.Projector(fake, faulty, PATHWAY, default_calendar(), queue)
    return fp.ProjectingStore(faulty, projector), fake, queue, faulty


def test_t_f4_a_store_failure_after_the_insert_leaves_one_event_and_a_retry_heals_the_projection(monkeypatch):
    inner = InMemoryEventStore()
    store, fake, queue, faulty = projecting_stack(monkeypatch, inner, "fail_after_insert=2")  # append #1 acknowledge? create_case is not an append
    with world(store) as client:
        cid = new_case(client)
        assert post(client, cid, "acknowledge", "lab").status_code == 201  # append #1
        projected_before = set(fake.resources)
        key = uuid4()
        resp = post(client, cid, "accept_ownership", "primary_care_owner", event_id=key)  # append #2: commits, then the store fails
        assert resp.status_code == 500
        assert faulty.injected == 1
        events = events_of(inner, cid)
        assert [e.type for e in events].count("accept_ownership") == 1  # the event is in the log, once
        assert f"Provenance/prov-{key}" not in fake.resources  # but it was never projected ...
        assert queue.count() == 1  # ... but it is queued (F3-1), and no client retry is needed for the projection
        assert set(fake.resources) == projected_before
        # the client retries with the same event id: no second event, and the projection catches up
        retry = post(client, cid, "accept_ownership", "primary_care_owner", event_id=key)
        assert retry.status_code == 201, retry.text
        events = events_of(inner, cid)
        assert [e.type for e in events].count("accept_ownership") == 1 and [e.seq for e in events] == [1, 2, 3]
        assert f"Provenance/prov-{key}" in fake.resources


def test_t_f4_a_committed_event_whose_store_call_failed_is_queued_for_projection_without_a_retry(monkeypatch):
    """F3-1 (fixed): the event committed, the store raised before ProjectingStore.project() ran. The retry row
    is queued without any client retry, and draining the queue projects the event."""
    inner = InMemoryEventStore()
    store, fake, queue, _ = projecting_stack(monkeypatch, inner, "fail_after_insert=1")
    with world(store) as client:
        cid = new_case(client)
        key = uuid4()
        assert post(client, cid, "acknowledge", "lab", event_id=key).status_code == 500
        assert len(events_of(inner, cid)) == 2
        assert queue.count() == 1
        assert f"Provenance/prov-{key}" not in fake.resources
        out = store.projector.drain()
        assert out["projected"] == 1 and out["still_queued"] == 0
        assert f"Provenance/prov-{key}" in fake.resources


def test_t_f4_a_rejected_append_queues_nothing(monkeypatch):
    """Only an event that is in the log is queued: a refused transition (409) leaves no retry row."""
    inner = InMemoryEventStore()
    store, fake, queue, _ = projecting_stack(monkeypatch, inner, "fail_after_insert=99")
    with world(store) as client:
        cid = new_case(client)
        assert post(client, cid, "accept_ownership", "primary_care_owner").status_code == 409  # not acknowledged yet
        assert queue.count() == 0 and len(events_of(inner, cid)) == 1


def test_t_f4_when_the_log_cannot_be_read_after_a_failed_append_the_event_is_queued_anyway(monkeypatch):
    """The connection died: the answer to the commit never came and the log cannot be read to tell. Queue
    speculatively; a drain that finds the event absent drops the row, one that finds it projects it."""
    inner = InMemoryEventStore()
    store, fake, queue, faulty = projecting_stack(monkeypatch, inner, "fail_after_insert=1")
    with world(store) as client:
        cid = new_case(client)
        key = uuid4()
        real = faulty.events_for

        def flaky(case_id):
            if faulty.injected:  # the check right after the injected failure
                raise RuntimeError("log unreadable")
            return real(case_id)

        faulty.events_for = flaky  # type: ignore[method-assign]
        assert post(client, cid, "acknowledge", "lab", event_id=key).status_code == 500
        faulty.events_for = real  # type: ignore[method-assign]
        assert queue.count() == 1
        assert store.projector.drain()["still_queued"] == 0
        assert f"Provenance/prov-{key}" in fake.resources


def test_t_f4_a_store_failure_before_the_insert_writes_nothing(monkeypatch):
    inner = InMemoryEventStore()
    store, fake, queue, faulty = projecting_stack(monkeypatch, inner, "fail_before_insert=1")
    with world(store) as client:
        cid = new_case(client)
        key = uuid4()
        before = log_hash(events_of(inner, cid))
        assert post(client, cid, "acknowledge", "lab", event_id=key).status_code == 500
        assert log_hash(events_of(inner, cid)) == before and queue.count() == 0
        assert post(client, cid, "acknowledge", "lab", event_id=key).status_code == 201  # the retry is the first delivery
        assert [e.type for e in events_of(inner, cid)] == ["case_created", "acknowledge"]


@needs_pg
def test_t_f4_the_same_store_failure_against_postgres(monkeypatch):
    with scratch_database("chaosfail") as url:
        pool = create_pool(url, max_size=8)
        try:
            inner = PostgresEventStore(pool)
            store, fake, queue, faulty = projecting_stack(monkeypatch, inner, "fail_after_insert=1")
            with world(store) as client:
                cid = new_case(client)
                key = uuid4()
                assert post(client, cid, "acknowledge", "lab", event_id=key).status_code == 500
                with pool.connection() as conn:
                    n = conn.execute("SELECT count(*) FROM events WHERE event_id = %s", (key,)).fetchone()[0]
                assert n == 1
                assert queue.count() == 1  # F3-1: queued for projection without any client retry
                assert post(client, cid, "acknowledge", "lab", event_id=key).status_code == 201
                with pool.connection() as conn:
                    n = conn.execute("SELECT count(*) FROM events WHERE event_id = %s", (key,)).fetchone()[0]
                assert n == 1 and f"Provenance/prov-{key}" in fake.resources
        finally:
            pool.close()
