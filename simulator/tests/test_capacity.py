"""Ticket B19: finite colonoscopy capacity and costed escalations. Hand-computed arithmetic for the slot calendar and the
staff queue, then the properties the ticket asks for (determinism, no double booking, utilisation never above 1, queue
conservation) on real runs, and the committed capacity.json / results.md."""

import json
import math
from datetime import date
from pathlib import Path

import pytest

from simulator.sim import capacity as C
from simulator.sim.calendar import WorkingCalendar
from simulator.sim.capacity_eval import ALL_KEYS, CAPACITY_KEYS, DEFAULT_CAPACITY, evaluate_capacity, write_capacity
from simulator.sim.capacity_report import BEGIN, END, render_section
from simulator.sim.effort import ACTIONS
from simulator.sim.params import CapacityParams, ParamError, load_params
from simulator.sim.runner import context_from_params, make_patients, metrics_of, run_arm

ROOT = Path(__file__).resolve().parents[2]
P, CONS = ROOT / "simulator" / "params.yaml", ROOT / "simulator" / "params.conservative.yaml"
RESULTS = ROOT / "docs" / "results"
HUGE = 1000.0


def _ctx(stack, **overrides):
    params = load_params(*stack, DEFAULT_CAPACITY, warn=False)
    for k, v in overrides.items():
        params = params.with_value(k.replace("__", "."), v)
    return context_from_params(params)


def _cp(**kw) -> CapacityParams:
    base = dict(providers=1, slots_per_session=6, sessions_per_week_per_1000_cases=5, scale=1.0, dna_backfill_prob=0.0,
                utilisation_window_start_day=0, utilisation_window_end_day=14, supervisor_minutes_per_day_per_1000_cases=30.0,
                coordinator_minutes_per_day_per_1000_cases=30.0, staffing_scale=1.0, accept_minutes=2.0, contact_minutes=8.0)
    base.update(kw)
    return CapacityParams(**base)


@pytest.fixture(scope="module")
def cal():
    return WorkingCalendar(date(2026, 1, 5), (5, 6))  # a Monday; Saturday and Sunday off


# ---- parameters ----------------------------------------------------------------------------------------------------------------

def test_params_yaml_is_untouched_by_the_capacity_layer():
    plain = load_params(P, warn=False)
    layered = load_params(P, DEFAULT_CAPACITY, warn=False)
    assert not any(k.startswith("capacity.") for k in plain.keys())
    assert {k: v for k, v in layered._p.items() if not k.startswith("capacity.")} == plain._p
    assert context_from_params(plain).mp.capacity is None
    assert context_from_params(layered).mp.capacity is not None


def test_minutes_match_the_effort_model():
    mins = {a.id: a.minutes for a in ACTIONS}
    cp = _ctx([P]).mp.capacity
    assert cp.accept_minutes == mins["O_ACCEPT_ESC_SUPERVISOR"] == mins["O_ACCEPT_ESC_COORDINATOR"]
    assert cp.contact_minutes == pytest.approx(mins["O_SUPERVISOR_CALL"] + mins["O_APPROVE_DRAFT"])
    assert mins["O_SUPERVISOR_CALL"] == mins["O_COORDINATOR_CALL"]


def test_bad_capacity_parameters_are_rejected():
    with pytest.raises(ParamError):
        _ctx([P], capacity__dna_backfill_prob=1.5)
    with pytest.raises(ParamError):
        _ctx([P], capacity__scale=0.0)


# ---- the slot calendar, by hand --------------------------------------------------------------------------------------------------

def test_daily_supply_averages_its_rate_and_is_an_integer(cal):
    # 5 sessions a week per 1000 cases x 6 slots, 1000 cases, 5 working days: 6 slots a day on one provider
    sb = C.SlotBook(_cp(), cal, 1000, 5)
    assert [sb.capacity_on(0, d) for d in range(0, 7)] == [6, 6, 6, 6, 6, 0, 0]
    # 2.5 slots a day: 5 slots every two working days, never fractional
    sb = C.SlotBook(_cp(sessions_per_week_per_1000_cases=2.0833333333333335), cal, 1000, 5)  # 12.5 slots/week = 2.5/day
    caps = [sb.capacity_on(0, d) for d in (0, 1, 2, 3, 4, 7, 8, 9, 10, 11)]
    assert all(isinstance(x, int) for x in caps) and sum(caps) == 25 and set(caps) <= {2, 3}
    # supply scales with the cohort: twice the cases, twice the slots
    assert sum(C.SlotBook(_cp(), cal, 2000, 5).capacity_on(0, d) for d in range(5)) == 2 * 6 * 5


def test_booking_takes_the_earliest_free_slot_on_or_after_the_request(cal):
    sb = C.SlotBook(_cp(sessions_per_week_per_1000_cases=1.0), cal, 1000, 5)  # 6 slots a week -> pattern floor-diffs of 1.2/day
    per_day = [sb.capacity_on(0, d) for d in range(5)]
    assert sum(per_day) == 6
    # fill day 1 (Tuesday) completely, then ask for it again: the next day with room
    first_with_room = None
    slots = []
    for case in range(10):
        slots.append(sb.book(case, 0, 1.4, 0.0))
    days = [math.floor(t) for t in slots]
    assert days == sorted(days) and days[0] == 1 and slots[0] == 1.4  # same day keeps the time of day
    for d in set(days):
        assert days.count(d) <= sb.capacity_on(0, d)
    assert all(sb.cal.is_working_index(d) for d in days)  # never a weekend
    assert sb.max_load() <= 1.0


def test_a_request_for_a_weekend_day_never_lands_on_it(cal):
    sb = C.SlotBook(_cp(), cal, 1000, 5)
    t = sb.book(1, 0, 5.35, 0.0)  # Saturday
    assert math.floor(t) == 7 and t == pytest.approx(7.35)  # Monday, time of day kept


def test_cancel_frees_the_slot_and_a_no_show_does_not(cal):
    sb = C.SlotBook(_cp(sessions_per_week_per_1000_cases=0.8333333333333334), cal, 1000, 5)  # 1 slot a day
    assert [sb.capacity_on(0, d) for d in range(5)] == [1, 1, 1, 1, 1]
    a = sb.book(1, 0, 0.5, 0.0)
    b = sb.book(2, 0, 0.5, 0.0)
    assert (math.floor(a), math.floor(b)) == (0, 1)
    sb.cancel(1)  # day 0 is free again
    assert math.floor(sb.book(3, 0, 0.5, 1.0)) == 0
    sb.resolve(2, "dna_empty")  # the slot stays used
    assert math.floor(sb.book(4, 0, 0.5, 1.0)) == 2
    assert sb.booked[(0, 1)] == 1


def test_second_booking_by_the_same_case_supersedes_the_first(cal):
    sb = C.SlotBook(_cp(), cal, 1000, 5)
    sb.book(7, 0, 1.0, 0.0)
    sb.book(7, 0, 2.0, 0.0)
    assert sum(sb.booked.values()) == 1 and [b.outcome for b in sb.log] == ["superseded", "booked"]


def test_utilisation_arithmetic(cal):
    sb = C.SlotBook(_cp(sessions_per_week_per_1000_cases=0.8333333333333334, utilisation_window_end_day=7), cal, 1000, 5)
    for case, outcome in ((1, "attended"), (2, "dna_backfilled"), (3, "dna_empty")):
        sb.book(case, 0, 0.0, 0.0)
        sb.resolve(case, outcome)
    m = sb.summary(1000)
    assert m["slots_offered"] == 5 and m["slots_booked"] == 3
    assert m["slot_utilisation"] == pytest.approx(3 / 5) and m["slot_used_share"] == pytest.approx(2 / 5)
    assert m["dna_backfilled_share"] == pytest.approx(0.5)


def test_no_slot_in_ten_years_is_an_error(cal):
    sb = C.SlotBook(_cp(sessions_per_week_per_1000_cases=0.0001), cal, 1, 5)
    with pytest.raises(ParamError):
        sb.book(1, 0, 0.0, 0.0)


# ---- the staff queue, by hand ----------------------------------------------------------------------------------------------------

def test_staff_queue_fifo_and_overflow_to_the_next_working_day(cal):
    # budget 30 minutes a day (30 per 1000 cases, 1000 cases); jobs of 10 minutes: three a day
    q = C.StaffQueue(_cp(), cal, 1000)
    jobs = [q.request(C.SUPERVISOR, 0.4, 10.0) for _ in range(7)]
    starts = [j.start_t for j in jobs]
    assert starts[:3] == [0.4, 0.4, 0.4]  # served the day they arrive: no delay
    assert starts[3:6] == [pytest.approx(1.35)] * 3 and starts[6] == pytest.approx(2.35)
    assert q.used[(C.SUPERVISOR, 0)] == 30 and q.used[(C.SUPERVISOR, 1)] == 30 and q.used[(C.SUPERVISOR, 2)] == 10
    # roles have their own budgets
    assert q.request(C.COORDINATOR, 0.5, 10.0).start_t == 0.5


def test_staff_queue_skips_the_weekend_and_resets_each_day(cal):
    q = C.StaffQueue(_cp(), cal, 1000)
    for _ in range(3):
        q.request(C.SUPERVISOR, 4.2, 10.0)  # Friday, budget gone
    j = q.request(C.SUPERVISOR, 4.3, 10.0)
    assert j.start_t == pytest.approx(7.35)  # Monday
    late = q.request(C.SUPERVISOR, 30.2, 10.0)  # a quiet day: no backlog, no delay
    assert late.start_t == 30.2


def test_staff_queue_request_on_a_weekend_waits_for_monday_at_no_extra_charge(cal):
    q = C.StaffQueue(_cp(), cal, 1000)
    j = q.request(C.SUPERVISOR, 5.5, 10.0)
    assert j.start_t == pytest.approx(7.35)  # next_working_t
    assert q.used[(C.SUPERVISOR, 7)] == 10


def test_a_budget_shorter_than_one_job_is_raised_to_one_job(cal):
    q = C.StaffQueue(_cp(supervisor_minutes_per_day_per_1000_cases=5.0), cal, 1000)
    assert q.budget[C.SUPERVISOR] == 10.0 and q.budget[C.COORDINATOR] == 30.0
    a, b = q.request(C.SUPERVISOR, 0.4, 10.0), q.request(C.SUPERVISOR, 0.4, 10.0)
    assert a.start_t == 0.4 and b.start_t == pytest.approx(1.35)  # one job a day
    assert max(q.used.values()) <= q.budget[C.SUPERVISOR]


def test_queue_length_and_delay_metrics(cal):
    q = C.StaffQueue(_cp(utilisation_window_end_day=3), cal, 1000)
    jobs = [q.request(C.SUPERVISOR, 0.4, 10.0) for _ in range(5)]  # three served on day 0, two wait for day 1
    for j in jobs:
        j.state = "acted"
    m = q.summary(1000, [0, 1, 2])
    assert m["esc_delay_mean"] == pytest.approx((2 * 0.95) / 5)
    assert m["esc_delayed_share"] == pytest.approx(2 / 5)
    # at the end of day 0 (t = 1.0) two jobs are waiting; at the end of days 1 and 2 none
    assert m["esc_queue_max"] == 2 and m["esc_queue_mean"] == pytest.approx(2 / 3)
    assert m["esc_supervisor_utilisation"] == pytest.approx(50 / 90)


# ---- properties of real runs -----------------------------------------------------------------------------------------------------------

N = 400


@pytest.fixture(scope="module", params=["upper", "literature"])
def runs(request):
    stack = [P] if request.param == "upper" else [P, CONS]
    ctx = _ctx(stack)
    pts = make_patients(ctx, N, 11)
    request.node.stack = stack
    return ctx, {arm: run_arm(ctx, arm, N, 360, 11, pts) for arm in ("baseline", "orchestrated")}


def test_runs_are_deterministic(runs):
    ctx, first = runs
    pts = make_patients(ctx, N, 11)
    for arm, res in first.items():
        again = run_arm(ctx, arm, N, 360, 11, pts)
        assert again.event_hash == res.event_hash
        assert again.capacity.metrics() == res.capacity.metrics()


def test_no_double_booking_and_utilisation_at_most_one(runs):
    ctx, res = runs
    for arm, r in res.items():
        sb = r.capacity.slots
        held = [b for b in sb.log if b.outcome not in ("cancelled", "superseded")]
        per = {}
        for b in held:
            per[(b.provider, b.day)] = per.get((b.provider, b.day), 0) + 1
            assert b.slot_t >= b.earliest_t - 1e-9 and sb.cal.is_working_index(b.day)
        for (p, d), n in per.items():
            assert n <= sb.capacity_on(p, d), (arm, p, d)
        # a case holds one slot at a time: its next booking is requested no earlier than the previous one's day
        by_case = {}
        for b in held:
            by_case.setdefault(b.case, []).append(b)
        for bs in by_case.values():
            bs.sort(key=lambda b: b.request_t)
            for x, y in zip(bs, bs[1:]):
                assert y.request_t >= x.slot_t - 1e-9
        assert sb.max_load() <= 1.0
        assert not any(b.outcome == "superseded" for b in sb.log)  # the engine never books twice
        m = r.capacity.metrics()
        assert 0 < m["slot_utilisation"] <= 1.0 and m["slot_used_share"] <= m["slot_utilisation"]
        assert all(b.outcome != "booked" or b.slot_t > 0 for b in sb.log)


def test_every_appointment_in_the_log_has_a_slot(runs):
    ctx, res = runs
    for arm, r in res.items():
        attended = sum(1 for c in r.cases for e in c.log if e.type == "complete_confirmatory")
        n_att = sum(1 for b in r.capacity.slots.log if b.outcome == "attended")
        assert attended == n_att


def test_escalation_queue_conservation(runs):
    ctx, res = runs
    r = res["orchestrated"]
    st = r.capacity.staff
    states = [j.state for j in st.jobs]
    assert len(st.jobs) == states.count("acted") + states.count("stale") + states.count("pending")
    # every ladder escalation that was worked is exactly one `escalated` event; coalesced rungs never became jobs
    esc_events = sum(1 for c in r.cases for e in c.log if e.type == "escalated")
    assert esc_events == sum(1 for j in st.jobs if j.kind == "rung" and j.state == "acted")
    # nothing is served before it is requested; first come first served within a role
    for role in C.ROLES:
        js = [j for j in st.jobs if j.role == role]
        assert all(j.start_t >= j.request_t - 1e-9 for j in js)
        starts = [j.start_t for j in js]
        assert starts == sorted(starts)
        for (rl, d), used in st.used.items():
            if rl == role:
                assert used <= st.budget[role] + 1e-6
    # the baseline arm has no escalations and so no jobs
    assert res["baseline"].capacity.staff.jobs == []


def test_capacity_limits_only_ever_delay_the_baseline(runs):
    ctx, res = runs
    stack = [P] if "conservative" not in " ".join(ctx.params.files) else [P, CONS]
    free = _ctx(stack, capacity__scale=HUGE, capacity__staff__scale=HUGE)
    base_free = run_arm(free, "baseline", N, 360, 11, make_patients(free, N, 11))
    done_free = sum(1 for r in base_free.records if r.t_complete is not None and r.t_complete <= 90)
    done_cap = sum(1 for r in res["baseline"].records if r.t_complete is not None and r.t_complete <= 90)
    assert done_cap <= done_free  # a full calendar can only push colonoscopies later, within a fixed horizon


@pytest.mark.parametrize("stack", [[P], [P, CONS]])
def test_unlimited_capacity_reproduces_the_unconstrained_event_log(stack):
    plain = context_from_params(load_params(*stack, warn=False))
    free = _ctx(stack, capacity__scale=HUGE, capacity__staff__scale=HUGE)
    pts = make_patients(plain, N, 5)
    for arm in ("baseline", "orchestrated"):
        a = run_arm(plain, arm, N, 360, 5, pts)
        b = run_arm(free, arm, N, 360, 5, pts)
        assert a.event_hash == b.event_hash and a.n_events == b.n_events
        assert metrics_of(plain, a) == metrics_of(free, b)
        assert b.capacity is not None and a.capacity is None
        assert b.capacity.metrics()["slot_utilisation"] < 0.05
        assert all(j.start_t == j.request_t for j in b.capacity.staff.jobs)


def test_more_slots_never_lengthen_the_wait():
    waits = []
    for scale in (0.7, 1.0, 1.5, HUGE):
        ctx = _ctx([P, CONS], capacity__scale=scale)
        pts = make_patients(ctx, N, 9)
        res = run_arm(ctx, "orchestrated", N, 360, 9, pts)
        waits.append(res.capacity.metrics()["capacity_delay_mean"])
    assert waits == sorted(waits, reverse=True) and waits[-1] == pytest.approx(0.0, abs=1e-9)


def test_baseline_arm_is_independent_of_staffing():
    hashes = set()
    for ss in (0.5, 1.0, 3.0):
        ctx = _ctx([P, CONS], capacity__staff__scale=ss)
        pts = make_patients(ctx, N, 9)
        hashes.add(run_arm(ctx, "baseline", N, 360, 9, pts).event_hash)
    assert len(hashes) == 1


def test_a_cancellation_frees_the_slot_it_held():
    ctx = _ctx([P, CONS])
    pts = make_patients(ctx, 3000, 3)
    res = run_arm(ctx, "baseline", 3000, 360, 3, pts)
    cancelled = [b for b in res.capacity.slots.log if b.outcome == "cancelled"]
    assert cancelled  # consent withdrawn with care declined, before the day
    sb = res.capacity.slots
    for b in cancelled:
        assert sb.booked[(b.provider, b.day)] <= sb.capacity_on(b.provider, b.day)


# ---- evaluation: reproducibility, the committed files ------------------------------------------------------------------------------------

def test_evaluate_capacity_is_byte_reproducible_and_job_count_independent(tmp_path):
    kw = dict(reps=2, cases=150, days=200, seed=7, resamples=50)
    a = evaluate_capacity(jobs=1, **kw)
    b = evaluate_capacity(jobs=2, **kw)
    pa, pb = write_capacity(a, tmp_path / "a"), write_capacity(b, tmp_path / "b")
    assert pa.read_bytes() == pb.read_bytes()
    data = json.loads(pa.read_text())
    assert "generated_at" not in pa.read_text()
    sc = data["scenarios"]["literature_anchored"]
    assert set(sc["capacity_sensitivity"]) == {"70", "85", "100", "115"}
    assert set(sc["staffing_sensitivity"]) == {"50", "75", "100", "150", "200", "300"}
    assert sc["base"] == sc["capacity_sensitivity"]["100"]
    # the baseline arm does not depend on staffing, so base staffing is the base-capacity run itself
    assert sc["staffing_sensitivity"]["100"]["arm"] == sc["base"]["arms"]["orchestrated"]


def _committed():
    p = RESULTS / "capacity.json"
    return json.loads(p.read_text()) if p.exists() else None


def test_the_committed_capacity_file_is_consistent():
    cap = _committed()
    assert cap is not None, "docs/results/capacity.json is missing: run make evaluate-capacity"
    assert cap["meta"]["seed"] == 20261017 and cap["meta"]["replications"] == 20
    res = json.loads((RESULTS / "results.json").read_text())
    for scen, sc in cap["scenarios"].items():
        # the unconstrained reference equals the main evaluation's numbers for the same seed, cases and replications
        for arm in ("baseline", "orchestrated"):
            for key in ("completion_90d", "completion_180d", "time_to_colonoscopy_median"):
                assert sc["unconstrained"]["arms"][arm][key]["mean"] == pytest.approx(res["scenarios"][scen]["arms"][arm][key]["mean"], abs=1e-6)
        # base-capacity utilisation of the baseline sits in the 80-90% band the ticket asks for
        assert 0.80 <= sc["base"]["arms"]["baseline"]["slot_utilisation"]["mean"] <= 0.90
        for blk in [sc["base"], *sc["capacity_sensitivity"].values()]:
            for arm in ("baseline", "orchestrated"):
                assert 0 < blk["arms"][arm]["slot_utilisation"]["mean"] <= 1.0
                assert blk["arms"][arm]["slot_utilisation"]["ci_high"] <= 1.0 + 1e-9
    assert set(CAPACITY_KEYS) <= set(next(iter(cap["scenarios"].values()))["base"]["arms"]["baseline"])
    assert all(k in next(iter(cap["scenarios"].values()))["base"]["arms"]["orchestrated"] for k in ALL_KEYS)


def test_the_reading_in_results_md_is_true_of_the_committed_numbers():
    """The plain-English reading opens with three claims; they must hold in the committed data."""
    cap = _committed()
    sc = cap["scenarios"]["literature_anchored"]
    gain = lambda blk, k: blk["difference"][k]["mean"]  # noqa: E731
    u, b = sc["unconstrained"], sc["base"]
    assert gain(b, "completion_90d") < 0.3 * gain(u, "completion_90d")  # the speed benefit mostly disappears
    assert gain(b, "completion_180d") >= 0.8 * gain(u, "completion_180d")  # the 180-day benefit mostly survives at base capacity
    assert gain(sc["capacity_sensitivity"]["70"], "completion_180d") < 0.4 * gain(u, "completion_180d")  # and falls away if slots are scarcer
    assert gain(b, "time_to_colonoscopy_km_median") > gain(u, "time_to_colonoscopy_km_median")  # the km-median gain (negative days) is smaller
    # staffing barely moves completion, as the text says
    st = sc["staffing_sensitivity"]
    assert abs(st["50"]["difference"]["completion_180d"]["mean"] - st["300"]["difference"]["completion_180d"]["mean"]) < 0.03


def test_results_md_carries_the_committed_section_verbatim():
    cap = _committed()
    md = (RESULTS / "results.md").read_text()
    assert md.count(BEGIN) == 1 and md.count(END) == 1
    block = md[md.index(BEGIN) + len(BEGIN): md.index(END)].strip("\n")
    assert block == render_section(cap)
    assert md.index("## Capacity caveat") < md.index(BEGIN) < md.index("## Summary")
    for title in ("## Under finite capacity", "### Headline outcomes at base capacity", "### Capacity sensitivity", "### Staffing sensitivity",
                  "### How much the benefit shrinks when capacity binds"):
        assert title in block


def test_metrics_md_defines_every_capacity_metric():
    doc = (RESULTS / "metrics.md").read_text()
    assert "## 12." in doc
    sec = doc[doc.index("## 12."):]
    missing = [k for k in CAPACITY_KEYS if f"`{k}`" not in sec]
    assert not missing, f"metrics.md section 12 does not define {missing}"


def test_evidence_md_has_the_capacity_section():
    ev = (ROOT / "docs" / "evidence.md").read_text()
    assert "## 12. Colonoscopy capacity and escalation handling" in ev
    sec = ev[ev.index("## 12."):]
    for sid in ("E12-1", "E12-2", "E12-3", "E12-4", "E12-5"):
        assert sid in sec
    params = load_params(DEFAULT_CAPACITY, warn=False)
    assert "E12-1" in params.param("capacity.slots_per_session").source
