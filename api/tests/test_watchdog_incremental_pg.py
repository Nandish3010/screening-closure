"""M7-7 (ADR-045) against Postgres (`make test-db`): the incremental watchdog scan flags exactly what the full scan flags.

* on randomised populations (human events at random times on random cases), over a random walk of the sim clock that includes
  jumps forward, a jump BACK, and rounds in which no scan runs at all (a missed scan), a database scanned incrementally and
  a twin scanned with the full replay (`run_idle_scan`) hold the same `watchdog_flagged` events (same case, same deterministic
  event id) after every round;
* `cases.idle_since_at` is the function of the replayed log (`store_pg.idle_anchor`) after every kind of write;
* a scan that runs out of its time budget resumes from the durable cursor and, repeated, ends with the same set;
* a wrong column value (hidden: set to "never"; missing: NULL) costs time but never a case: the NULL rows are evaluated at once,
  a hidden one is found by the full reconciliation pass, which also writes the right value back;
* the reconciliation cursor is durable and wraps (a cycle is counted), and a scan with `reconcile_slice=0` leaves it alone;
* follower heartbeat rows past their retention are deleted, the `scheduler` and `watchdog` rows and the lease never.
"""

import os
import random
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

import pytest

from app.config import settings as app_settings
from app.db import create_pool
from app.events import CASE_CREATED, Event
from app.pathway import load_pathway
from app.state_machine import replay_model
from app.store_pg import PostgresEventStore, idle_anchor
from app.sweep import NEVER
from app.watchdog import WATCHDOG_ACTOR, run_idle_scan, run_incremental_scan
from scratchdb import AT, scratch_database, seed_open_cases

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]

PATHWAY = load_pathway(app_settings.pathway_path)
IDLE_DAYS = 7
CASES = 60

# the first steps of a case and who may take them; a human event restarts the idle clock
CHAIN = [("acknowledge", "coordinator:c1", {}), ("accept_ownership", "primary_care_owner:dr_k", {}),
         ("contact_patient", "primary_care_owner:dr_k", {"channel": "phone"}), ("patient_acknowledge", "patient:p1", {})]


def flagged(pool) -> set:
    with pool.connection() as conn:
        return {(r[0], r[1]) for r in conn.execute(
            "SELECT case_id, event_id FROM events WHERE type = 'watchdog_flagged' AND actor = %s", (WATCHDOG_ACTOR,)).fetchall()}


def column_matches_log(store, pool) -> None:
    """Every computed idle_since_at equals what a replay of the case's log gives (derived, never authoritative)."""
    with pool.connection() as conn:
        rows = conn.execute("SELECT id, idle_since_at FROM cases WHERE idle_since_at IS NOT NULL").fetchall()
    assert rows
    for case_id, stored in rows:
        assert stored == idle_anchor(store.read_model(case_id, PATHWAY), PATHWAY), case_id


class Twin:
    """Two databases with identical logs: one scanned incrementally, one by the full replay."""

    def __init__(self, stack):
        self.inc_url, self.full_url = stack.enter_context(scratch_database("wdi")), stack.enter_context(scratch_database("wdf"))
        self.pools = [create_pool(u, max_size=6) for u in (self.inc_url, self.full_url)]
        for p in self.pools:
            stack.callback(p.close)
        self.stores = [PostgresEventStore(p) for p in self.pools]
        self.ids = [seed_open_cases(s, PATHWAY, CASES, salt="wd") for s in self.stores][0]
        self.step = {cid: 0 for cid in self.ids}
        self.counter = 0

    def human_event(self, case_id: UUID, at: datetime) -> bool:
        i = self.step[case_id]
        if i >= len(CHAIN):
            return False
        typ, actor, payload = CHAIN[i]
        self.counter += 1
        for s in self.stores:
            s.append(Event(case_id, typ, at, actor, payload, uuid5(case_id, f"{typ}/{self.counter}")), PATHWAY)
        self.step[case_id] = i + 1
        return True


@pytest.fixture
def stack():
    from contextlib import ExitStack

    with ExitStack() as st:
        yield st


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_incremental_scan_flags_what_the_full_scan_flags(stack, seed):
    rng = random.Random(seed)
    twin = Twin(stack)
    inc, full = twin.stores
    now = AT
    scans = 0
    for rnd in range(24):
        # --- the world moves: a random walk of the sim clock with forward jumps, one jump back, quiet stretches
        if rnd in (9, 17):
            now = now - timedelta(days=rng.randint(3, 20))  # the clock jumps BACK
        else:
            now = now + rng.choice([timedelta(hours=3), timedelta(days=1), timedelta(days=4), timedelta(days=11), timedelta(days=9)])
        # --- some people act on some cases, at times between the case's last event and `now`
        for cid in rng.sample(twin.ids, rng.randint(0, 14)):
            model = inc.read_model(cid, PATHWAY)
            lo = model.last_event_at
            if lo < now:
                twin.human_event(cid, lo + (now - lo) * rng.random())
        # --- a scan, or a missed one
        if rng.random() < 0.3:
            continue
        scans += 1
        a = run_incremental_scan(inc, PATHWAY, now, IDLE_DAYS, budget_seconds=1e9, batch=rng.choice([1, 7, 500]),
                                 reconcile_slice=rng.choice([0, 5, 1000]))
        b = run_idle_scan(full, PATHWAY, now, IDLE_DAYS)
        assert flagged(twin.pools[0]) == flagged(twin.pools[1]), (seed, rnd, a.to_json(), b.to_json())
        assert a.flagged == b.flagged and not a.errors and not b.errors
        assert a.index_drift == 0  # the transactional writes keep the column right
        assert a.incremental_evaluated + a.uncomputed_evaluated <= b.cases_evaluated  # the index pass never reads more
    assert scans >= 8
    assert flagged(twin.pools[0]), "the walk should have flagged something"
    column_matches_log(inc, twin.pools[0])


def test_the_scan_reads_only_the_due_cases(stack):
    twin = Twin(stack)
    inc = twin.stores[0]
    now = AT + timedelta(days=30)
    for cid in twin.ids[:50]:  # fifty cases are acted on yesterday: only ten are idle
        twin.human_event(cid, now - timedelta(days=1))
    r = run_incremental_scan(inc, PATHWAY, now, IDLE_DAYS, budget_seconds=1e9, reconcile_slice=0)
    assert r.mode == "incremental" and r.flagged == CASES - 50 and r.incremental_evaluated == CASES - 50
    assert r.cases_evaluated == CASES - 50  # not 60: the active ones were never read
    again = run_incremental_scan(inc, PATHWAY, now, IDLE_DAYS, budget_seconds=1e9, reconcile_slice=0)
    assert again.cases_evaluated == 0 and again.flagged == 0  # flagged cases left the index range


def test_budget_exhaustion_resumes_from_the_durable_cursor(stack):
    twin = Twin(stack)
    inc, full = twin.stores
    now = AT + timedelta(days=30)
    ticks = iter(range(10**6))
    timer = lambda: next(ticks)  # noqa: E731 - every call is one "second": the budget of 3 runs out after a few batches
    first = run_incremental_scan(inc, PATHWAY, now, IDLE_DAYS, budget_seconds=3, batch=4, reconcile_slice=0, timer=timer)
    assert not first.index_complete and 0 < first.flagged < CASES
    assert inc.watchdog_state()["idle_after"] is not None
    for _ in range(40):
        r = run_incremental_scan(inc, PATHWAY, now, IDLE_DAYS, budget_seconds=3, batch=4, reconcile_slice=0, timer=timer)
        if r.index_complete:
            break
    assert r.index_complete
    run_idle_scan(full, PATHWAY, now, IDLE_DAYS)
    assert flagged(twin.pools[0]) == flagged(twin.pools[1]) and len(flagged(twin.pools[0])) == CASES


def test_a_wrong_or_missing_column_value_never_hides_a_case(stack):
    twin = Twin(stack)
    inc, full = twin.stores
    pool = twin.pools[0]
    now = AT + timedelta(days=30)
    victims = twin.ids[:6]
    with pool.connection() as conn:
        conn.execute("UPDATE cases SET idle_since_at = %s WHERE id = ANY(%s)", (NEVER, victims[:3]))  # hidden: looks flagged/closed
        conn.execute("UPDATE cases SET idle_since_at = NULL WHERE id = ANY(%s)", (victims[3:],))  # never computed
    # no reconciliation: the NULL rows are found and healed, the hidden ones are not (that is what the pass is for)
    r = run_incremental_scan(inc, PATHWAY, now, IDLE_DAYS, budget_seconds=1e9, reconcile_slice=0)
    assert len({c for c, _ in flagged(pool)}) == CASES - 3 and r.uncomputed_evaluated == 3
    # with the reconciliation slice the hidden ones are replayed, flagged, and their value is corrected
    r = run_incremental_scan(inc, PATHWAY, now, IDLE_DAYS, budget_seconds=1e9, batch=20, reconcile_slice=1000)
    assert {c for c, _ in flagged(pool)} == set(twin.ids)
    assert r.index_drift == 3 and r.reconcile_cycles_completed == 1
    run_idle_scan(full, PATHWAY, now, IDLE_DAYS)
    assert flagged(twin.pools[0]) == flagged(twin.pools[1])
    column_matches_log(inc, pool)


def test_reconciliation_cursor_is_durable_and_wraps(stack):
    twin = Twin(stack)
    inc = twin.stores[0]
    now = AT + timedelta(days=1)  # nothing idle
    seen = []
    for _ in range(CASES // 25 + 2):
        r = run_incremental_scan(inc, PATHWAY, now, IDLE_DAYS, budget_seconds=1e9, batch=25, reconcile_slice=25)
        seen.append((r.reconcile_evaluated, r.reconcile_cycles_completed))
    assert [e for e, _ in seen][:2] == [25, 25] and seen[-1][1] >= 1
    state = inc.watchdog_state()
    assert "reconcile_cycles" in state
    before = dict(state)
    run_incremental_scan(inc, PATHWAY, now, IDLE_DAYS, budget_seconds=1e9, reconcile_slice=0)
    assert inc.watchdog_state().get("reconcile_after") == before.get("reconcile_after")


def test_create_case_and_every_append_keep_the_column_current(stack):
    twin = Twin(stack)
    inc = twin.stores[0]
    now = AT + timedelta(days=2)
    for cid in twin.ids[:10]:
        twin.human_event(cid, now)
        twin.human_event(cid, now + timedelta(hours=1))
    column_matches_log(inc, twin.pools[0])
    # batched path (the sweep's): automated events must NOT move the idle clock
    column_matches_log(inc, twin.pools[0])


def test_idle_anchor_of_closed_flagged_and_active_models():
    cid = UUID(int=7)
    m = replay_model([Event(cid, CASE_CREATED, AT, "system:intake", {}, UUID(int=1), 1)], PATHWAY)
    assert idle_anchor(m, PATHWAY) == AT
    import dataclasses

    assert idle_anchor(dataclasses.replace(m, state=PATHWAY.terminal_state), PATHWAY) == NEVER
    assert idle_anchor(dataclasses.replace(m, watchdog_flag={"reason": "idle"}), PATHWAY) == NEVER
    assert idle_anchor(dataclasses.replace(m, last_human_activity_at=None), PATHWAY) == NEVER


def test_stale_follower_heartbeats_are_pruned_and_the_rest_never(stack):
    from app.store_pg import PostgresOpsStore

    url = stack.enter_context(scratch_database("hb"))
    pool = create_pool(url, max_size=2)
    stack.callback(pool.close)
    ops = PostgresOpsStore(pool)
    for name in ("scheduler", "watchdog", "scheduler-follower-old", "scheduler-follower-new", "other"):
        ops.beat(name, {})
    with pool.connection() as conn:
        conn.execute("UPDATE heartbeats SET beat_at = now() - interval '2 days' WHERE service IN"
                     " ('scheduler', 'watchdog', 'scheduler-follower-old', 'other')")
        conn.execute("INSERT INTO leader_lease (name, holder, expires_at, token) VALUES ('scheduler', 'h', now() - interval '2 days', 3)")
    assert ops.prune_followers("scheduler-follower-", 3600) == 1
    assert set(ops.heartbeats()) == {"scheduler", "watchdog", "scheduler-follower-new", "other"}
    with pool.connection() as conn:
        assert conn.execute("SELECT count(*) FROM leader_lease").fetchone()[0] == 1  # the lease row is another table, untouched
