"""A20 against Postgres (`make test-db`): the sweep index `cases.next_due_at` (migration 012) and the batched write
path `append_batches`.

* the stored index is derived: after every kind of write it equals `app.sweep.next_due_at` of the replayed log, and
  `events` stays the only source of truth;
* a sweep that reads only the cases whose index has passed writes exactly the events (ids and payloads) of a sweep
  that reads every case, over several sweep times with human events between them;
* a missing, stale (other timing fingerprint) or deleted index only costs time: such cases are evaluated, and
  `full_scan=True` ignores the index altogether;
* `append_batches` keeps `append_many`'s per-case semantics: a case that is refused (content conflict, invalid
  transition, unknown case) loses nothing for the others, the lock order cannot deadlock overlapping batches, and a
  retry is idempotent.
"""

import os
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4, uuid5

import pytest

from app.calendar import default_calendar
from app.config import settings as app_settings
from app.db import create_pool
from app.events import (
    CASE_CREATED,
    CaseNotFound,
    Event,
    EventContentConflict,
    EventIdConflict,
)
from app.pathway import load_pathway
from app.state_machine import InvalidTransition
from app.store_pg import PostgresEventStore
from app.sweep import NEVER, handoff_due_at, SCHEDULER_ACTOR, next_due_at, plan_case, run_sweep, timing_fingerprint
from scratchdb import AT, scratch_database, seed_open_cases

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

PATHWAY = load_pathway(app_settings.pathway_path)
CAL = default_calendar()
OWNER = "clinician:dr_a"
OWNER_PC = "primary_care_owner:dr_k"
SIGNED = {"evidence_ref": "DiagnosticReport/1", "signed_by": "Practitioner/9"}
CASES = 48


@pytest.fixture
def env():
    with scratch_database("a20") as url:
        pool = create_pool(url, max_size=6)
        store = PostgresEventStore(pool)
        ids = seed_open_cases(store, PATHWAY, CASES)
        try:
            yield store, pool, ids
        finally:
            pool.close()


def index_rows(pool):
    with pool.connection() as conn:
        return {
            r[0]: (r[1], r[2], r[3])
            for r in conn.execute("SELECT id, next_due_at, next_due_basis, current_state FROM cases").fetchall()
        }


def scheduler_events(pool):
    with pool.connection() as conn:
        return conn.execute(
            "SELECT event_id, case_id, seq, type, payload FROM events WHERE actor = %s ORDER BY case_id, seq",
            (SCHEDULER_ACTOR,),
        ).fetchall()


def assert_index_is_the_function_of_the_log(store, pool):
    """Every case holding an index value holds exactly what a replay of its log computes (derived, not authoritative)."""
    basis = timing_fingerprint(PATHWAY, CAL)
    rows = index_rows(pool)
    checked = 0
    for case_id, model, err in store.iter_open_case_models(PATHWAY):
        assert err is None
        due, stored_basis, _ = rows[case_id]
        if due is None:
            continue  # not computed: always evaluated
        want = next_due_at(model, PATHWAY, CAL)
        assert due == (NEVER if want is None else want), f"{case_id}: stored {due}, replay gives {want}"
        assert stored_basis == basis
        checked += 1
    return checked


def ack(case_id, at):
    # a deterministic id: sweep event ids derive from the event that started the clock, and two databases are compared
    return Event(case_id, "acknowledge", at, "lab:l", {}, uuid5(case_id, f"ack|{at.isoformat()}"))


# ---- the index is maintained by every write path ---------------------------------------------------------


def test_a_new_case_has_no_index_and_is_evaluated_then_indexed(env):
    store, pool, ids = env
    assert all(v[0] is None for v in index_rows(pool).values())  # create_case does not compute it
    now = AT + timedelta(days=1)  # before every SLA: nothing to emit
    first = run_sweep(store, PATHWAY, CAL, now)
    assert first.cases_evaluated == CASES and first.cases_skipped == 0 and first.mark_overdue == 0
    assert assert_index_is_the_function_of_the_log(store, pool) == CASES  # the sweep wrote it back
    second = run_sweep(store, PATHWAY, CAL, now)
    assert second.cases_evaluated == 0 and second.cases_skipped == CASES  # nothing can be due before the SLAs expire


def test_append_and_append_many_and_append_batches_all_recompute_the_index(env):
    store, pool, ids = env
    a, b, c, d = ids[:4]
    # a human event through the single append
    store.append(ack(a, AT + timedelta(hours=3)), PATHWAY)
    # the scheduler's events through append_many and append_batches
    now = AT + timedelta(days=9)
    for case_id, how in ((b, "many"), (c, "batches")):
        model = store.read_model(case_id, PATHWAY)
        planned = plan_case(case_id, model, PATHWAY, CAL, now)
        assert planned
        if how == "many":
            store.append_many(planned, PATHWAY)
        else:
            assert store.append_batches([planned], PATHWAY) == [store.read_model(case_id, PATHWAY).state]
    rows = index_rows(pool)
    assert all(rows[x][0] is not None for x in (a, b, c))
    assert rows[d][0] is None  # untouched
    assert assert_index_is_the_function_of_the_log(store, pool) == 3
    # the single-case path and the batch path agree on the value
    ma, mb = store.read_model(a, PATHWAY), store.read_model(b, PATHWAY)
    assert rows[a][0] == (next_due_at(ma, PATHWAY, CAL) or NEVER)
    assert rows[b][0] == (next_due_at(mb, PATHWAY, CAL) or NEVER)


def test_a_refused_append_leaves_the_index_alone(env):
    store, pool, ids = env
    a = ids[0]
    store.append(ack(a, AT + timedelta(hours=1)), PATHWAY)
    before = index_rows(pool)[a]
    with pytest.raises(InvalidTransition):
        store.append(Event(a, "close", AT + timedelta(hours=2), OWNER, SIGNED), PATHWAY)
    assert index_rows(pool)[a] == before


# ---- the indexed sweep plans exactly what a full scan plans ----------------------------------------------


def run_scenario(url, *, full_scan, batch_cases=None):
    """The same cases, human events and sweep times in a database; returns the scheduler's events."""
    pool = create_pool(url, max_size=4)
    try:
        store = PostgresEventStore(pool)
        ids = seed_open_cases(store, PATHWAY, CASES)
        evaluated = skipped = 0
        for step, days in enumerate((0.5, 2.2, 3.1, 5.4, 8.0, 8.5, 13.0, 21.0, 22.0, 40.0, 41.0)):
            now = AT + timedelta(days=days)
            # between sweeps a few cases get a human event, at distinct times
            for i, case_id in enumerate(ids):
                if (i + step) % 7 == 0:
                    try:
                        store.append(ack(case_id, now - timedelta(hours=2)), PATHWAY)
                    except InvalidTransition:
                        pass
            out = run_sweep(store, PATHWAY, CAL, now, full_scan=full_scan, batch_cases=batch_cases)
            assert out.errors == [] and out.rejected == 0
            evaluated += out.cases_evaluated
            skipped += out.cases_skipped
        return scheduler_events(pool), evaluated, skipped
    finally:
        pool.close()


@pytest.mark.parametrize("batch_cases", [1, 100])
def test_the_indexed_sweep_writes_exactly_the_events_of_a_full_scan(batch_cases):
    with scratch_database("a20full") as url_full, scratch_database("a20idx") as url_idx:
        full, full_eval, _ = run_scenario(url_full, full_scan=True, batch_cases=1)
        indexed, idx_eval, idx_skipped = run_scenario(url_idx, full_scan=False, batch_cases=batch_cases)
    assert len(full) > CASES  # the scenario really emits breaches and rungs
    assert indexed == full  # same event ids, case, seq, type and payload
    assert idx_skipped > 0 and idx_eval < full_eval  # and the index really skipped work


def test_the_index_never_goes_stale_over_the_scenario():
    with scratch_database("a20inv") as url:
        pool = create_pool(url, max_size=4)
        try:
            store = PostgresEventStore(pool)
            ids = seed_open_cases(store, PATHWAY, CASES)
            for days in (0.5, 3.0, 9.0, 30.0):
                now = AT + timedelta(days=days)
                for case_id in ids[::5]:
                    try:
                        store.append(ack(case_id, now - timedelta(hours=1)), PATHWAY)
                    except InvalidTransition:
                        pass
                run_sweep(store, PATHWAY, CAL, now)
                assert_index_is_the_function_of_the_log(store, pool)
        finally:
            pool.close()


# ---- the index is advisory: missing or stale values cost time, never work --------------------------------


def test_a_null_or_foreign_basis_index_is_evaluated_and_repaired(env):
    store, pool, ids = env
    now = AT + timedelta(days=30)
    run_sweep(store, PATHWAY, CAL, AT + timedelta(days=1))  # indexes every case, emits nothing
    with pool.connection() as conn:
        conn.execute("UPDATE cases SET next_due_at = NULL WHERE id = ANY(%s)", (ids[:10],))
        conn.execute("UPDATE cases SET next_due_basis = 'other-calendar' WHERE id = ANY(%s)", (ids[10:20],))
    out = run_sweep(store, PATHWAY, CAL, now)
    assert out.cases_evaluated == CASES  # every case is overdue at +30 days; the 20 damaged ones too
    assert out.mark_overdue >= CASES and out.errors == []
    assert assert_index_is_the_function_of_the_log(store, pool) == CASES


def test_a_calendar_change_re_evaluates_every_case(env):
    store, pool, ids = env
    run_sweep(store, PATHWAY, CAL, AT + timedelta(days=1))
    import dataclasses

    other = dataclasses.replace(CAL, holidays=frozenset(CAL.holidays | {AT.date() + timedelta(days=1)}))
    out = run_sweep(PostgresEventStore(store._pool, calendar=other), PATHWAY, other, AT + timedelta(days=1))
    assert out.cases_evaluated == CASES  # stored under the old fingerprint: not trusted


def test_full_scan_ignores_a_wrong_index(env):
    store, pool, ids = env
    run_sweep(store, PATHWAY, CAL, AT + timedelta(days=1))
    with pool.connection() as conn:
        conn.execute("UPDATE cases SET next_due_at = %s", (NEVER,))  # a lie that hides every breach
    now = AT + timedelta(days=30)
    hidden = run_sweep(store, PATHWAY, CAL, now)
    assert hidden.cases_evaluated == 0 and hidden.mark_overdue == 0  # the index is trusted, so this is the lie's cost
    out = run_sweep(store, PATHWAY, CAL, now, full_scan=True)
    assert out.cases_evaluated == CASES and out.mark_overdue >= CASES  # the operator's escape hatch finds all of it
    assert assert_index_is_the_function_of_the_log(store, pool) == CASES  # and the appends repaired the index


def test_the_sweep_selects_a_pending_handoff_when_its_window_closes_and_a_pending_rung_when_it_is_due(env):
    store, pool, ids = env
    h, r = ids[:2]
    day = lambda n: AT + timedelta(days=n)  # noqa: E731
    for i, (type_, actor, payload) in enumerate((
        ("acknowledge", "lab:l", {}),
        ("accept_ownership", OWNER_PC, {}),
        ("contact_patient", OWNER_PC, {}),
        ("patient_acknowledge", "patient:p", {}),
        ("handoff_requested", OWNER_PC, {"to_provider": "endoscopy-unit"}),
    ), start=1):
        store.append(Event(h, type_, day(i), actor, payload), PATHWAY)
    model = store.read_model(h, PATHWAY)
    assert model.pending_handoff
    due = handoff_due_at(model, PATHWAY, CAL)
    run_sweep(store, PATHWAY, CAL, day(6))  # indexes everything; case r breaches its first SLA
    assert index_rows(pool)[h][0] <= due
    # the handoff is not selected before its window closes and is, with its bounce planned, once it has
    before = run_sweep(store, PATHWAY, CAL, due - timedelta(minutes=1))
    assert before.handoff_bounced == 0 and before.cases_evaluated < CASES
    after = run_sweep(store, PATHWAY, CAL, due + timedelta(minutes=1))
    assert after.handoff_bounced == 1 and after.errors == []
    # a case in the middle of its ladder is selected exactly when its next rung falls due, not before
    nd = index_rows(pool)[r][0]
    assert nd is not None and nd < NEVER
    model = store.read_model(r, PATHWAY)
    assert model.overdue and nd == next_due_at(model, PATHWAY, CAL)
    early = run_sweep(store, PATHWAY, CAL, nd - timedelta(minutes=1))
    assert early.escalate == 0
    assert plan_case(r, store.read_model(r, PATHWAY), PATHWAY, CAL, nd + timedelta(minutes=1)) != []
    late = run_sweep(store, PATHWAY, CAL, nd + timedelta(minutes=1))
    assert late.escalate >= 1


# ---- append_batches ----------------------------------------------------------------------------------------


def planned_for(store, ids, now):
    out = []
    for case_id in ids:
        planned = plan_case(case_id, store.read_model(case_id, PATHWAY), PATHWAY, CAL, now)
        assert planned
        out.append(planned)
    return out


def test_append_batches_matches_append_many_case_by_case():
    with scratch_database("a20bm") as url_a, scratch_database("a20bb") as url_b:
        pa, pb = create_pool(url_a, max_size=4), create_pool(url_b, max_size=4)
        try:
            sa, sb = PostgresEventStore(pa), PostgresEventStore(pb)
            ids = seed_open_cases(sa, PATHWAY, 12)
            assert seed_open_cases(sb, PATHWAY, 12) == ids
            now = AT + timedelta(days=9)
            batches = planned_for(sa, ids, now)
            states = [sa.append_many(p, PATHWAY) for p in batches]
            assert sb.append_batches(batches, PATHWAY) == states
            assert scheduler_events(pa) == scheduler_events(pb)
            assert {k: v[0] for k, v in index_rows(pa).items()} == {k: v[0] for k, v in index_rows(pb).items()}
            assert sb.append_batches(batches, PATHWAY) == states  # a retry is a no-op that reports the state
            assert scheduler_events(pa) == scheduler_events(pb)
        finally:
            pa.close()
            pb.close()


def test_a_refused_case_in_a_batch_loses_none_of_the_others(env):
    store, pool, ids = env
    now = AT + timedelta(days=9)
    batches = planned_for(store, ids[:6], now)
    # 1: the id of case 1's first event is already used on that case by an event with other content
    first = batches[1][0]
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO events (event_id, case_id, seq, type, at, actor, payload) VALUES (%s,%s,2,'acknowledge',%s,'patient:p','{}')",
            (first.event_id, first.case_id, AT + timedelta(hours=1)),
        )
        conn.execute("UPDATE cases SET current_state = 'acknowledged' WHERE id = %s", (first.case_id,))
    # 2: an event that is not a valid step from the case's state
    bad_state = [Event(batches[2][0].case_id, "close", now, OWNER, SIGNED)]
    # 3: an unknown case
    ghost = [Event(uuid4(), "mark_overdue", now, SCHEDULER_ACTOR, {})]
    # 4: an event id that belongs to another case
    other = batches[4][0]
    stolen = [Event(batches[5][0].case_id, other.type, now, SCHEDULER_ACTOR, dict(other.payload), other.event_id)]
    store.append_many(batches[4], PATHWAY)
    batch = [batches[0], batches[1], bad_state, ghost, batches[3], stolen]
    expect_total_before = len(scheduler_events(pool))
    out = store.append_batches(batch, PATHWAY)
    assert isinstance(out[0], str) and isinstance(out[4], str)  # the two good cases committed
    assert isinstance(out[1], EventContentConflict)
    assert isinstance(out[2], InvalidTransition)
    assert isinstance(out[3], CaseNotFound)
    assert isinstance(out[5], EventIdConflict)
    written = {r[1] for r in scheduler_events(pool)}
    assert ids[0] in written and ids[3] in written  # the good cases' events are there
    assert ids[1] not in written and ids[2] not in written and ids[5] not in written
    assert len(scheduler_events(pool)) == expect_total_before + len(batches[0]) + len(batches[3])
    # a refused case is exactly as it was
    assert store.read_model(ids[2], PATHWAY).state == "result_received"
    assert assert_index_is_the_function_of_the_log(store, pool) >= 2


def test_a_sweep_survives_a_case_that_is_refused_in_its_batch(env):
    """The sweep batches 100 cases at a time; one whose event id is taken by different content between the read and
    the write must be counted as rejected and must not stop or undo the other cases' events."""
    store, pool, ids = env
    now = AT + timedelta(days=9)
    victim = ids[7]
    planned = plan_case(victim, store.read_model(victim, PATHWAY), PATHWAY, CAL, now)[0]

    class Racing(PostgresEventStore):
        armed = True

        def append_batches(self, batches, pathway):
            if self.armed:  # another detector wins the race for the first planned event of `victim`
                Racing.armed = False
                with pool.connection() as conn:
                    conn.execute(
                        "INSERT INTO events (event_id, case_id, seq, type, at, actor, payload)"
                        " VALUES (%s, %s, 2, 'mark_overdue', %s, %s, %s)",
                        (planned.event_id, victim, now, SCHEDULER_ACTOR, '{"state": "different content"}'),
                    )
            return super().append_batches(batches, pathway)

    out = run_sweep(Racing(pool), PATHWAY, CAL, now)
    assert out.errors == []
    assert out.rejected == 1  # the victim, reported like any refused append
    written = {r[1] for r in scheduler_events(pool)}
    assert written >= set(ids) - {victim} or len(written) >= CASES - 1
    assert out.mark_overdue + out.escalate >= CASES - 1
    # a clean sweep afterwards finds the other cases done
    again = run_sweep(PostgresEventStore(pool), PATHWAY, CAL, now)
    assert again.mark_overdue == 0 or again.rejected <= 1


def test_overlapping_batches_in_opposite_orders_do_not_deadlock(env):
    store, pool, ids = env
    now = AT + timedelta(days=9)
    batches = planned_for(store, ids[:20], now)
    results, errors = [], []
    barrier = threading.Barrier(2)

    def go(order):
        barrier.wait()
        try:
            results.append(PostgresEventStore(pool).append_batches([batches[i] for i in order], PATHWAY))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=go, args=(list(range(20)),))
    t2 = threading.Thread(target=go, args=(list(reversed(range(20))),))
    t1.start(); t2.start(); t1.join(30); t2.join(30)
    assert not t1.is_alive() and not t2.is_alive() and errors == []
    events = scheduler_events(pool)
    assert len({r[0] for r in events}) == len(events) == sum(len(b) for b in batches)  # once each


def test_append_batches_refuses_a_case_listed_twice_and_an_empty_case(env):
    store, pool, ids = env
    now = AT + timedelta(days=9)
    b = planned_for(store, ids[:1], now)[0]
    with pytest.raises(ValueError):
        store.append_batches([b, b], PATHWAY)
    with pytest.raises(ValueError):
        store.append_batches([[]], PATHWAY)
    assert store.append_batches([], PATHWAY) == []
    assert scheduler_events(pool) == []
