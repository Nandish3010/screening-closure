from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.events import Event
from app.state_machine import CaseModel, InvalidTransition, apply, step
from simulator.sim.actors import as_machine_event
from simulator.sim.calendar import WorkingCalendar
from simulator.sim.engine import ARMS, BASELINE, ORCHESTRATED
from app.state_machine import replay_model
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
        apply("clinician_reviewed", close("primary_care_owner:1", evidence_ref="r"), pw)
    with pytest.raises(InvalidTransition):
        apply("patient_acknowledged", close("primary_care_owner:1", evidence_ref="r", signed_by="c"), pw)
    with pytest.raises(InvalidTransition):  # A36 (ADR-044): signed in someone else's name
        apply("clinician_reviewed", close("primary_care_owner:1", evidence_ref="r", signed_by="c"), pw)
    assert apply("clinician_reviewed", close("primary_care_owner:1", evidence_ref="r", signed_by="primary_care_owner:1"), pw) == "closed"


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
        refusal = None  # the informed refusal on record (A33): a declined close is replayed against it
        for ev in c.log:
            if ev.type == "reopened":
                state = ev.payload["resume_state"]
            elif "to_state" in ev.payload:  # a side-state move of the API's machine (M7-1)
                state = ev.payload["to_state"]
            elif ev.type in stateful_events(pw):
                state = step(CaseModel(state=state, refusal=refusal), as_machine_event(ev), pw).state
                if ev.type == "record_informed_refusal":
                    refusal = {"counselling_ref": ev.payload["counselling_ref"], "counselled_by": ev.payload["counselled_by"]}
        assert state == c.state
        # and the API's own read model of the case (its machine log replayed from scratch) agrees, unless consent is withdrawn
        if c.mach is not None and not c.mach.frozen:
            assert replay_model(c.mach.log, pw).state == c.state, c.p.idx


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
        assert not [e for e in c.log if e.type == "handoff_requested" and e.payload.get("kind") != "transfer" and e.at < ev.at]


def test_a_requested_referral_cancels_the_milestone_timer(ctx):
    orch = _run(ctx, ORCHESTRATED, cases=300, days=200)
    for c in orch.cases:
        requested = [e.at for e in c.log if e.type == "handoff_requested" and e.payload.get("kind") != "transfer"]
        for ev in _milestone_overdue(c, "referral_requested"):
            assert not requested or min(requested) >= ev.at


def test_the_colonoscopy_breach_is_the_confirmatory_milestone_and_keeps_its_31_working_days(ctx):
    orch = _run(ctx, ORCHESTRATED, cases=300, days=250)
    col = [(c, e) for c in orch.cases for e in _milestone_overdue(c, "confirmatory_completed")]
    assert col
    for c, e in col:
        assert e.payload["sla_working_days"] == ctx.pathway.milestones["confirmatory_completed"].working_days == 31
        # the clock runs from the FIRST referral request (A25), whatever step the case is in when it breaches. A change of
        # primary-care owner (a transfer) is not a referral request in the API's model the simulator keeps (sim.machine)
        assert e.payload["step"]
        first = min(x.at for x in c.log if x.type == "handoff_requested" and x.payload.get("kind") != "transfer")
        due = ctx.cal.add_working_days(first, 31)
        assert abs((e.at - due).total_seconds()) < 60  # the breach is at that due date, whatever happened after the first request
    assert "complete_confirmatory" not in ctx.pathway.slas_working_days


def test_a_bounced_referral_does_not_restart_the_colonoscopy_clock_in_the_engine(ctx):
    """A25: a case that was rejected or bounced and asked again still breaches 31 working days after its FIRST request."""
    orch = _run(ctx, ORCHESTRATED, cases=600, days=300)
    def requests(c):
        return sorted(x.at for x in c.log if x.type == "handoff_requested" and x.payload.get("kind") != "transfer")

    rerequested = [c for c in orch.cases if len(requests(c)) >= 2]
    assert rerequested, "no case asked a second provider in 600 cases"
    breached = [c for c in rerequested if _milestone_overdue(c, "confirmatory_completed")]
    assert breached
    for c in breached:
        reqs = requests(c)
        [e] = _milestone_overdue(c, "confirmatory_completed")  # once
        assert abs((e.at - ctx.cal.add_working_days(reqs[0], 31)).total_seconds()) < 60  # from the first request
    later = [c for c in breached if (lambda r: r[1] - r[0] >= timedelta(days=5))(requests(c))]
    assert later, "no breached case was re-requested more than five days after its first request"
    for c in later:
        reqs = requests(c)
        [e] = _milestone_overdue(c, "confirmatory_completed")
        assert e.at < ctx.cal.add_working_days(reqs[1], 31)  # a restart at the second request would be later


def test_any_milestone_of_the_pathway_runs_because_the_apis_sweep_runs_it(ctx):
    """M7-1: the engine no longer models milestones itself, so a milestone from any event works (it used to be refused)."""
    import dataclasses

    from simulator.sim.engine import make_engine
    from simulator.sim.runner import make_patients

    odd = dataclasses.replace(ctx.pathway.milestones["referral_requested"], name="odd", from_event="acknowledge",
                              working_days=2)
    pw = dataclasses.replace(ctx.pathway, milestones={**ctx.pathway.milestones, "odd": odd})
    res = make_engine(ORCHESTRATED, ctx.mp, pw, ctx.cal, 120, SEED).run(make_patients(ctx, 150, SEED))
    assert any(_milestone_overdue(c, "odd") for c in res.cases)


def test_the_simulators_actor_names_meet_the_state_machines_table_without_changing_what_is_logged():
    """The state machine is default-deny about who may write what; the simulator keeps its own, hashed actor names and
    renames only the role part for the machine (`sim/actors.py`)."""
    from datetime import datetime, timezone
    from uuid import uuid4

    from simulator.sim.actors import machine_actor

    assert machine_actor("owner:pcp-3") == "primary_care_owner:pcp-3"
    assert machine_actor("staff:outreach-2") == "coordinator:outreach-2"
    assert machine_actor("clinician:cl-1") == "primary_care_owner:cl-1"
    assert machine_actor("endoscopy:unit") == "specialist_owner:unit"
    assert machine_actor("lab:staff-1") == "lab:staff-1" and machine_actor("patient:k") == "patient:k"
    for service in ("system:scheduler", "scheduler", "watchdog", "system:intake", "nobody:x", "owner:", ""):
        assert machine_actor(service) == service  # not a person: the table decides (and refuses most of them)
    ev = Event(uuid4(), "acknowledge", datetime(2026, 1, 5, tzinfo=timezone.utc), "staff:intake-1", {})
    assert as_machine_event(ev).actor == "coordinator:intake-1" and ev.actor == "staff:intake-1"
