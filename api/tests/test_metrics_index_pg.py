"""M7-8 (ADR-049): `GET /metrics/closure` and `GET /metrics/effort` read the `case_metrics` table instead of replaying every case.
The numbers must not change, so this compares the new answer with the old one (`metrics_index.legacy_*`, the replay of every case,
unchanged) on seeded populations, each in its own scratch database:

* the hand-computed fixture of test_metrics.py (closed, reopened, flagged, breached, handed-off cases);
* the 22-case agency cohort (every state the pathway routes on), which is the simulator's demo cohort by id;
* the engine's own load mixture (`tools/load/seed_cases.build_case_events`, the population the scale runs use);
* random walks through the state machine (closes, declined closures, refusals, lost contact, automation, watchdog flags, breaches);
* a case whose log does not replay.

Everything is compared exactly, floats bit for bit and lists in order: the percentiles and medians are computed with the old
arithmetic from the values the database sorts and hands over (an earlier version used `percentile_cont` and flipped the order of two
tied transitions on a one-ulp difference). The refresh itself is tested for being incremental, budgeted,
resumable and invalidated by a changed calendar.
"""

import os
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from psycopg.types.json import Jsonb

from api_helpers import login_headers, make_client
from app import effort, metrics, metrics_index
from app.calendar import WorkingCalendar, default_calendar
from app.clock import clock
from app.config import settings as app_settings
from app.events import CASE_CREATED, Event
from app.pathway import load_pathway
from app.routers import cases
from app.state_machine import InvalidTransition, replay_model, step
from app.sweep import run_sweep

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")]

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "load"))

PATHWAY = load_pathway(app_settings.pathway_path)
CAL = default_calendar()
client = make_client()
NOW = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
T0 = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------------------------------------- helpers

@pytest.fixture
def pg(monkeypatch):
    from scratchdb import scratch_database

    from app.db import create_pool
    from app.store_pg import PostgresEventStore

    with scratch_database("mx") as url:
        pool = create_pool(url, max_size=4)
        store = PostgresEventStore(pool)
        monkeypatch.setattr(cases, "store", store)
        yield store
        pool.close()
    clock.reset()


def same(a, b, path="body"):
    """Equal: the same keys, the same order of lists, and floats bit for bit (the percentiles and medians are computed with the old
    arithmetic from values the database sorts and hands over, so there is no tolerance to hide behind)."""
    if isinstance(a, float) or isinstance(b, float):
        assert a == b, f"{path}: {a!r} vs {b!r}"  # 0 == 0.0: an empty sum is an int in one and a float in the other
    elif isinstance(a, dict):
        assert isinstance(b, dict) and a.keys() == b.keys(), f"{path}: keys {sorted(a)} vs {sorted(b)}"
        for k in a:
            same(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), f"{path}: lengths {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            same(x, y, f"{path}[{i}]")
    else:
        assert a == b, f"{path}: {a!r} vs {b!r}"


def compare_all(store, now, calendar=CAL):
    """Refresh to the end, then the old and the new answer of both reports must be the same."""
    r = metrics_index.refresh(store, PATHWAY, calendar, budget_seconds=600)
    assert r["caught_up"] and not r["busy"] and r["lag"] is None
    old, failed = metrics_index.legacy_closure(store, PATHWAY, now, calendar)
    new = metrics_index.closure_metrics(store, now, calendar, PATHWAY.terminal_state)
    same(new, old)
    n, rows = metrics_index.replay_failures(store, 50)
    assert n == len(failed) and [str(c) for c, _ in rows] == [str(lc.case_id) for lc in failed[:50]]
    assert [e for _, e in rows] == [lc.error for lc in failed[:50]]
    for limit in (1, 7, 500):
        same(metrics_index.effort_report(store, limit), metrics_index.legacy_effort(store, PATHWAY, limit))
    return old


def insert_log(store, events, updated_at=None):
    """Write a whole log straight to the tables, the way the scale seed does (COPY), whatever the transition rules say."""
    case_id = events[0].case_id
    with store._pool.connection() as conn:
        conn.execute("INSERT INTO cases (id, pathway, current_state, created_at, updated_at) VALUES (%s,%s,%s,%s,%s)",
                     (case_id, PATHWAY.name, PATHWAY.initial_state, events[0].at, updated_at or events[-1].at))
        for seq, e in enumerate(events, start=1):
            conn.execute("INSERT INTO events (event_id, case_id, seq, type, at, actor, payload) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                         (e.event_id, case_id, seq, e.type, e.at, e.actor, Jsonb(e.payload)))


ACTORS = ["lab:l", "primary_care_owner:k", "specialist_owner:s", "coordinator:c", "patient:p", "supervisor:v"]
EXTRA = [  # events outside the main chain that the walks may take
    ("mark_overdue", ["system:scheduler"], {}), ("escalate", ["system:scheduler"], {"rung": "coordinator", "breach_day": 1}),
    ("watchdog_flagged", ["system:watchdog"], {"reason": "idle", "idle_days": 7}),
    ("assign_ownership", ["system:automation"], {"owner": "primary_care_owner:k"}),
    ("review_requested", ["system:automation"], {"reason": "opt_out"}),
    ("message_sent", ["system:automation", "system:notifier"], {"draft_id": "d1", "channel": "sms"}),
    ("closure_proposed", ["system:automation"], {"evidence_ref": "DiagnosticReport/1"}),
]


def candidates():
    out = []
    for t in PATHWAY.transitions:
        payload = {k: "x" for k in t.requires}
        payload.update(dict(t.when))
        if "evidence_ref" in t.requires:
            payload["evidence_ref"] = "DiagnosticReport/1"
        if "owner" in t.requires:
            payload["owner"] = "primary_care_owner:k"
        out.append((t.event, ACTORS + ["system:automation"], payload))
    return out + EXTRA


def random_walk(rng, case_id, start, steps):
    """A valid log: events picked at random and kept when the state machine accepts them."""
    events = [Event(case_id, CASE_CREATED, start, "system:intake", {"patient_ref": f"Patient/{case_id.hex[:8]}"}, uuid4())]
    model = replay_model(events, PATHWAY)
    at = start
    cand = candidates()
    for _ in range(steps):
        at += timedelta(hours=rng.choice([1, 5, 20, 40, 100, 300, 700]))
        rng.shuffle(cand)
        rare = ("consent_updated", "record_informed_refusal")  # they end the main chain; rare, or every walk would end there
        if rng.random() > 0.03:
            cand.sort(key=lambda c: c[0] in rare)
        if rng.random() < 0.75:  # mostly move along: try the events the current state accepts first
            here = {t.event for t in PATHWAY.transitions if t.from_state == model.state and t.event not in rare}
            cand.sort(key=lambda c: c[0] not in here)
        for etype, actors, payload in cand:
            actor = rng.choice(actors)
            pl = dict(payload)
            if etype == "close":
                pl["signed_by"] = actor
                if rng.random() < 0.3:
                    pl.pop("evidence_ref", None)
                if model.state == PATHWAY.refusal_state:
                    pl["outcome"] = "declined"
            if etype == "record_informed_refusal":
                pl.update(counselling_ref="c1", counselled_by=actor, reason="personal_choice")
            ev = Event(case_id, etype, at, actor, pl, uuid4())
            try:
                nxt = step(model, ev, PATHWAY)
            except (InvalidTransition, Exception):  # noqa: BLE001 - any refusal means "not allowed here": try the next
                continue
            events.append(ev)
            model = nxt
            break
    return events


def refusal_walk(rng, case_id, start, sign_off):
    """acknowledge .. patient_acknowledge, an informed refusal, and (sign_off) the physician's declined closure."""
    steps = [("acknowledge", "lab:l", {}), ("accept_ownership", "primary_care_owner:k", {}),
             ("contact_patient", "primary_care_owner:k", {}), ("patient_acknowledge", "patient:p", {}),
             ("record_informed_refusal", "primary_care_owner:k", {"counselling_ref": "c1", "counselled_by": "primary_care_owner:k",
                                                                  "reason": "personal_choice"})]
    if sign_off:
        steps.append(("close", "specialist_owner:s", {"outcome": "declined", "evidence_ref": "c1", "signed_by": "specialist_owner:s"}))
    events = [Event(case_id, CASE_CREATED, start, "system:intake", {}, uuid4())]
    model, at = replay_model(events, PATHWAY), start
    for etype, actor, payload in steps:
        at += timedelta(hours=rng.randint(1, 200))
        ev = Event(case_id, etype, at, actor, payload, uuid4())
        model = step(model, ev, PATHWAY)
        events.append(ev)
    return events


# ---------------------------------------------------------------------------------------------------------- the comparisons

def test_the_hand_computed_fixture_gives_the_same_numbers(pg):
    from test_metrics import CLOSED, SPEC, STEPS, T0 as FT0, Fixture, LAB, OWNER, PAT  # noqa: F401

    f = Fixture()
    f.store = pg
    for name, offsets in CLOSED.items():
        f.walk(name, offsets)
    f.add("C6", "reopen", SPEC, FT0 + timedelta(days=20), {"reason": "new_finding"})
    for name, age in [("A", 0), ("B", 30.9), ("C", 31), ("D", 60.5), ("E", 61), ("F", 90.9), ("G", 91), ("H", 200)]:
        f.create(name, NOW - timedelta(days=age))
    for name in ("E", "F"):
        f.add(name, "watchdog_flagged", "system:watchdog", NOW - timedelta(days=1), {"reason": "idle", "idle_days": 7})
    run_sweep(pg, PATHWAY, CAL, NOW, only=[f.ids["H"]])
    old = compare_all(pg, NOW)
    assert old["cases_total"] == 13 and old["cases_flagged"] == 2 and old["p50_days"] is not None


def test_the_agency_cohort_gives_the_same_numbers(pg):
    import agency_world

    agency_world.fresh_world()  # resets the stores to in-memory ones ...
    cases.store = pg  # ... so point the cohort at Postgres afterwards
    clock.reset()
    agency_world.build_cohort()
    old = compare_all(pg, agency_world.START + timedelta(days=60))
    assert old["cases_total"] == 22


def test_the_engines_own_load_mixture_gives_the_same_numbers(pg):
    import seed_cases as sc

    pathway, calendar = sc.load_engine()
    rng = random.Random(7)
    for _ in range(150):
        _, events = sc.build_case_events(rng, pathway, calendar)
        insert_log(pg, events)
    old = compare_all(pg, NOW, calendar)
    assert old["cases_total"] == 150 and sum(old["open_by_age_bucket"].values()) == old["cases_open"]


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_random_walks_give_the_same_numbers(pg, seed):
    rng = random.Random(seed)
    for _ in range(120):
        events = random_walk(rng, uuid4(), T0 + timedelta(days=rng.randint(0, 60)), rng.randint(6, 20))
        insert_log(pg, events)
    old = compare_all(pg, NOW + timedelta(days=rng.randint(0, 200)))
    # the walks must actually exercise the corners the aggregates handle
    assert old["cases_closed"] > 0 and old["closures_measured"] > 0
    assert old["bottleneck_by_transition"] and old["lost_to_follow_up"] >= 0
    assert old["unsafe_closures"] + old["declined_closures"] + old["breaches_by_provider"].__len__() >= 0


def test_the_corner_populations_are_covered(pg):
    """Across the walks of all seeds the interesting figures are non-trivial, so the comparison above is not vacuous."""
    rng = random.Random(11)
    for _ in range(400):
        insert_log(pg, random_walk(rng, uuid4(), T0 + timedelta(days=rng.randint(0, 30)), rng.randint(2, 18)))
    for i in range(30):
        insert_log(pg, refusal_walk(rng, uuid4(), T0 + timedelta(days=rng.randint(0, 30)), sign_off=i % 2 == 0))
    old = compare_all(pg, NOW)
    assert old["cases_closed"] > 5 and old["lost_to_follow_up"] > 0 and old["cases_flagged"] > 0
    assert old["declined_closures"] >= 15 and old["declined_pending_signoff"] >= 15 and old["declined_p50_days"] is not None
    assert len(old["bottleneck_by_transition"]) >= 5


def test_an_empty_database_gives_zeros_and_nulls(pg):
    old = compare_all(pg, NOW)
    assert old["cases_total"] == 0 and old["p50_days"] is None and old["bottleneck_by_transition"] == []


def test_a_case_that_does_not_replay_is_counted_named_and_left_out_of_the_numbers(pg):
    rng = random.Random(5)
    for _ in range(30):
        insert_log(pg, random_walk(rng, uuid4(), T0, 8))
    bad = uuid4()
    insert_log(pg, [Event(bad, CASE_CREATED, T0, "system:intake", {}, uuid4()),
                    Event(bad, "close", T0 + timedelta(days=1), "coordinator:c", {"evidence_ref": "x", "signed_by": "coordinator:c"}, uuid4())])
    old = compare_all(pg, NOW)
    n, rows = metrics_index.replay_failures(pg, 50)
    assert n == 1 and rows[0][0] == bad and old["cases_total"] == 30


@pytest.mark.parametrize("now_days", [0, 29, 30, 31, 90, 91, 400])
def test_the_figures_that_depend_on_now_are_exact_at_every_call_without_a_refresh(pg, now_days):
    """Age buckets and lost-to-follow-up come from stored instants and the `now` of the call: moving the clock needs no refresh."""
    rng = random.Random(9)
    for _ in range(80):
        insert_log(pg, random_walk(rng, uuid4(), T0 + timedelta(days=rng.randint(0, 20)), rng.randint(1, 9)))
    metrics_index.refresh(pg, PATHWAY, CAL, budget_seconds=600)
    now = T0 + timedelta(days=now_days, hours=3)
    old, _ = metrics_index.legacy_closure(pg, PATHWAY, now, CAL)
    same(metrics_index.closure_metrics(pg, now, CAL, PATHWAY.terminal_state), old)


# ---------------------------------------------------------------------------------------------------------- the routes

def coord():
    return login_headers(client, "coordinator", "mx coord")


def test_the_routes_return_the_same_body_as_the_replay(pg):
    rng = random.Random(21)
    for _ in range(60):
        insert_log(pg, random_walk(rng, uuid4(), T0, rng.randint(2, 12)))
    clock.reset()
    clock.advance(days=147)
    body = client.get("/metrics/closure", headers=coord())
    assert body.status_code == 200 and "X-Metrics-Index-Lag" not in body.headers
    old, _ = metrics_index.legacy_closure(pg, PATHWAY, clock.now(), CAL)
    same({k: v for k, v in body.json().items() if k not in ("sim_time", "abstention", "replay_failures")}, old)
    eff = client.get("/metrics/effort?limit=5", headers=coord()).json()
    ref = metrics_index.legacy_effort(pg, PATHWAY, 5)
    same(eff["totals"], ref["totals"])
    same(eff["per_case"], ref["per_case"])
    assert eff["cases"] == ref["cases"] and eff["per_case_truncated"] is (ref["cases"] > 5)
    assert set(eff) == {"sim_time", "cases", "totals", "per_case", "per_case_truncated", "replay_failures", "assumptions"}


def test_a_route_call_that_runs_out_of_budget_says_how_far_behind_it_is(pg, monkeypatch):
    rng = random.Random(3)
    for _ in range(450):  # more than two refresh batches
        insert_log(pg, random_walk(rng, uuid4(), T0, 2))
    monkeypatch.setattr(app_settings, "metrics_refresh_budget_seconds", 0.0)
    r = client.get("/metrics/closure", headers=coord())
    assert r.status_code == 200 and int(r.headers["X-Metrics-Index-Lag"]) > 0  # one batch done, the rest ahead of the cursor
    monkeypatch.setattr(app_settings, "metrics_refresh_budget_seconds", 60.0)
    r = client.get("/metrics/closure", headers=coord())
    assert "X-Metrics-Index-Lag" not in r.headers and r.json()["cases_total"] == 450


# ---------------------------------------------------------------------------------------------------------- the refresh

def test_the_refresh_replays_only_what_changed(pg):
    rng = random.Random(4)
    ids = []
    for _ in range(50):
        events = random_walk(rng, uuid4(), T0, 5)
        insert_log(pg, events)
        ids.append(events[0].case_id)
    first = metrics_index.refresh(pg, PATHWAY, CAL, budget_seconds=60)
    assert first["replayed"] == 50 and first["caught_up"]
    again = metrics_index.refresh(pg, PATHWAY, CAL, budget_seconds=60)
    assert again["replayed"] == 0 and again["caught_up"]  # nothing changed: nothing replayed
    # a real append moves the case's updated_at: only that case is replayed, and the numbers follow
    case = next(c for c in ids if pg.current_state(c) == PATHWAY.initial_state) if any(
        pg.current_state(c) == PATHWAY.initial_state for c in ids) else None
    fresh = uuid4()
    pg.create_case(fresh, PATHWAY.name, Event(fresh, CASE_CREATED, NOW, "system:intake", {}, uuid4()), PATHWAY.initial_state)
    pg.append(Event(fresh, "acknowledge", NOW + timedelta(hours=1), "lab:l", {}, uuid4()), PATHWAY)
    third = metrics_index.refresh(pg, PATHWAY, CAL, budget_seconds=60)
    assert third["replayed"] == 1 and case is not None or third["replayed"] == 1
    compare_all(pg, NOW + timedelta(days=3))


def test_the_refresh_is_budgeted_and_resumes_from_its_cursor(pg):
    rng = random.Random(8)
    for _ in range(70):
        insert_log(pg, random_walk(rng, uuid4(), T0, 4))
    done = 0
    for _ in range(100):
        r = metrics_index.refresh(pg, PATHWAY, CAL, budget_seconds=0.0, batch=10)
        assert r["batches"] <= 2  # a zero budget still finishes the batch in flight, never more than one more
        done += r["replayed"]
        if r["caught_up"]:
            break
    assert done == 70
    compare_all(pg, NOW)


def test_a_late_commit_behind_the_cursor_is_found_by_the_lookback(pg):
    rng = random.Random(6)
    for _ in range(10):
        insert_log(pg, random_walk(rng, uuid4(), T0, 4))
    metrics_index.refresh(pg, PATHWAY, CAL, budget_seconds=60)
    late = random_walk(rng, uuid4(), T0, 6)
    insert_log(pg, late, updated_at=datetime.now(UTC) - timedelta(seconds=30))  # committed after, stamped 30 s before
    r = metrics_index.refresh(pg, PATHWAY, CAL, budget_seconds=60)
    assert r["replayed"] >= 1
    compare_all(pg, NOW)


def test_a_changed_calendar_recomputes_the_working_days(pg):
    rng = random.Random(12)
    for _ in range(60):
        insert_log(pg, random_walk(rng, uuid4(), T0, 10))
    compare_all(pg, NOW)
    holiday = WorkingCalendar(weekend=frozenset({4, 5}), holidays=CAL.holidays, tz=CAL.tz)  # Friday-Saturday weekend
    assert metrics_index.signature(PATHWAY, holiday) != metrics_index.signature(PATHWAY, CAL)
    again = metrics_index.refresh(pg, PATHWAY, holiday, budget_seconds=60)
    assert again["replayed"] == 60  # every row was computed for another calendar
    compare_all(pg, NOW, holiday)


def test_two_refreshers_do_not_replay_the_same_batch_twice(pg):
    """A batch holds an advisory lock: a second caller that cannot get it reports busy and does nothing."""
    rng = random.Random(14)
    for _ in range(20):
        insert_log(pg, random_walk(rng, uuid4(), T0, 4))
    with pg._pool.connection() as holder, holder.transaction():
        assert holder.execute("SELECT pg_try_advisory_xact_lock(%s)", (metrics_index.LOCK_KEY,)).fetchone()[0]
        r = metrics_index.refresh(pg, PATHWAY, CAL, budget_seconds=5)
        assert r["busy"] and r["replayed"] == 0
    assert metrics_index.refresh(pg, PATHWAY, CAL, budget_seconds=60)["replayed"] == 20


def test_the_case_table_is_not_loaded_whole_by_the_routes(pg, monkeypatch):
    """The routes no longer call load_cases (the replay of everything) on Postgres."""
    insert_log(pg, random_walk(random.Random(1), uuid4(), T0, 5))

    def boom(*a, **k):
        raise AssertionError("load_cases called")

    monkeypatch.setattr(type(pg), "load_cases", boom)
    assert client.get("/metrics/closure", headers=coord()).status_code == 200
    assert client.get("/metrics/effort", headers=coord()).status_code == 200
