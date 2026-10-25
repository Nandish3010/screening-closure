"""T-A29 (A10): closure metrics against hand-computed values on a small fixture.

The fixture is built from explicit event times, so every expected number below was worked out
from the spec of the fixture (offsets in days), not read back from the code under test.
"""

import os
from datetime import UTC, datetime, timedelta
from statistics import median
from uuid import UUID, uuid4

import pytest

from api_helpers import login_headers, make_client
from app import metrics
from app.calendar import default_calendar
from app.clock import clock
from app.config import settings as app_settings
from app.events import CASE_CREATED, Event, InMemoryEventStore
from app.pathway import load_pathway
from app.routers import cases
from app.sweep import run_sweep

PATHWAY = load_pathway(app_settings.pathway_path)
CAL = default_calendar()
T0 = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)  # a Monday
NOW = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)  # a Monday, 147 days after T0
LAB, OWNER, PAT, SPEC = "lab:l", "primary_care_owner:k", "patient:p", "specialist_owner:s"
WATCHDOG = "system:watchdog"

STEPS = [  # (event, actor) for the main path to closure
    ("acknowledge", LAB), ("accept_ownership", OWNER), ("contact_patient", OWNER),
    ("patient_acknowledge", PAT), ("book_confirmatory", SPEC), ("complete_confirmatory", SPEC),
    ("clinician_review", SPEC), ("close", SPEC),
]
STATES = [s for s in PATHWAY.states]  # result_received ... closed
SAFE = {"evidence_ref": "DiagnosticReport/1"}  # signed by the closer: `add` fills signed_by in (A36)


def ev(cid, type_, actor, at, payload=None):
    return Event(cid, type_, at, actor, payload or {}, uuid4())


class Fixture:
    def __init__(self):
        self.store = InMemoryEventStore()
        self.ids: dict[str, UUID] = {}

    def create(self, name, at):
        cid = uuid4()
        self.ids[name] = cid
        self.store.create_case(cid, PATHWAY.name, ev(cid, CASE_CREATED, "system:intake", at, {"patient_ref": f"Patient/{name}"}),
                               PATHWAY.initial_state)
        return cid

    def add(self, name, type_, actor, at, payload=None):
        if type_ == "close":
            payload = {**SAFE, "signed_by": actor, **(payload or {})}
        self.store.append(ev(self.ids[name], type_, actor, at, payload), PATHWAY)

    def walk(self, name, offsets, upto=8):
        """Offsets are days after T0 for each of the eight main-path steps."""
        self.create(name, T0)
        for (type_, actor), day in list(zip(STEPS, offsets))[:upto]:
            self.add(name, type_, actor, T0 + timedelta(days=day))

    def records(self, *names):
        return [metrics.build_record(self.store.events_for(self.ids[n]), PATHWAY) for n in names]


# Offsets (days after T0) of ack, accept, contact, patient ack, book, complete, review, close.
CLOSED = {
    "C1": [1, 2, 3, 5, 6, 8, 9, 10],
    "C2": [1, 3, 5, 9, 12, 18, 21, 24],
    "C3": [2, 4, 7, 13, 18, 28, 34, 38],
    "C4": [2, 5, 9, 17, 24, 38, 48, 52],
    "C6": [1, 2, 4, 6, 9, 12, 14, 15],  # closed on day 15, reopened on day 20
}


@pytest.fixture(scope="module")
def fx() -> Fixture:
    f = Fixture()
    for name, offsets in CLOSED.items():
        f.walk(name, offsets)
    f.add("C6", "reopen", SPEC, T0 + timedelta(days=20), {"reason": "new_finding"})

    # open cases of known ages at NOW (only a case_created unless said otherwise)
    for name, age in [("A", 0), ("B", 30.9), ("C", 31), ("D", 60.5), ("E", 61), ("F", 90.9), ("G", 91), ("H", 200)]:
        f.create(name, NOW - timedelta(days=age))
    return f


@pytest.fixture(scope="module")
def full(fx) -> Fixture:
    f = fx
    # "Fresh2": created 45 days ago, acknowledged 10 days ago: recent progress, not lost
    f.create("Fresh2", NOW - timedelta(days=45))
    f.add("Fresh2", "acknowledge", LAB, NOW - timedelta(days=10))
    # "Lc": contacted 5 days after creation, lost contact flagged 10 days later
    f.create("Lc", NOW - timedelta(days=100))
    for i, (type_, actor) in enumerate(STEPS[:3], start=1):
        f.add("Lc", type_, actor, NOW - timedelta(days=100 - 2 * i))
    f.add("Lc", "mark_lost_contact", OWNER, NOW - timedelta(days=90), {"failed_attempts": 3})
    # watchdog flags on E and F; H is surfaced by a real sweep at NOW
    for name in ("E", "F"):
        f.add(name, "watchdog_flagged", WATCHDOG, NOW - timedelta(days=1), {"reason": "idle", "idle_days": 7})
    run_sweep(f.store, PATHWAY, CAL, NOW, only=[f.ids["H"]])
    # providers: P1 breaches twice (unassigned, then endo-a); P2 endo-b; P3 endo-a
    f.create("P1", T0)
    for day, (type_, actor) in zip([1, 2, 3], STEPS[:3]):
        f.add("P1", type_, actor, T0 + timedelta(days=day))
    run_sweep(f.store, PATHWAY, CAL, T0 + timedelta(days=15), only=[f.ids["P1"]])  # patient_acknowledge (7 wd) breached
    for name, provider in (("P1", "endo-a"), ("P2", "endo-b"), ("P3", "endo-a")):
        if name != "P1":
            f.create(name, T0)
            for day, (type_, actor) in zip([1, 2, 3], STEPS[:3]):
                f.add(name, type_, actor, T0 + timedelta(days=day))
        base = 21 if name == "P1" else 4
        f.add(name, "patient_acknowledge", PAT, T0 + timedelta(days=base))
        f.add(name, "handoff_requested", OWNER, T0 + timedelta(days=base), {"to_provider": provider})
        f.add(name, "handoff_accepted", SPEC, T0 + timedelta(days=base + 1))
        f.add(name, "book_confirmatory", SPEC, T0 + timedelta(days=base + 2))
        run_sweep(f.store, PATHWAY, CAL, NOW, only=[f.ids[name]])
    return f


def test_t_a29_percentiles_use_linear_interpolation_over_the_first_safe_close(fx):
    recs = fx.records("C1", "C2", "C3", "C4", "C6")
    ttc = metrics.time_to_closure(recs, CAL)
    # first closes on days 10, 15 (C6; its re-open on day 20 does not undo it), 24, 38, 52
    assert ttc["n"] == 5
    assert ttc["p50_days"] == 24.0
    assert ttc["p90_days"] == pytest.approx(38 + 0.6 * (52 - 38))  # position 0.9 x 4 = 3.6 -> 46.4
    # working days after the start day: Jan 15 = 8, Jan 20 = 11, Jan 29 = 18, Feb 12 = 28, Feb 26 = 38
    assert (ttc["p50_working_days"], ttc["p90_working_days"]) == (18.0, pytest.approx(28 + 0.6 * 10))
    four = metrics.time_to_closure(fx.records("C1", "C2", "C3", "C4"), CAL)
    assert (four["p50_days"], four["p90_days"]) == (31.0, pytest.approx(47.8))
    assert (four["p50_working_days"], four["p90_working_days"]) == (23.0, pytest.approx(35.0))


def test_t_a29_percentile_edge_cases():
    assert metrics.percentile([], 0.5) is None
    assert metrics.percentile([7], 0.9) == 7
    assert metrics.percentile([1, 2], 0.5) == 1.5
    assert metrics.percentile([5, 1, 3], 0.5) == 3
    assert metrics.percentile([0, 10], 0.0) == 0 and metrics.percentile([0, 10], 1.0) == 10
    assert metrics.time_to_closure([], CAL)["p50_days"] is None


def test_t_a29_open_cases_by_age_bucket_at_the_bucket_edges(fx, full):
    recs = full.records("C1", "C2", "C3", "C4", "C6", "A", "B", "C", "D", "E", "F", "G", "H", "Fresh2", "Lc")
    got = metrics.open_by_age_bucket(recs, NOW)
    # open = not currently closed (C6 was reopened: open again, 147 days old). Ages in whole days:
    # 0-30: A (0), B (30.9)    31-60: C (31), D (60.5), Fresh2 (45)    61-90: E (61), F (90.9)
    # 90+: G (91), H (200), Lc (100), C6 (147)
    assert got == {"0-30": 2, "31-60": 3, "61-90": 2, "90+": 4}
    assert metrics.age_bucket(30.999) == "0-30" and metrics.age_bucket(31) == "31-60"
    assert metrics.age_bucket(90.999) == "61-90" and metrics.age_bucket(91) == "90+"


def test_t_a29_breaches_by_provider_count_each_overdue_against_the_provider_at_the_time(full):
    got = metrics.breaches_by_provider(full.records("P1", "P2", "P3"))
    assert got == [{"provider": "endo-a", "count": 2}, {"provider": "endo-b", "count": 1},
                   {"provider": "unassigned", "count": 1}]


def test_t_a29_bottleneck_is_the_median_days_in_a_state_before_each_transition(fx):
    recs = fx.records("C1", "C2", "C3", "C4", "C6")
    rows = metrics.bottleneck_by_transition(recs, CAL)
    by_name = {r["transition"]: r for r in rows}
    # hand-computed from the offsets: days between consecutive steps, then the median
    spans: dict[str, list[float]] = {}
    for offsets in CLOSED.values():
        days = [0, *offsets]
        for i in range(8):
            spans.setdefault(f"{STATES[i]} -> {STATES[i + 1]}", []).append(days[i + 1] - days[i])
    spans["closed -> reopened"] = [5]
    assert {k: v["median_days"] for k, v in by_name.items()} == {k: median(v) for k, v in spans.items()}
    assert by_name["confirmatory_booked -> confirmatory_completed"]["median_days"] == 6  # [2,6,10,14,3]
    assert by_name["patient_contacted -> patient_acknowledged"]["median_days"] == 4  # [2,4,6,8,2]
    assert by_name["closed -> reopened"]["n"] == 1 and by_name["result_received -> acknowledged"]["n"] == 5
    assert [r["median_days"] for r in rows] == sorted((r["median_days"] for r in rows), reverse=True)  # slowest first
    # the four-case medians worked out by hand: 8.0, 5.0, 4.5, 4.0, 3.5, 2.5, 2.0, 1.5
    four = metrics.bottleneck_by_transition(fx.records("C1", "C2", "C3", "C4"), CAL)
    assert [r["median_days"] for r in four] == [8.0, 5.0, 4.5, 4.0, 3.5, 2.5, 2.0, 1.5]
    assert four[0]["transition"] == "confirmatory_booked -> confirmatory_completed"
    # working days: Jan 5 -> Jan 6 is one working day (C1's first step)
    assert metrics.bottleneck_by_transition(fx.records("C1"), CAL)[-1]["median_working_days"] >= 0


def test_t_a29_lost_cases_and_the_ones_nobody_was_told_about(full):
    recs = full.records("C1", "C2", "C3", "C4", "C6", "A", "B", "C", "D", "E", "F", "G", "H", "Fresh2", "Lc")
    got = metrics.lost_cases(recs, NOW)
    # lost: no colonoscopy and (a lost-contact flag or 30+ idle days): B C D E F G H and Lc.
    # A (0 days), Fresh2 (acknowledged 10 days ago) are not lost; C1-C4 and C6 completed.
    assert got["lost_to_follow_up"] == 8
    # surfaced = any overdue / escalation / watchdog flag / lost-contact event: E, F (flags), H (sweep), Lc (flag).
    assert got["lost_never_surfaced"] == 4  # B, C, D, G
    assert metrics.lost_cases(full.records("A", "Fresh2"), NOW) == {"lost_to_follow_up": 0, "lost_never_surfaced": 0}


def test_t_a29_exactly_30_idle_days_is_lost_and_29_is_not(fx):
    f = Fixture()
    f.create("x", NOW - timedelta(days=30))
    f.create("y", NOW - timedelta(days=29.99))
    assert metrics.lost_cases(f.records("x", "y"), NOW)["lost_to_follow_up"] == 1


def test_t_a29_the_full_bundle(full):
    names = ["C1", "C2", "C3", "C4", "C6", "A", "B", "C", "D", "E", "F", "G", "H", "Fresh2", "Lc", "P1", "P2", "P3"]
    got = metrics.closure_metrics(full.records(*names), NOW, CAL)
    assert got["cases_total"] == 18 and got["cases_closed"] == 4 and got["cases_open"] == 14
    assert got["closures_measured"] == 5 and got["unsafe_closures"] == 0
    assert got["cases_flagged"] == 2  # E and F
    assert set(got) >= {"p50_days", "p90_days", "p50_working_days", "p90_working_days", "open_by_age_bucket",
                        "breaches_by_provider", "bottleneck_by_transition", "lost_never_surfaced", "cases_flagged"}


def test_t_a29_abstention_rate():
    assert metrics.abstention(0, 0) == {"attempts": 0, "abstentions": 0, "rate": None}
    assert metrics.abstention(8, 2) == {"attempts": 8, "abstentions": 2, "rate": 0.25}


# ---- the route ---------------------------------------------------------------------------------------

client = make_client()


@pytest.fixture
def served(monkeypatch, full):
    monkeypatch.setattr(cases, "store", full.store)
    clock.reset()
    clock.advance(days=147)  # NOW
    assert clock.now() == NOW
    yield full
    clock.reset()


def H(role):
    return login_headers(client, role, f"metrics {role}")


def test_t_a29_the_route_returns_the_pure_functions_over_every_case(served):
    r = client.get("/metrics/closure", headers=H("coordinator"))
    assert r.status_code == 200, r.text
    body = r.json()
    records = [metrics.build_record(lc.events, cases.pathway) for lc in served.store.load_cases(cases.pathway)]
    expected = metrics.closure_metrics(records, NOW, CAL)
    assert {k: v for k, v in body.items() if k not in ("sim_time", "abstention", "replay_failures")} == expected
    assert body["sim_time"] == NOW.isoformat() and body["abstention"] is None  # no drafts table in memory
    assert body["replay_failures"] == {"count": 0, "cases": []}
    assert body["cases_total"] == 18 and body["p50_days"] == 24.0 and body["cases_flagged"] == 2
    assert body["open_by_age_bucket"]["90+"] >= 4
    assert client.get("/metrics/closure", headers=H("supervisor")).status_code == 200


@pytest.mark.parametrize("role", ["lab", "primary_care_owner", "specialist_owner", "patient"])
def test_t_a29_other_roles_are_refused(role):
    assert client.get("/metrics/closure", headers=H(role)).status_code == 403
    assert client.get("/metrics/closure").status_code == 401


def test_t_a29_an_empty_store_gives_zeros_and_nulls(monkeypatch):
    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    body = client.get("/metrics/closure", headers=H("coordinator")).json()
    assert body["cases_total"] == 0 and body["p50_days"] is None and body["p90_working_days"] is None
    assert body["open_by_age_bucket"] == {"0-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
    assert body["breaches_by_provider"] == [] and body["bottleneck_by_transition"] == []
    assert body["lost_never_surfaced"] == 0 and body["cases_flagged"] == 0


# ---- the drafts table is optional ----------------------------------------------------------------------


class _Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, answers):
        self.answers = answers
        self.sql = []

    def execute(self, sql, *a):
        self.sql.append(sql)
        for needle, row in self.answers.items():
            if needle in sql:
                if isinstance(row, Exception):
                    raise row
                return _Cursor(row)
        raise AssertionError(sql)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Pool:
    def __init__(self, answers):
        self.conn = _Conn(answers)

    def connection(self):
        return self.conn


def test_t_a29_abstention_counts_degrade_gracefully_when_the_drafts_table_is_absent_or_different():
    from app.store_pg import PostgresEventStore

    assert PostgresEventStore(_Pool({"to_regclass": (None,)})).abstention_counts() is None
    ok = PostgresEventStore(_Pool({"to_regclass": ("drafts",), "count(abstention_reason)": (8, 2)}))
    assert ok.abstention_counts() == (8, 2)
    broken = PostgresEventStore(_Pool({"to_regclass": ("drafts",), "count(abstention_reason)": RuntimeError("no such column")}))
    assert broken.abstention_counts() is None


def test_t_a29_the_route_includes_abstention_when_the_store_has_it(monkeypatch, served):
    monkeypatch.setattr(served.store, "abstention_counts", lambda: (8, 2), raising=False)
    body = client.get("/metrics/closure", headers=H("coordinator")).json()
    assert body["abstention"] == {"attempts": 8, "abstentions": 2, "rate": 0.25}


# ---- Postgres (make test-db) -------------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
def test_t_a29_the_route_over_postgres_agrees_with_the_pure_functions():
    from app.db import create_pool, run_migrations
    from app.store_pg import PostgresEventStore

    pool = create_pool(os.environ["DATABASE_URL"], max_size=4)
    run_migrations(pool)
    pg = PostgresEventStore(pool)
    saved = cases.store
    cases.store = pg
    try:
        f = Fixture()
        f.store = pg  # the same fixture, written to Postgres
        for name, offsets in CLOSED.items():
            f.walk(name, offsets)
        f.add("C6", "reopen", SPEC, T0 + timedelta(days=20), {"reason": "new_finding"})
        clock.reset()
        clock.advance(days=147)
        body = client.get("/metrics/closure", headers=H("coordinator")).json()
        records = [metrics.build_record(lc.events, cases.pathway) for lc in pg.load_cases(cases.pathway) if lc.model]
        assert {k: v for k, v in body.items() if k not in ("sim_time", "abstention", "replay_failures")} == metrics.closure_metrics(records, NOW, CAL)
        assert body["cases_total"] >= 5 and body["closures_measured"] >= 5
        counts = pg.abstention_counts()
        assert body["abstention"] is None if counts is None else body["abstention"]["attempts"] == counts[0]
    finally:
        cases.store = saved
        clock.reset()
        pool.close()
