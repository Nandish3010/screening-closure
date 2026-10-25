"""T-B7 (ticket B5): messy-case injection, one table-driven scenario per injected case, both arms.

Each scenario forces exactly one messy case on otherwise-normal cases (the draw is patched to a `MessyPlan` with only that
field set; wrong contact also gives every patient an invalid contact; every other scenario gives every patient a valid
one), runs the baseline and the orchestrated arm on a small cohort and states, per arm:

* the ORDERED subsequence of events (type and, where it matters, payload keys) that must appear in every case that reached
  the scenario's precondition, and the event types that must never appear;
* the outcome that `build_record` reports for the case (`messy` tags, `handled`, `consent_breach`) and a few scenario checks;
* for the orchestrated arm, that the case's machine log (the API read model's log) is what the scenario needs and that it
  replays unchanged through the real API store and the real sweep (`test_sweep_conformance.replay`).

| scenario              | precondition          | orchestrated arm                                                                           | baseline arm                                                       |
|-----------------------|-----------------------|--------------------------------------------------------------------------------------------|--------------------------------------------------------------------|
| wrong_contact         | contacted             | message_sent, delivery_failed (then contact_recovered: handled); never an unnoticed send   | message_sent, nothing else: no delivery_failed, no contact_recovered, not handled |
| provider_switch       | the switch applies    | provider_switched{applies}, handoff_requested{transfer}, handoff_accepted{transfer, new owner}: handled | provider_switched{applies, outcome lost or continued}; no transfer; not handled |
| duplicate_result      | every case            | one case_created, one duplicate_result_merged: handled; no duplicate_result_received       | one case_created, duplicate_result_received{new_work_item}; not handled |
| conflicting_ack       | owner accepted        | accept_ownership, conflicting_ack_rejected (one owner only in the machine log): handled     | accept_ownership{implicit}, conflicting_ack_unresolved{claimants 2}; not handled |
| consent_after_booking | booked                | book_confirmatory, consent_updated{withdrawn, flagged_for_review}, consent_review_completed; no message_sent after; machine froze or restored: handled | book_confirmatory, consent_updated{recorded False}; no review, no block; not handled |
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Callable

import pytest

from app.calendar import default_calendar
from simulator.sim import engine as engine_mod
from simulator.sim.engine import BASELINE, ORCHESTRATED, Engine
from simulator.sim.messy import MessyPlan
from simulator.sim.runner import make_patients
from simulator.tests.test_sweep_conformance import _key, replay

SEED = 7
PATIENTS = 30
DAYS = 240

NO_MESSY = dict(wrong_contact=False, switch_day=None, duplicate_day=None, conflicting_ack=False,
                consent_after_booking_lag=None)

Step = tuple[str, "dict | Callable[[dict], bool]"]  # (event type, payload subset or predicate)


def _step(t: str, **payload) -> Step:
    return (t, payload)


def _matches(ev, step: Step) -> bool:
    t, cond = step
    if ev.type != t:
        return False
    return cond(ev.payload) if callable(cond) else all(ev.payload.get(k) == v for k, v in cond.items())


def subsequence(log, steps: list[Step]) -> bool:
    """The steps occur in this order in the log (other events may sit between them)."""
    it = iter(log)
    return all(any(_matches(ev, s) for ev in it) for s in steps)


def types(case) -> list[str]:
    return [e.type for e in case.log]


@dataclass
class ArmSpec:
    precondition: Callable  # case -> bool: the cases that reached the stage this scenario's events belong to
    steps: list[Step]  # ordered subsequence every such case must show
    forbidden: tuple[str, ...] = ()  # event types that must not appear in ANY case of the cohort
    tags: tuple[str, ...] = ()  # the realised messy tags build_record reports for the cases under the precondition
    check: Callable | None = None  # check(case, record): scenario-specific outcome
    min_cases: int = 5  # the precondition must hold for at least this many cases (the test is not vacuous)


@dataclass
class Scenario:
    name: str
    plan: dict
    arms: dict[str, ArmSpec]
    wrong_contact: bool = False


def _first(case, etype, pred=lambda p: True):
    return next((e for e in case.log if e.type == etype and pred(e.payload)), None)


# ---- scenario checks -------------------------------------------------------------------------------------------------

def _wc_orch(case, rec):
    recovered = "contact_recovered" in types(case)
    assert ("wrong_contact" in rec.handled) == recovered, "wrong_contact is handled exactly when the contact was recovered"
    if recovered:  # a recovery follows the failed delivery, and it is by a named owner
        assert subsequence(case.log, [_step("delivery_failed"), ("contact_recovered", lambda p: True)])
        assert _first(case, "contact_recovered").actor.startswith("owner:")
    assert not rec.consent_breach


def _wc_base(case, rec):
    assert "wrong_contact" not in rec.handled
    assert case.contact_ok is False, "nobody ever corrected the number in today's process"


def _sw_orch(case, rec):
    transfers = [e for e in case.log if e.payload.get("kind") == "transfer"]
    accepted = [e for e in transfers if e.type == "handoff_accepted"]
    assert len(accepted) == 1, "exactly one transfer is accepted"
    assert accepted[0].payload["owner"].startswith("owner:new-") and accepted[0].actor == accepted[0].payload["owner"]
    for e in transfers:  # a rejected transfer is followed by a second request before the acceptance
        if e.type == "handoff_rejected":
            assert subsequence(case.log[case.log.index(e):], [_step("handoff_requested", kind="transfer"),
                                                              _step("handoff_accepted", kind="transfer")])
    assert "provider_switch" in rec.handled


def _sw_base(case, rec):
    sw = _first(case, "provider_switched", lambda p: p.get("applies"))
    assert sw.payload["outcome"] in ("lost", "continued")
    if sw.payload["outcome"] == "lost":
        assert case.abandoned and not [e for e in case.log if e.at > sw.at], "a lost case does nothing after the switch"
    assert "provider_switch" not in rec.handled


def _dup_orch(case, rec):
    assert types(case).count("case_created") == 1 and types(case).count("duplicate_result_merged") == 1
    assert _first(case, "duplicate_result_merged").payload == {"matched": True}
    assert _first(case, "duplicate_result_merged").actor == "system:dedup"
    created = [e for e in case.mach.log if e.type == "case_created"]
    assert len(created) == 1 and not [e for e in case.mach.log if "duplicate" in e.type], "the API never sees the duplicate"
    assert "duplicate_result" in rec.handled


def _dup_base(case, rec):
    assert types(case).count("case_created") == 1 and types(case).count("duplicate_result_received") == 1
    assert _first(case, "duplicate_result_received").payload == {"new_work_item": True}
    assert "duplicate_result" not in rec.handled


def _conf_orch(case, rec):
    owners = [e for e in case.log if e.type == "accept_ownership"]
    assert len(owners) == 1, "the second claim is not part of the case log"
    rej = _first(case, "conflicting_ack_rejected")
    assert rej.actor == "system:state-machine" and rej.payload["owner"] == owners[0].payload["owner"]
    assert rej.payload["claimant"].startswith("owner:other-") and rej.payload["claimant"] != rej.payload["owner"]
    assert rej.payload["reason"], "the state machine's refusal is recorded"
    assert [e.type for e in case.mach.log].count("accept_ownership") == 1, "the API saw exactly one claim"
    assert rec.t_owner is not None and "conflicting_ack" in rec.handled


def _conf_base(case, rec):
    assert [e for e in case.log if e.type == "accept_ownership"][0].payload == {"owner": None, "implicit": True}
    assert "conflicting_ack" not in rec.handled and rec.t_owner is None, "today's process never names an owner"


def _cab_orch(case, rec):
    upd = _first(case, "consent_updated", lambda p: p.get("source") == "after_booking")
    assert upd.payload == {"status": "withdrawn", "scope": "outreach", "flagged_for_review": True, "source": "after_booking"}
    later = [e for e in case.log if e.at >= upd.at and e.type == "message_sent"]
    assert not later, "no message goes out after the withdrawal"
    blocked = [e for e in case.log if e.type == "message_blocked"]
    assert all(e.payload["reason"] == "consent_withdrawn" and e.at >= upd.at for e in blocked)
    review = _first(case, "consent_review_completed")
    mlog = [e for e in case.mach.log if e.type == "consent_updated"]
    assert [e.payload["withdrawn"] for e in mlog][:1] == [True], "the API read model recorded the withdrawal"
    restored = len(mlog) > 1 and mlog[-1].payload["withdrawn"] is False
    if review.payload["outcome"] == "declined_care":  # the review found the patient declines care: frozen in consent_withdrawn
        assert case.mach.frozen and not restored
        assert "lost_contact" in types(case)
    else:
        assert review.payload["outcome"] == "continues_without_outreach" and restored and not case.mach.frozen
    assert "consent_after_booking" in rec.handled and not rec.consent_breach


def _cab_base(case, rec):
    upd = _first(case, "consent_updated", lambda p: p.get("source") == "after_booking")
    assert upd.payload == {"status": "withdrawn", "scope": "outreach", "recorded": False, "source": "after_booking"}
    assert "consent_after_booking" not in rec.handled


def _all(_case) -> bool:
    return True


def _has(etype: str, pred=lambda p: True):
    return lambda case: any(e.type == etype and pred(e.payload) for e in case.log)


SCENARIOS = [
    Scenario(
        "wrong_contact", dict(wrong_contact=True), wrong_contact=True,
        arms={
            ORCHESTRATED: ArmSpec(
                _has("message_sent"), [_step("message_sent", kind="initial"), _step("delivery_failed")],
                tags=("wrong_contact",), check=_wc_orch),
            BASELINE: ArmSpec(
                _has("message_sent"), [_step("message_sent", kind="initial")],
                forbidden=("delivery_failed", "contact_recovered"), tags=("wrong_contact",), check=_wc_base),
        }),
    Scenario(
        "provider_switch", dict(switch_day=5.0),
        arms={
            ORCHESTRATED: ArmSpec(
                _has("provider_switched", lambda p: p.get("applies")),
                [_step("provider_switched", applies=True), _step("handoff_requested", kind="transfer"),
                 _step("handoff_accepted", kind="transfer")],
                tags=("provider_switch",), check=_sw_orch),
            BASELINE: ArmSpec(
                _has("provider_switched", lambda p: p.get("applies")),
                [("provider_switched", lambda p: p["applies"] is True and p["outcome"] in ("lost", "continued"))],
                tags=("provider_switch",), check=_sw_base),
        }),
    Scenario(
        "duplicate_result", dict(duplicate_day=3.0),
        arms={
            ORCHESTRATED: ArmSpec(
                _all, [_step("case_created"), _step("duplicate_result_merged", matched=True)],
                forbidden=("duplicate_result_received",), tags=("duplicate_result",), check=_dup_orch),
            BASELINE: ArmSpec(
                _all, [_step("case_created"), _step("duplicate_result_received", new_work_item=True)],
                forbidden=("duplicate_result_merged",), tags=("duplicate_result",), check=_dup_base),
        }),
    Scenario(
        "conflicting_ack", dict(conflicting_ack=True),
        arms={
            ORCHESTRATED: ArmSpec(
                _has("accept_ownership"), [_step("accept_ownership"), _step("conflicting_ack_rejected")],
                forbidden=("conflicting_ack_unresolved",), tags=("conflicting_ack",), check=_conf_orch),
            BASELINE: ArmSpec(
                _has("accept_ownership"),
                [_step("accept_ownership", implicit=True), _step("conflicting_ack_unresolved", claimants=2)],
                forbidden=("conflicting_ack_rejected",), tags=("conflicting_ack",), check=_conf_base),
        }),
    Scenario(
        "consent_after_booking", dict(consent_after_booking_lag=2.0),
        arms={
            ORCHESTRATED: ArmSpec(
                _has("book_confirmatory"),
                [_step("book_confirmatory"),
                 _step("consent_updated", status="withdrawn", flagged_for_review=True, source="after_booking"),
                 _step("consent_review_completed")],
                tags=("consent_after_booking",), check=_cab_orch),
            BASELINE: ArmSpec(
                _has("book_confirmatory"),
                [_step("book_confirmatory"), _step("consent_updated", status="withdrawn", recorded=False,
                                                   source="after_booking")],
                forbidden=("consent_review_completed", "message_blocked"), tags=("consent_after_booking",),
                check=_cab_base),
        }),
]

_CACHE: dict = {}


def _run(ctx, sc: Scenario, arm: str):
    key = (sc.name, arm)
    if key not in _CACHE:
        # reach 1.0: every case gets the orchestrated process; no missed timers: the API cannot miss one, so the replay must match
        mp = dataclasses.replace(ctx.mp, programme_reach=1.0, timer_miss_prob=0.0)
        patients = [dataclasses.replace(p, contact_valid=not sc.wrong_contact, provider_switch=False)
                    for p in make_patients(ctx, PATIENTS, SEED)]
        with pytest.MonkeyPatch.context() as mpatch:
            mpatch.setattr(engine_mod, "draw_messy", lambda seed, p, m: MessyPlan(**{**NO_MESSY, **sc.plan}))
            res = Engine(arm, mp, ctx.pathway, ctx.cal, DAYS, SEED).run(patients)
        _CACHE[key] = res
    return _CACHE[key]


@pytest.mark.parametrize("arm", [BASELINE, ORCHESTRATED])
@pytest.mark.parametrize("sc", SCENARIOS, ids=lambda s: s.name)
def test_messy_case_produces_its_defined_events_and_outcome(ctx, sc, arm):
    res = _run(ctx, sc, arm)
    spec = sc.arms[arm]
    assert len(res.cases) == PATIENTS and len(res.records) == PATIENTS
    assert len({c.p.case_id for c in res.cases}) == PATIENTS, "one case per patient"
    reached = 0
    for case, rec in zip(res.cases, res.records):
        got = set(types(case))
        assert not got & set(spec.forbidden), (case.p.idx, got & set(spec.forbidden))
        if arm == BASELINE:
            assert case.mach is None
        if not spec.precondition(case):
            continue
        reached += 1
        assert subsequence(case.log, spec.steps), (case.p.idx, types(case))
        assert rec.messy == spec.tags, (case.p.idx, rec.messy)
        if spec.check:
            spec.check(case, rec)
    assert reached >= spec.min_cases, f"only {reached} cases reached the precondition: the scenario would be vacuous"


@pytest.mark.parametrize("sc", SCENARIOS, ids=lambda s: s.name)
def test_orchestrated_machine_log_replays_through_the_real_store_and_sweep(ctx, sc):
    res = _run(ctx, sc, ORCHESTRATED)
    pathway, calendar = ctx.pathway, default_calendar()
    for case in res.cases:
        assert case.mach is not None
        got = replay(case.mach, pathway, calendar)
        assert [_key(e) for e in got] == [_key(e) for e in case.mach.log], case.p.idx
        assert sum(1 for e in got if e.type == "case_created") == 1


def test_the_scenario_table_covers_the_five_messy_cases_and_both_arms():
    from simulator.sim.messy import MESSY_TAGS

    assert tuple(s.name for s in SCENARIOS) == MESSY_TAGS
    assert all(set(s.arms) == {BASELINE, ORCHESTRATED} for s in SCENARIOS)
    for s in SCENARIOS:  # exactly one messy field is set in each plan
        plan = MessyPlan(**{**NO_MESSY, **s.plan})
        assert plan.tags() == (s.name,), s.name


def test_a_duplicate_result_yields_one_case_in_both_arms(ctx):
    sc = next(s for s in SCENARIOS if s.name == "duplicate_result")
    for arm in (BASELINE, ORCHESTRATED):
        res = _run(ctx, sc, arm)
        assert all(types(c).count("case_created") == 1 for c in res.cases)
        assert sum(types(c).count("case_created") for c in res.cases) == PATIENTS
