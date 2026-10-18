import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.events import Event
from app.state_machine import InvalidTransition, apply
from simulator.sim.calendar import WorkingCalendar
from simulator.sim.engine import ARMS, BASELINE, ORCHESTRATED
from simulator.sim.invariants import check_case_log, check_consent, stateful_events
from simulator.sim.patients import make_cohort, make_patient
from simulator.sim.runner import make_patients, metrics_of, run_arm

SEED = 20261017


def _run(ctx, arm, cases=150, days=120, seed=SEED):
    return run_arm(ctx, arm, cases, days, seed)


# ---- determinism -----------------------------------------------------------------------------
@pytest.mark.parametrize("arm", ARMS)
def test_same_seed_same_event_log_hash(ctx, arm):
    a, b = _run(ctx, arm), _run(ctx, arm)
    assert a.event_hash == b.event_hash and a.n_events == b.n_events


@pytest.mark.parametrize("arm", ARMS)
def test_different_seed_different_event_log_hash(ctx, arm):
    assert _run(ctx, arm, seed=1).event_hash != _run(ctx, arm, seed=2).event_hash


def test_arms_differ_but_share_patients(ctx):
    pts = make_patients(ctx, 100, SEED)
    a = run_arm(ctx, BASELINE, 100, 120, SEED, pts)
    b = run_arm(ctx, ORCHESTRATED, 100, 120, SEED, pts)
    assert a.event_hash != b.event_hash
    assert [c.p for c in a.cases] == [c.p for c in b.cases]  # common random numbers: same cohort


def test_adding_patients_does_not_change_existing_ones(ctx):
    small = make_cohort(50, SEED, ctx.mp, ctx.cal)
    big = make_cohort(51, SEED, ctx.mp, ctx.cal)
    assert small == big[:50]
    assert make_patient(7, SEED, ctx.mp, ctx.cal) == small[7]
    # and their simulated histories are unchanged when a patient is added at the end
    a = run_arm(ctx, ORCHESTRATED, 50, 120, SEED, small)
    b = run_arm(ctx, ORCHESTRATED, 51, 120, SEED, big)
    for ca, cb in zip(a.cases, b.cases):
        assert [(e.type, e.at, e.actor) for e in ca.log] == [(e.type, e.at, e.actor) for e in cb.log]


# ---- closure rule ----------------------------------------------------------------------------
def test_state_machine_rejects_unsafe_closes(ctx):
    pw = ctx.pathway
    from uuid import uuid4
    from datetime import datetime, timezone

    now = datetime(2026, 1, 5, tzinfo=timezone.utc)

    def close(actor, **payload):
        return Event(uuid4(), "close", now, actor, payload)

    with pytest.raises(InvalidTransition):
        apply("clinician_reviewed", close("system:scheduler", evidence_ref="r", signed_by="c"), pw)
    with pytest.raises(InvalidTransition):
        apply("clinician_reviewed", close("watchdog", evidence_ref="r", signed_by="c"), pw)
    with pytest.raises(InvalidTransition):
        apply("clinician_reviewed", close("clinician:1", evidence_ref="r"), pw)
    with pytest.raises(InvalidTransition):
        apply("patient_acknowledged", close("clinician:1", evidence_ref="r", signed_by="c"), pw)
    assert apply("clinician_reviewed", close("clinician:1", evidence_ref="r", signed_by="c"), pw) == "closed"


@pytest.mark.parametrize("arm", ARMS)
@settings(max_examples=15, deadline=None)
@given(seed=st.integers(min_value=0, max_value=2**31 - 1))
def test_closure_rule_never_violated_property(ctx, arm, seed):
    res = run_arm(ctx, arm, 80, 150, seed)
    closed = 0
    for c in res.cases:
        assert check_case_log(c.log, ctx.pathway) == [], (arm, seed, c.p.key)
        if arm == ORCHESTRATED:
            assert check_consent(c.log) == [], (seed, c.p.key)
        for ev in c.log:
            if ev.type == "close":
                closed += 1
                assert ev.payload["evidence_ref"] and ev.payload["signed_by"]
                assert not ev.actor.startswith(("system:", "scheduler", "watchdog", "model:"))
    assert closed > 0  # the property is not vacuous


def test_closure_rule_on_a_large_run_of_both_arms(ctx):
    for arm in ARMS:
        res = _run(ctx, arm, cases=1000, days=240)
        assert sum(len(check_case_log(c.log, ctx.pathway)) for c in res.cases) == 0
        assert sum(1 for c in res.cases if c.state == ctx.pathway.terminal_state) > 100


def test_every_case_is_in_a_state_consistent_with_its_log(ctx):
    res = _run(ctx, ORCHESTRATED, cases=300, days=200)
    for c in res.cases:
        # replay the log with the shared state machine: the engine's state must match
        state, pw = ctx.pathway.initial_state, ctx.pathway
        for ev in c.log:
            if ev.type == "reopened":
                state = ev.payload["resume_state"]
            elif ev.type in stateful_events(pw):
                state = apply(state, ev, pw)
        assert state == c.state


# ---- behaviour of the arms -------------------------------------------------------------------------
def test_baseline_has_no_owner_timers_or_escalation(ctx):
    res = _run(ctx, BASELINE, cases=300, days=200)
    types = {e.type for c in res.cases for e in c.log}
    assert not types & {"escalated", "overdue", "watchdog_flagged", "reminder_sent", "message_blocked", "delivery_failed"}
    owners = [e.payload.get("owner") for c in res.cases for e in c.log if e.type == "accept_ownership"]
    assert owners and all(o is None for o in owners)
    sent = [e for c in res.cases for e in c.log if e.type == "message_sent"]
    assert all(e.payload["kind"] == "initial" for e in sent)
    for c in res.cases:  # letter or SMS sent once
        assert sum(1 for e in c.log if e.type == "message_sent") <= 2  # a reopened case is contacted again


def test_orchestrated_names_owners_and_escalates(ctx):
    res = _run(ctx, ORCHESTRATED, cases=300, days=200)
    types = {e.type for c in res.cases for e in c.log}
    assert {"overdue", "escalated", "handoff_requested", "handoff_accepted", "watchdog_flagged"} <= types
    owners = [e.payload.get("owner") for c in res.cases for e in c.log if e.type == "accept_ownership"]
    assert owners and all(o for o in owners)


def test_orchestrated_beats_baseline_on_completion_and_loss(ctx):
    pts = make_patients(ctx, 1500, SEED)
    b = metrics_of(ctx, run_arm(ctx, BASELINE, 1500, 180, SEED, pts))
    o = metrics_of(ctx, run_arm(ctx, ORCHESTRATED, 1500, 180, SEED, pts))
    assert o["completion_180d"] > b["completion_180d"] + 0.1
    assert o["open_90d"] < b["open_90d"]
    assert o["silent_loss_rate"] < b["silent_loss_rate"]
    assert o["consent_breach_rate"] == 0.0
    assert b["time_to_ownership_median"] is None and o["time_to_ownership_median"] is not None


def test_events_are_time_ordered_and_stay_within_follow_up(ctx):
    res = _run(ctx, ORCHESTRATED, cases=200, days=90)
    for c in res.cases:
        ats = [e.at for e in c.log]
        assert ats == sorted(ats)
        assert (ats[-1] - ats[0]).total_seconds() / 86400 <= 90 + 1e-6


def test_messy_injection_can_be_switched_off(ctx, tmp_path):
    from simulator.sim.runner import load_context

    f = tmp_path / "nomessy.yaml"
    f.write_text("messy:\n  rate_multiplier: {value: 0, source: test, status: assumed}\n"
                 "patients:\n  provider_switch_prob: {value: 0, source: test, status: assumed}\n")
    from simulator.sim import DEFAULT_PARAMS

    c2 = load_context([DEFAULT_PARAMS, f], warn=False)
    res = run_arm(c2, ORCHESTRATED, 300, 120, SEED)
    types = {e.type for c in res.cases for e in c.log}
    assert not types & {"duplicate_result_merged", "conflicting_ack_rejected", "provider_switched"}


def test_engine_speed_2000_cases_120_days_under_60s(ctx):
    import time

    t0 = time.perf_counter()
    for arm in ARMS:
        run_arm(ctx, arm, 2000, 120, SEED)
    assert time.perf_counter() - t0 < 60


# ---- milestones from the pathway YAML (A18, ADR-028) -------------------------------------------------
def _milestone_overdue(case, name):
    return [e for e in case.log if e.type == "overdue" and e.payload.get("milestone") == name]


def test_the_orchestrated_arm_raises_the_referral_breach_and_the_baseline_ignores_it(ctx):
    base, orch = _run(ctx, BASELINE, cases=300, days=200), _run(ctx, ORCHESTRATED, cases=300, days=200)
    assert not any(_milestone_overdue(c, "referral_requested") for c in base.cases)
    assert not any(e.type == "overdue" for c in base.cases for e in c.log)  # no timers at all in the baseline
    breached = [c for c in orch.cases if _milestone_overdue(c, "referral_requested")]
    assert breached, "300 cases never breached the 15-working-day referral milestone"
    days = ctx.pathway.milestones["referral_requested"].working_days
    for c in breached:
        [ev] = _milestone_overdue(c, "referral_requested")  # once per case
        assert ev.actor == "scheduler" and ev.payload["sla_working_days"] == days
        created = next(e for e in c.log if e.type == "case_created").at
        assert ev.at >= ctx.cal.add_working_days(created, days)  # never before the due date
        # the breach is only raised for a case that had not requested the referral by then
        assert not [e for e in c.log if e.type == "handoff_requested" and e.at < ev.at]


def test_a_requested_referral_cancels_the_milestone_timer(ctx):
    orch = _run(ctx, ORCHESTRATED, cases=300, days=200)
    for c in orch.cases:
        requested = [e.at for e in c.log if e.type == "handoff_requested"]
        for ev in _milestone_overdue(c, "referral_requested"):
            assert not requested or min(requested) >= ev.at


def test_the_colonoscopy_breach_is_the_confirmatory_milestone_and_keeps_its_31_working_days(ctx):
    orch = _run(ctx, ORCHESTRATED, cases=300, days=250)
    col = [(c, e) for c in orch.cases for e in _milestone_overdue(c, "confirmatory_completed")]
    assert col
    for _, e in col:
        assert e.payload["sla_working_days"] == ctx.pathway.milestones["confirmatory_completed"].working_days == 31
        assert e.payload["step"] == "complete_confirmatory"
    assert "complete_confirmatory" not in ctx.pathway.slas_working_days


def test_the_engine_refuses_a_milestone_it_cannot_model(ctx):
    import dataclasses

    from simulator.sim.engine import make_engine
    from simulator.sim.params import ParamError

    bad = dataclasses.replace(
        ctx.pathway.milestones["referral_requested"], name="odd", from_event="acknowledge")
    pw = dataclasses.replace(ctx.pathway, milestones={**ctx.pathway.milestones, "odd": bad})
    with pytest.raises(ParamError, match="cannot model milestone"):
        make_engine(ORCHESTRATED, ctx.mp, pw, ctx.cal, 100, SEED)
