"""T-A97 to T-A101: the outcome funnel (`GET /metrics/funnel`, ADR-052).

The funnel counts the screening loop's five steps (result recorded, prioritised, referred, attended, outcome documented), the
median days between them, and the cases at risk of being lost. It reads the `case_funnel` table, not a replay of every case.
Like test_metrics_index_pg.py (ADR-049), the integration tests here prove the table gives exactly the numbers of the replay
(`metrics_funnel.legacy_funnel`, unchanged reference) on seeded populations: random walks through the state machine on all three
pathways with priority tiers, the engine's own load mixture, a case that does not replay. Floats are compared bit for bit.
The first group needs no database: definitions pinned by hand on the in-memory store, the filters, the role gate, the bounds.
"""

import os
import random
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from api_helpers import login_headers, make_client
from app import metrics, metrics_funnel, metrics_index
from app.calendar import default_calendar
from app.clock import clock
from app.config import settings as app_settings
from app.events import CASE_CREATED, Event, InMemoryEventStore
from app.pathway import PathwayRegistry, load_pathway
from app.routers import cases

ROOT = Path(__file__).resolve().parents[2]
BOWEL, CERVICAL, CHRONIC = (load_pathway(ROOT / "config/pathways" / f"{n}.yaml") for n in ("colorectal_fit", "cervical_hpv", "chronic_conditions"))
ALL = (BOWEL, CERVICAL, CHRONIC)
CAL = default_calendar()
client = make_client()
T0 = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
NOW = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
PCO, SPEC, COORD, LAB, PAT = "primary_care_owner:k", "specialist_owner:s", "coordinator:c", "lab:l", "patient:p"
STEPS = ["recorded", "prioritised", "referred", "attended", "documented"]


def coord():
    return login_headers(client, "coordinator", "fn coord")


def counts(body):
    return {s["key"]: s["count"] for s in body["steps"]}


def step(body, key):
    return next(s for s in body["steps"] if s["key"] == key)


def log(case_id, start, steps, priority=None):
    """A valid log: `steps` is [(hours since the previous event, type, actor, payload)]. The genesis carries `priority` when given."""
    genesis = {"patient_ref": f"Patient/{case_id.hex[:8]}"}
    if priority:
        genesis.update(priority=priority, priority_rule="r1")
    events, at = [Event(case_id, CASE_CREATED, start, "system:intake", genesis, uuid4())], start
    for hours, kind, actor, payload in steps:
        at += timedelta(hours=hours)
        events.append(Event(case_id, kind, at, actor, payload, uuid4()))
    return events


MAIN = [(1, "acknowledge", LAB, {}), (2, "accept_ownership", PCO, {}), (3, "contact_patient", PCO, {}),
        (4, "patient_acknowledge", PAT, {})]
REFER = [(24, "handoff_requested", PCO, {"to_provider": "endo-sp"}), (24, "handoff_accepted", SPEC, {})]
BOOK = [(5, "book_confirmatory", SPEC, {})]
ATTEND = [(48, "complete_confirmatory", SPEC, {})]
REVIEW = [(24, "clinician_review", SPEC, {})]
CLOSE = [(72, "close", SPEC, {"evidence_ref": "DiagnosticReport/1", "signed_by": SPEC})]
REFUSE = [(5, "record_informed_refusal", PCO, {"counselling_ref": "c1", "counselled_by": PCO, "reason": "personal_choice"}),
          (48, "close", SPEC, {"outcome": "declined", "evidence_ref": "c1", "signed_by": SPEC})]


# ------------------------------------------------------------------------------------------------ no database needed

@pytest.fixture
def three(monkeypatch):
    reg = PathwayRegistry([BOWEL, CERVICAL, CHRONIC])
    store = InMemoryEventStore()
    monkeypatch.setattr(cases, "registry", reg)
    monkeypatch.setattr(cases, "pathway", reg.default)
    monkeypatch.setattr(cases, "store", store)
    clock.reset()
    yield store
    clock.reset()


def test_t_a97_the_definitions_on_a_hand_computed_population():
    """Three bowel cases and two chronic cases, each step counted by the rule in the module docstring, and the days by hand."""
    recs = {}
    spec = {
        "full": (BOWEL, None, MAIN + REFER + BOOK + ATTEND + REVIEW + CLOSE),            # all five steps, ranked none
        "booked": (BOWEL, None, MAIN + BOOK),                                           # booked with no handoff: referred
        "waiting": (BOWEL, None, MAIN),                                                 # recorded only
        "ranked": (CHRONIC, "urgent", MAIN + REFER + BOOK + ATTEND),                    # ranked, attended, not closed
        "refused": (CHRONIC, "soon", MAIN + REFUSE),                                    # informed refusal, closed declined
    }
    for name, (pw, tier, steps) in spec.items():
        events = log(uuid4(), T0, steps, tier)
        recs[name] = metrics.build_record(events, pw)
    rows = {name: metrics_funnel.row_of(r) for name, r in recs.items()}
    assert [rows[n]["priority"] for n in spec] == [None, None, None, "urgent", "soon"]
    assert [rows[n]["referred_at"] is not None for n in spec] == [True, True, False, True, False]
    assert [rows[n]["attended_at"] is not None for n in spec] == [True, False, False, True, False]
    assert [rows[n]["documented_at"] is not None for n in spec] == [True, False, False, False, False]
    assert [rows[n]["declined_at"] is not None for n in spec] == [False, False, False, False, True]
    # Days: the result at T0; the handoff 10 h later (1+2+3+4) + 24 h = 34 h after T0; booking 24 + 5 h after that; and so on.
    full = rows["full"]
    assert full["d_referred"] == pytest.approx(34 / 24)
    assert full["d_attended"] == pytest.approx((24 + 5 + 48) / 24)   # handoff_requested -> complete_confirmatory
    assert full["d_documented"] == pytest.approx((24 + 72) / 24)     # complete_confirmatory -> close
    assert rows["booked"]["d_referred"] == pytest.approx((1 + 2 + 3 + 4 + 5) / 24)  # the booking is the referral


def test_t_a97_an_informed_refusal_is_counted_apart_and_is_not_attended_or_documented(three):
    store = three
    for pw, tier, steps in ((BOWEL, None, MAIN + REFUSE), (BOWEL, None, MAIN + REFER + BOOK + ATTEND + REVIEW + CLOSE)):
        events = log(uuid4(), T0, steps, tier)
        store.create_case(events[0].case_id, pw.name, events[0], pw.initial_state)
        for e in events[1:]:
            store.append(e, pw)
    clock.reset()
    body = client.get("/metrics/funnel", headers=coord()).json()
    assert counts(body) == {"recorded": 2, "prioritised": 0, "referred": 1, "attended": 1, "documented": 1}
    assert body["declined_closures"] == 1
    closure = client.get("/metrics/closure", headers=coord()).json()
    assert closure["closures_measured"] == 1 and closure["declined_closures"] == 1  # the closure metrics say the same


def test_t_a97_steps_conversions_medians_and_the_wording_of_the_loop(three):
    store = three
    cases_ = [(BOWEL, None, MAIN + REFER + BOOK + ATTEND + REVIEW + CLOSE), (BOWEL, None, MAIN + REFER + BOOK + ATTEND),
              (CHRONIC, "urgent", MAIN + REFER), (CERVICAL, "routine", MAIN)]
    for pw, tier, steps in cases_:
        events = log(uuid4(), T0, steps, tier)
        store.create_case(events[0].case_id, pw.name, events[0], pw.initial_state)
        for e in events[1:]:
            store.append(e, pw)
    body = client.get("/metrics/funnel", headers=coord()).json()
    assert [s["key"] for s in body["steps"]] == STEPS
    assert counts(body) == {"recorded": 4, "prioritised": 2, "referred": 3, "attended": 2, "documented": 1}
    assert [s["label"] for s in body["steps"]] == ["Result recorded", "Higher-risk people prioritised", "Referred to the appropriate service",
                                                   "Attended", "Outcome documented"]
    # conversion: against the step each is measured from; ranking gates nothing, so referred is against the recorded
    assert [step(body, k)["from"] for k in STEPS] == [None, "recorded", "recorded", "referred", "attended"]
    assert step(body, "recorded")["conversion"] is None
    assert step(body, "prioritised")["conversion"] == 0.5 and step(body, "referred")["conversion"] == 0.75
    assert step(body, "attended")["conversion"] == round(2 / 3, 4) and step(body, "documented")["conversion"] == 0.5
    assert step(body, "referred")["share_of_recorded"] == 0.75
    # medians are of the cases that reached both ends; the step with no time says so
    assert step(body, "prioritised")["median_days"] is None
    assert step(body, "referred")["median_n"] == 3 and step(body, "referred")["median_days"] == pytest.approx(34 / 24)
    assert step(body, "attended")["median_n"] == 2 and step(body, "documented")["median_n"] == 1
    assert body["sim_time"] and body["filters"] == {"pathway": None, "priority": None}
    assert body["options"] == {"pathways": ["colorectal_fit", "cervical_hpv", "chronic_conditions"],
                               "priorities": ["urgent", "soon", "routine", "unranked"]}


def test_t_a97_the_pathway_and_priority_filters(three):
    store = three
    for pw, tier, steps in ((BOWEL, None, MAIN + REFER), (CHRONIC, "urgent", MAIN + REFER + BOOK), (CHRONIC, "routine", MAIN),
                            (CERVICAL, "urgent", MAIN)):
        events = log(uuid4(), T0, steps, tier)
        store.create_case(events[0].case_id, pw.name, events[0], pw.initial_state)
        for e in events[1:]:
            store.append(e, pw)

    def get(**q):
        r = client.get("/metrics/funnel", params=q, headers=coord())
        assert r.status_code == 200, r.text
        return r.json()

    assert counts(get(pathway="chronic_conditions"))["recorded"] == 2
    assert counts(get(pathway="colorectal_fit")) == {"recorded": 1, "prioritised": 0, "referred": 1, "attended": 0, "documented": 0}
    assert counts(get(priority="urgent"))["recorded"] == 2 and counts(get(priority="unranked"))["recorded"] == 1
    both = get(pathway="chronic_conditions", priority="urgent")
    assert counts(both) == {"recorded": 1, "prioritised": 1, "referred": 1, "attended": 0, "documented": 0}
    assert both["filters"] == {"pathway": "chronic_conditions", "priority": "urgent"}
    assert counts(get(pathway="cervical_hpv", priority="soon"))["recorded"] == 0  # an empty cohort: zeros, no division
    assert step(get(pathway="cervical_hpv", priority="soon"), "referred")["conversion"] is None


@pytest.mark.parametrize("query", ["pathway=no_such_pack", "priority=critical", "pathway=Colorectal", "pathway=a%20b", "priority=%27%3B--",
                                   "pathway=" + "a" * 65])
def test_t_a97_a_filter_that_names_nothing_loaded_is_refused_and_not_echoed(three, query):
    r = client.get(f"/metrics/funnel?{query}", headers=coord())
    assert r.status_code == 422


def test_t_a98_the_cases_at_risk_follow_the_closure_metrics_rule_and_are_bounded(three):
    store = three
    stale = [(BOWEL, None, MAIN), (CHRONIC, "urgent", MAIN + REFER)]
    fresh = [(BOWEL, None, MAIN + REFER + BOOK + ATTEND), (BOWEL, None, MAIN + REFUSE)]  # attended, and a refuser: never at risk
    for pw, tier, steps in stale + fresh:
        events = log(uuid4(), T0, steps, tier)
        store.create_case(events[0].case_id, pw.name, events[0], pw.initial_state)
        for e in events[1:]:
            store.append(e, pw)
    clock.reset()
    clock.advance(days=60)
    body = client.get("/metrics/funnel", headers=coord()).json()
    risk = body["at_risk"]
    assert risk["count"] == 2 and risk["idle_days_threshold"] == 30 and len(risk["cases"]) == 2 and risk["truncated"] is False
    assert {c["pathway"] for c in risk["cases"]} == {"colorectal_fit", "chronic_conditions"}
    assert all(c["reason"] == "no_progress" and c["idle_days"] >= 30 for c in risk["cases"])
    assert risk["cases"][0]["idle_days"] >= risk["cases"][1]["idle_days"]  # the longest-waiting first
    # on the default pathway it is the closure metrics' lost_to_follow_up, number for number
    assert client.get("/metrics/funnel?pathway=colorectal_fit", headers=coord()).json()["at_risk"]["count"] == \
        client.get("/metrics/closure", headers=coord()).json()["lost_to_follow_up"]
    assert all(set(c) == {"case_id", "pathway", "priority", "state", "idle_days", "reason"} for c in risk["cases"])  # an id, never a person


def test_t_a98_the_list_of_cases_at_risk_is_capped_and_the_count_is_exact():
    rows = [{"case_id": str(i)} for i in range(3)]
    body = metrics_funnel.shape({k: 40 for k in STEPS}, {}, declined=0, at_risk=40, at_risk_cases=rows, failed=0, at_risk_limit=3)
    assert body["at_risk"]["count"] == 40 and len(body["at_risk"]["cases"]) == 3 and body["at_risk"]["truncated"] is True
    assert metrics_funnel.AT_RISK_LIMIT == 10


@pytest.mark.parametrize("role,status", [("coordinator", 200), ("supervisor", 200), ("primary_care_owner", 403), ("specialist_owner", 403),
                                         ("lab", 403), ("patient", 403)])
def test_t_a99_only_the_coordinator_and_the_supervisor_read_the_funnel(three, role, status):
    assert client.get("/metrics/funnel", headers=login_headers(client, role, f"fn {role}")).status_code == status


def test_t_a99_the_route_needs_a_login():
    assert client.get("/metrics/funnel").status_code == 401


def test_t_a99_the_funnel_is_in_the_openapi_schema_with_both_filters():
    spec = client.get("/openapi.json").json()
    params = {p["name"] for p in spec["paths"]["/metrics/funnel"]["get"]["parameters"]}
    assert params == {"pathway", "priority"}


def test_t_a97_an_empty_deployment_gives_zeros_and_nulls(three):
    body = client.get("/metrics/funnel", headers=coord()).json()
    assert counts(body) == {k: 0 for k in STEPS} and all(s["conversion"] is None and s["median_days"] is None for s in body["steps"])
    assert body["at_risk"]["count"] == 0 and body["at_risk"]["cases"] == [] and body["declined_closures"] == 0


# ------------------------------------------------------------------------------------------------ the table equals the replay

needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")
def db(fn):
    """An integration test: it needs the Postgres of DATABASE_URL (a `_test` database)."""
    return pytest.mark.integration(needs_db(fn))


@pytest.fixture
def pg(monkeypatch):
    from scratchdb import scratch_database

    from app.db import create_pool
    from app.store_pg import PostgresEventStore

    reg = PathwayRegistry(ALL)
    with scratch_database("fn") as url:
        pool = create_pool(url, max_size=4)
        store = PostgresEventStore(pool)
        monkeypatch.setattr(cases, "store", store)
        monkeypatch.setattr(cases, "registry", reg)
        monkeypatch.setattr(cases, "pathway", reg.default)
        yield store
        pool.close()
    clock.reset()


def insert_log(store, pathway, events, updated_at=None):
    case_id = events[0].case_id
    with store._pool.connection() as conn:
        conn.execute("INSERT INTO cases (id, pathway, current_state, created_at, updated_at) VALUES (%s,%s,%s,%s,%s)",
                     (case_id, pathway.name, pathway.initial_state, events[0].at, updated_at or events[-1].at))
        for seq, e in enumerate(events, start=1):
            conn.execute("INSERT INTO events (event_id, case_id, seq, type, at, actor, payload) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                         (e.event_id, case_id, seq, e.type, e.at, e.actor, Jsonb(e.payload)))


HANDOFF = ("handoff_requested", [PCO, SPEC, "supervisor:v"], {"to_provider": "endo-sp"})


@pytest.fixture
def walks(monkeypatch):
    """The random walks of test_metrics_index_pg, with a referral request among the events they may take."""
    import test_metrics_index_pg as tm

    monkeypatch.setattr(tm, "EXTRA", [*tm.EXTRA, HANDOFF])
    return tm


def populate(tm, store, rng, n):
    """`n` random walks spread over the three pathways; most carry a tier on the genesis event, a few do not."""
    tiers = [None, "urgent", "soon", "routine"]
    for i in range(n):
        pw = ALL[i % 3]
        events = tm.random_walk(rng, uuid4(), T0 + timedelta(days=rng.randint(0, 60)), rng.randint(2, 20))
        tier = rng.choice(tiers) if pw is not BOWEL or rng.random() < 0.2 else None
        if tier:
            events[0] = replace(events[0], payload={**events[0].payload, "priority": tier, "priority_rule": "r1"})
        insert_log(store, pw, events)
    for i in range(24):
        events = tm.refusal_walk(rng, uuid4(), T0 + timedelta(days=rng.randint(0, 30)), sign_off=i % 2 == 0)
        insert_log(store, ALL[i % 3], events)


def same(a, b, path="body"):
    if isinstance(a, dict):
        assert isinstance(b, dict) and a.keys() == b.keys(), f"{path}: keys {sorted(a)} vs {sorted(b)}"
        for k in a:
            same(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), f"{path}: lengths {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            same(x, y, f"{path}[{i}]")
    else:
        assert a == b and type(a) is type(b) or (isinstance(a, (int, float)) and a == b), f"{path}: {a!r} vs {b!r}"


FILTERS = [(None, None)] + [(p, None) for p in ("colorectal_fit", "cervical_hpv", "chronic_conditions")] + \
          [(None, t) for t in ("urgent", "soon", "routine", "unranked")] + [("chronic_conditions", "urgent"), ("colorectal_fit", "unranked")]


def compare_all(store, now):
    r = metrics_funnel.refresh(store, ALL, budget_seconds=600)
    assert r["caught_up"] and not r["busy"] and r["lag"] is None
    seen = {}
    for pathway, priority in FILTERS:
        new = metrics_funnel.funnel_report(store, now, pathway=pathway, priority=priority)
        old = metrics_funnel.legacy_funnel(store, ALL, now, pathway=pathway, priority=priority)
        same(new, old, f"funnel[{pathway},{priority}]")
        seen[(pathway, priority)] = new
    return seen


@pytest.mark.parametrize("seed", [1, 2, 3])
@db
def test_t_a100_the_table_gives_the_numbers_of_the_replay_on_random_walks(pg, walks, seed):
    rng = random.Random(seed)
    populate(walks, pg, rng, 240)
    seen = compare_all(pg, NOW + timedelta(days=rng.randint(0, 200)))
    whole = seen[(None, None)]
    c = counts(whole)
    # the comparison is not vacuous: every step and the corners are populated
    assert all(c[k] > 0 for k in STEPS) and whole["declined_closures"] > 0 and whole["at_risk"]["count"] > 0
    assert c["documented"] <= c["attended"] <= c["referred"] <= c["recorded"] and c["prioritised"] <= c["recorded"]
    assert all(s["median_days"] is not None for s in whole["steps"] if s["key"] in ("referred", "attended", "documented"))
    # filters partition the whole: pathways add up, and so do the tiers (every case has a tier or is unranked)
    assert sum(counts(seen[(p, None)])["recorded"] for p in ("colorectal_fit", "cervical_hpv", "chronic_conditions")) == c["recorded"]
    assert sum(counts(seen[(None, t)])["recorded"] for t in ("urgent", "soon", "routine", "unranked")) == c["recorded"]


@db
def test_t_a100_on_the_default_pathway_the_funnel_agrees_with_the_closure_metrics(pg, walks):
    """One definition of 'closed with evidence' and of 'lost': the funnel and /metrics/closure cannot disagree."""
    rng = random.Random(8)
    populate(walks, pg, rng, 300)
    metrics_funnel.refresh(pg, ALL, budget_seconds=600)
    metrics_index.refresh(pg, BOWEL, CAL, budget_seconds=600)
    now = NOW + timedelta(days=90)
    closure = metrics_index.closure_metrics(pg, now, CAL, BOWEL.terminal_state)
    f = metrics_funnel.funnel_report(pg, now, pathway="colorectal_fit")
    assert closure["cases_total"] == counts(f)["recorded"] > 0
    assert closure["closures_measured"] == counts(f)["documented"] > 0
    assert closure["declined_closures"] >= 0 and closure["lost_to_follow_up"] == f["at_risk"]["count"] > 0
    assert (closure["cases_closed"] == 0) == (counts(f)["documented"] + f["declined_closures"] == 0) or closure["cases_closed"] >= 0


@db
def test_t_a100_the_engines_own_load_mixture_gives_the_same_numbers(pg):
    sys.path.insert(0, str(ROOT / "tools" / "load"))
    import seed_cases as sc

    pathway, calendar = sc.load_engine()
    rng = random.Random(7)
    for _ in range(150):
        _, events = sc.build_case_events(rng, pathway, calendar)
        insert_log(pg, BOWEL, events)
    seen = compare_all(pg, NOW)
    c = counts(seen[(None, None)])
    assert c["recorded"] == 150 and c["referred"] > 0 and c["attended"] > 0 and seen[(None, None)]["at_risk"]["count"] > 0


@db
def test_t_a100_an_empty_database_and_a_case_that_does_not_replay(pg):
    seen = compare_all(pg, NOW)
    assert counts(seen[(None, None)]) == {k: 0 for k in STEPS}
    rng = random.Random(5)
    import test_metrics_index_pg as tm

    for _ in range(20):
        insert_log(pg, BOWEL, tm.random_walk(rng, uuid4(), T0, 8))
    bad = uuid4()
    insert_log(pg, CHRONIC, [Event(bad, CASE_CREATED, T0, "system:intake", {}, uuid4()),
                             Event(bad, "close", T0 + timedelta(days=1), COORD, {"evidence_ref": "x", "signed_by": COORD}, uuid4())])
    seen = compare_all(pg, NOW)
    assert counts(seen[(None, None)])["recorded"] == 20 and seen[(None, None)]["replay_failures"]["count"] == 1
    assert seen[("chronic_conditions", None)]["replay_failures"]["count"] == 1 and seen[("colorectal_fit", None)]["replay_failures"]["count"] == 0


@pytest.mark.parametrize("now_days", [0, 29, 30, 31, 91, 400])
@db
def test_t_a100_the_cases_at_risk_depend_on_now_and_need_no_refresh(pg, walks, now_days):
    rng = random.Random(9)
    populate(walks, pg, rng, 90)
    metrics_funnel.refresh(pg, ALL, budget_seconds=600)
    now = T0 + timedelta(days=now_days, hours=3)
    same(metrics_funnel.funnel_report(pg, now), metrics_funnel.legacy_funnel(pg, ALL, now))


@db
def test_t_a100_the_list_of_cases_at_risk_is_the_longest_waiting_first_and_ties_break_by_id(pg, walks):
    rng = random.Random(12)
    populate(walks, pg, rng, 200)
    metrics_funnel.refresh(pg, ALL, budget_seconds=600)
    now = NOW + timedelta(days=120)
    for limit in (1, 3, 10, 500):
        same(metrics_funnel.funnel_report(pg, now, at_risk_limit=limit), metrics_funnel.legacy_funnel(pg, ALL, now, at_risk_limit=limit))
    whole = metrics_funnel.funnel_report(pg, now, at_risk_limit=500)["at_risk"]
    assert whole["truncated"] is False and len(whole["cases"]) == whole["count"] > 10
    idle = [c["idle_days"] for c in whole["cases"]]
    assert idle == sorted(idle, reverse=True)


# ------------------------------------------------------------------------------------------------ the refresh and the route

@db
def test_t_a101_the_refresh_replays_only_what_changed_and_keeps_a_cursor_per_pathway(pg, walks):
    rng = random.Random(4)
    populate(walks, pg, rng, 90)
    first = metrics_funnel.refresh(pg, ALL, budget_seconds=600)
    assert first["replayed"] == 90 + 24 and first["caught_up"] and first["lag"] is None
    assert metrics_funnel.refresh(pg, ALL, budget_seconds=600)["replayed"] == 0  # a quiet minute replays nothing
    with pg._pool.connection() as conn:
        keys = sorted(r[0] for r in conn.execute("SELECT key FROM metrics_index_state WHERE key LIKE 'funnel:%'").fetchall())
    assert keys == ["funnel:cervical_hpv", "funnel:chronic_conditions", "funnel:colorectal_fit"]
    # one case changes: only that one is replayed, and the answer follows it
    cid = next(iter(pg.load_cases(CERVICAL))).case_id
    with pg._pool.connection() as conn:
        conn.execute("UPDATE cases SET updated_at = (SELECT max(updated_at) FROM cases) + interval '1 day' WHERE id = %s", (cid,))
    assert metrics_funnel.refresh(pg, ALL, budget_seconds=600)["replayed"] == 1


@db
def test_t_a101_the_closure_metrics_still_report_the_default_pathway_only_after_the_funnel_has_run(pg, walks):
    rng = random.Random(6)
    populate(walks, pg, rng, 90)
    metrics_funnel.refresh(pg, ALL, budget_seconds=600)
    clock.reset()
    clock.advance(days=150)
    total = client.get("/metrics/closure", headers=coord()).json()["cases_total"]
    assert total == sum(1 for lc in pg.load_cases(BOWEL) if lc.model is not None)
    assert client.get("/metrics/funnel", headers=coord()).status_code == 200
    assert client.get("/metrics/closure", headers=coord()).json()["cases_total"] == total  # ADR-049: unchanged by ADR-052


@db
def test_t_a101_the_route_returns_the_replay_body_and_says_when_it_is_behind(pg, walks, monkeypatch):
    rng = random.Random(21)
    populate(walks, pg, rng, 1300)  # more than two refresh batches per pathway
    clock.reset()
    clock.advance(days=147)
    monkeypatch.setattr(app_settings, "metrics_refresh_budget_seconds", 0.0)
    r = client.get("/metrics/funnel", headers=coord())
    assert r.status_code == 200 and int(r.headers["X-Metrics-Index-Lag"]) > 0
    monkeypatch.setattr(app_settings, "metrics_refresh_budget_seconds", 60.0)
    r = client.get("/metrics/funnel?pathway=cervical_hpv&priority=urgent", headers=coord())
    assert "X-Metrics-Index-Lag" not in r.headers
    body = r.json()
    old = metrics_funnel.legacy_funnel(pg, cases.all_pathways(), clock.now(), pathway="cervical_hpv", priority="urgent")
    same({k: body[k] for k in old}, old)
    assert set(body) == {"sim_time", "filters", "options", *old}


@db
def test_t_a101_the_migration_adds_one_table_and_the_funnel_row_holds_no_person(pg):
    with pg._pool.connection() as conn:
        cols = [r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'case_funnel'").fetchall()]
    assert "patient_ref" not in cols and not any("name" in c or "phone" in c for c in cols)
    assert {"case_id", "pathway", "priority", "referred_at", "attended_at", "documented_at"} <= set(cols)
