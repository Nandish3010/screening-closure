"""Ticket B17: human effort per case. Arithmetic is checked against hand-computed values (comments show the sums)."""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from simulator.sim import effort as E
from simulator.sim.effort import (
    ACTION, ACTIONS, AUTO, AUTOMATED_STEPS, CHASE_DEFAULT, CHASE_VARIANTS, EVENT_MAP, MANUAL, MINUTES, ORCH, ROLES, SOURCES, STATUSES,
    ChaseCfg, arm_effort, case_effort, chase_counts, minutes_of, rep_metrics, rep_reduction,
)
from simulator.sim.engine import OVERLAY_EVENTS, STEP_KIND
from simulator.sim.scenario import CASES, scenario_logs

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = (ROOT / "docs" / "evidence.md").read_text()
SECTION_11 = EVIDENCE[EVIDENCE.index("## 11. Human effort per action"):]


# ---- every action has a status and a source ---------------------------------------------------------------------------

def test_every_action_has_a_status_a_source_and_a_sane_range():
    ids = [a.id for a in ACTIONS]
    assert len(ids) == len(set(ids))
    for a in ACTIONS:
        assert a.status in STATUSES, a.id
        assert a.role in ROLES and a.process in (MANUAL, ORCH, "both"), a.id
        assert a.source.strip() and a.short.strip(), a.id
        assert 0 < a.low <= a.minutes <= a.high, a.id
        if a.status == "assumed":
            assert a.source.startswith("assumed"), a.id
        else:  # a published figure: it must cite a source id, and that id must be in evidence.md section 11
            assert a.source_ids(), f"{a.id} is {a.status} but cites no source id"
    for sid in SOURCES:
        assert sid in SECTION_11, f"{sid} is not in docs/evidence.md section 11"


def test_sourced_rows_are_not_overclaimed():
    """Nobody measured these tasks in positive-FIT follow-up: nothing may be graded `sourced` unless evidence.md says so."""
    assert not [a.id for a in ACTIONS if a.status == "sourced"]
    assert "none is graded **sourced**" in SECTION_11


def test_every_event_type_is_classified_for_both_processes():
    engine_events = set(STEP_KIND) | set(OVERLAY_EVENTS) | {"case_created"}
    assert engine_events <= set(EVENT_MAP), sorted(engine_events - set(EVENT_MAP))
    for et, rules in EVENT_MAP.items():
        assert set(rules) == {MANUAL, ORCH}, et
        for rule in rules.values():
            if callable(rule):
                continue
            for tok in rule:
                assert (tok[5:] in AUTO) if tok.startswith("auto:") else (tok in ACTION), (et, tok)


def test_every_action_and_step_is_reachable():
    text = Path(E.__file__).read_text()
    for a in ACTIONS:
        if a.chase:
            assert text.count(f'"{a.id}"') >= 2, a.id  # defined and used by chase_counts
        else:
            assert text.count(f'"{a.id}"') >= 2, a.id  # defined and mapped from an event
    for s in AUTOMATED_STEPS:
        assert text.count(f'"{s.id}"') + text.count(f"auto:{s.id}") >= 2, s.id


def test_the_orchestrated_human_steps_are_the_a21_split():
    """Human-only: accept escalation, approve drafted message, sign closure, handle exhausted reroute. Everything else listed is automated."""
    orch_only = {a.id for a in ACTIONS if a.process == ORCH}
    assert {"O_ACCEPT_ESC_SUPERVISOR", "O_ACCEPT_ESC_COORDINATOR", "O_APPROVE_DRAFT", "O_SIGN_CLOSURE", "O_EXHAUSTED_REROUTE"} <= orch_only
    automated = {s.id for s in AUTOMATED_STEPS}
    for needed in ("A_ACK", "A_ASSIGN_OWNER", "A_TEMPLATE_OUTREACH", "A_EVIDENCE_DETECT", "A_CLOSURE_PROPOSAL", "A_REFERRAL_REROUTE",
                   "A_BOUNCE", "A_OVERDUE", "A_ESCALATION_EMIT"):
        assert needed in automated
    # in the orchestrated process none of the machine steps may map to a human action: ack, owner, evidence, proposal, reroute
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)

    def ev(t, payload=None, actor="x"):
        return SimpleNamespace(type=t, payload=payload or {}, actor=actor, at=start)

    for t in ("case_created", "acknowledge", "accept_ownership", "complete_confirmatory", "clinician_review", "handoff_requested",
              "reminder_sent", "overdue"):
        assert all(tok.startswith("auto:") for tok in E.tokens(ORCH, ev(t))), t
    assert E.tokens(ORCH, ev("close")) == ("O_SIGN_CLOSURE",)


# ---- arithmetic on the five-case scenario (orchestrated process) ----------------------------------------------------------
#
# C1: consent review 5 (clinician) + sign closure 5                         -> 10 min, 2 human actions; automated: ingest, ack, assign,
#     reply, booking, consent ledger, evidence, proposal                      -> 8
# C2: transfer decision 5 + accept supervisor escalation 2 + sign 5 + reopen review 5 + sign 5 -> 22 min (clinician), 5 actions; automated: ingest,
#     ack, assign, escalation emit, then two cycles of (reply, booking, evidence, proposal)      -> 12
# C3: accept supervisor escalation 2 + sign 5                               -> 7 min, 2 actions; automated: ingest, ack, assign, dedup, emit,
#     reply, booking, evidence, proposal                                      -> 9
# C4: lost_contact (ladder_exhausted) review 8 (coordinator)                -> 8 min, 1 action; automated: ingest, ack, assign, emit  -> 4
# C5: sign 5                                                                -> 5 min, 1 action; automated: ingest, ack, assign, claim guard,
#     reply, booking, evidence, proposal                                      -> 8
# Totals: 52 minutes (clinician 44, coordinator 8), 11 human actions, 41 automated; 4 cases closed (C1, C2, C3, C5).
FIVE = {
    "minutes": [10, 22, 7, 8, 5], "actions": [2, 5, 2, 1, 1], "auto": [8, 12, 9, 4, 8],
}


def _five(process=ORCH):
    return [case_effort(log, process, 180.0) for log in scenario_logs()]


def test_five_case_scenario_orchestrated_arithmetic():
    ces = _five()
    for ce, mins, acts, auto in zip(ces, FIVE["minutes"], FIVE["actions"], FIVE["auto"]):
        assert minutes_of(ce.human)["total"] == pytest.approx(mins)
        assert sum(ce.human.values()) == acts
        assert sum(ce.auto.values()) == auto
        assert not any(ce.chase[v] for v in ce.chase)  # no chasing in the orchestrated process
    cases = [SimpleNamespace(orch=True, log=log, lat=SimpleNamespace(u_reach=0.0)) for log in scenario_logs()]
    block = arm_effort(cases, 1.0, 180.0)["all"]
    assert block["n_cases"] == 5 and block["n_closed"] == 4
    m = rep_metrics(block)
    assert m["minutes_per_case"] == pytest.approx(52 / 5)  # 10.4
    assert m["minutes_per_closed_case"] == pytest.approx(52 / 4)  # 13.0
    assert m["human_actions_per_case"] == pytest.approx(11 / 5)
    assert m["automated_actions_per_case"] == pytest.approx(41 / 5)
    assert m["automation_share"] == pytest.approx(41 / 52)
    assert m["minutes_per_case.owner_clinician"] == pytest.approx(44 / 5)
    assert m["minutes_per_case.coordinator"] == pytest.approx(8 / 5)
    assert m["minutes_per_case.specialist"] == 0
    # every minute in this example is an assumed figure
    assert m["minutes_per_case.status.assumed"] == pytest.approx(52 / 5)
    assert m["minutes_per_case.status.sourced-adjacent"] == 0


def test_in_reach_subset_and_unreached_cases_follow_the_manual_process():
    """A case of the orchestrated arm outside the programme's reach is costed with today's process."""
    logs = scenario_logs()
    cases = [SimpleNamespace(orch=(i % 2 == 0), log=log, lat=SimpleNamespace(u_reach=0.1 if i % 2 == 0 else 0.9)) for i, log in enumerate(logs)]
    out = arm_effort(cases, 0.5, 180.0)
    assert out["all"]["n_cases"] == 5 and out["in_reach"]["n_cases"] == 3  # C1, C3, C5
    manual_expected = sum(
        sum(case_effort(logs[i], MANUAL, 180.0).human.values()) for i in (1, 3))
    orch_expected = sum(sum(case_effort(logs[i], ORCH, 180.0).human.values()) for i in (0, 2, 4))
    assert sum(out["all"]["human"].values()) == manual_expected + orch_expected
    assert sum(out["in_reach"]["human"].values()) == orch_expected


# ---- today's process, including chasing ---------------------------------------------------------------------------------------

START = datetime(2026, 1, 5, tzinfo=timezone.utc)


def _log(rows):
    return [SimpleNamespace(type=t, actor=a, payload=p, at=START + timedelta(days=d)) for d, t, a, p in rows]


MANUAL_LOG = _log([
    (0, "case_created", "system:intake", {}),
    (1, "acknowledge", "lab:staff-0", {}),
    (2, "accept_ownership", "staff:intake-0", {"owner": None, "implicit": True}),
    (3, "contact_patient", "staff:outreach-0", {}),
    (3, "message_sent", "staff:outreach-0", {"kind": "initial", "channel": "sms"}),
    (20, "patient_acknowledge", "patient:p", {"via": "reply"}),
    (22, "handoff_requested", "staff:referrals-0", {"attempt": 0}),
    (60, "handoff_accepted", "endoscopy:unit-0", {"attempt": 0}),
    (60, "book_confirmatory", "staff:referrals-0", {"appointment_on": "2026-04-05"}),  # 2026-01-05 + 90 days
    (140, "complete_confirmatory", "endoscopy:unit", {}),
    (141, "clinician_review", "clinician:cl-0", {}),
    (150, "close", "clinician:cl-0", {}),
])
# Events (minutes): lookup 5, ack 2, handover 9.5, SMS 1.5, record contact 2, referral 12.4, specialist 9.5, book 8.0, record completion 5,
# clinician review 5, file closure 3 = 62.9.
# Chasing, default (3 attempts, every 14 days): patient waited 20-3 = 17 days -> 1 call (7.2); referral waited 60-22 = 38 days -> 2 calls
# (8.1 each = 16.2); colonoscopy recorded 140 - 90 = 50 days after the appointment date -> 3 checks (5.0 each = 15.0) = 38.4. Total 101.3.
# No chasing: 62.9. Intensive (5 attempts every 7 days): 17 -> 2 (14.4), 38 -> 5 (40.5), 50 -> 5 (25.0) = 79.9, total 142.8.


def test_manual_process_events_and_chasing_arithmetic():
    ce = case_effort(MANUAL_LOG, MANUAL, 180.0)
    assert ce.auto == {}
    assert minutes_of(ce.human)["total"] == pytest.approx(62.9)
    assert ce.human["X_SPECIALIST_REVIEW"] == 1 and ce.human["M_SMS_ATTEMPT"] == 1
    assert ce.chase["default"] == {"M_CHASE_PATIENT": 1, "M_CHASE_SPECIALIST": 2, "M_CHASE_COMPLETION": 3}
    assert minutes_of({**ce.human, **{k: ce.human.get(k, 0) + v for k, v in ce.chase["default"].items()}})["total"] == pytest.approx(101.3)
    assert not ce.chase["none"]
    assert minutes_of(ce.chase["intensive"])["total"] == pytest.approx(79.9)
    assert minutes_of(ce.chase["default"])["total"] == pytest.approx(38.4)


def test_chase_edge_cases():
    # answered within one interval: no chase; never answered: capped at N; an unanswered referral with a later re-request ends at the re-request
    quick = _log([(0, "case_created", "s", {}), (1, "message_sent", "s", {"channel": "phone"}), (10, "patient_acknowledge", "p", {})])
    assert not chase_counts(quick, 180.0, CHASE_VARIANTS)["default"]
    silent = _log([(0, "case_created", "s", {}), (1, "message_sent", "s", {"channel": "phone"})])
    assert chase_counts(silent, 180.0, CHASE_VARIANTS)["default"] == {"M_CHASE_PATIENT": 3}
    assert chase_counts(silent, 180.0, {"x": ChaseCfg(2, 100.0)})["x"] == {"M_CHASE_PATIENT": 1}  # 179 days / 100 = 1 attempt
    two_req = _log([(0, "case_created", "s", {}), (5, "handoff_requested", "s", {}), (40, "handoff_requested", "s", {}),
                    (50, "handoff_accepted", "endoscopy:unit-1", {})])
    c = chase_counts(two_req, 180.0, CHASE_VARIANTS)["default"]
    assert c["M_CHASE_SPECIALIST"] == 2 + 0  # first request waited 35 days (2 attempts), second answered in 10 days (0)


def test_phone_letter_and_sms_attempts_cost_by_channel():
    mk = lambda ch: case_effort(_log([(0, "case_created", "s", {}), (1, "message_sent", "s", {"channel": ch})]), MANUAL, 10.0).human
    assert mk("phone")["M_PHONE_ATTEMPT"] == 1 and mk("letter")["M_LETTER"] == 1
    assert mk("sms")["M_SMS_ATTEMPT"] == 1 and mk("whatsapp")["M_SMS_ATTEMPT"] == 1


def test_orchestrated_message_kinds():
    mk = lambda kind: case_effort(_log([(0, "case_created", "s", {}), (1, "message_sent", "s", {"kind": kind, "channel": "phone"})]), ORCH, 10.0)
    assert not mk("initial").human and mk("initial").auto["A_TEMPLATE_OUTREACH"] == 1
    assert not mk("appointment_reminder").human
    assert mk("human_call").human == {"O_SUPERVISOR_CALL": 1, "O_APPROVE_DRAFT": 1}
    assert mk("coordinator_route").human == {"O_COORDINATOR_CALL": 1, "O_APPROVE_DRAFT": 1}


def test_call_back_is_the_only_patient_event_a_person_records_in_the_orchestrated_process():
    ack = lambda via: case_effort(_log([(0, "case_created", "s", {}), (2, "patient_acknowledge", "p", {"via": via})]), ORCH, 10.0)
    assert ack("reply").human == {} and ack("reply").auto["A_REPLY_DETECT"] == 1
    assert ack("self_presented").human == {"X_RECORD_CONTACT": 1}


# ---- the reduction on a hand-built example ----------------------------------------------------------------------------------

def _block(n, closed, human, auto=None, chase=None):
    return {"n_cases": n, "n_closed": closed, "human": human, "auto": auto or {},
            "chase": {v: (chase or {}) if v == "default" else {} for v in CHASE_VARIANTS}}


def test_labour_reduction_hand_example():
    # baseline, 4 cases: lookup 4 x 5 = 20, referral 2 x 12.4 = 24.8, specialist 2 x 9.5 = 19 -> 63.8 min = 15.95 per case; 2 closed -> 31.9 per closed
    base = _block(4, 2, {"M_RESULT_LOOKUP": 4, "M_REFERRAL_SEND": 2, "X_SPECIALIST_REVIEW": 2})
    # orchestrated, 4 cases: sign 3 x 5 = 15, specialist 2 x 9.5 = 19, accept escalation 2 x 2 = 4 -> 38 min = 9.5 per case; 3 closed -> 12.667 per closed;
    # 7 human actions, 20 automated -> automation share 20 / 27
    orch = _block(4, 3, {"O_SIGN_CLOSURE": 3, "X_SPECIALIST_REVIEW": 2, "O_ACCEPT_ESC_COORDINATOR": 2}, auto={"A_ACK": 4, "A_OVERDUE": 16})
    mb, mo = rep_metrics(base), rep_metrics(orch)
    assert mb["minutes_per_case"] == pytest.approx(15.95) and mo["minutes_per_case"] == pytest.approx(9.5)
    assert mb["minutes_per_closed_case"] == pytest.approx(31.9) and mo["minutes_per_closed_case"] == pytest.approx(38 / 3)
    assert mo["automation_share"] == pytest.approx(20 / 27) and mb["automation_share"] == 0
    r = rep_reduction(base, orch)
    assert r["labour_reduction"] == pytest.approx(1 - 38 / 63.8)  # 0.40439
    assert r["labour_reduction_per_closed_case"] == pytest.approx(1 - (38 / 3) / 31.9)
    assert r["human_actions_reduction"] == pytest.approx(1 - 7 / 8)  # baseline 8 actions, orchestrated 7 -> 12.5%
    assert r["labour_reduction.specialist"] == pytest.approx(0.0)  # same two specialist reviews
    assert r["labour_reduction.coordinator"] == pytest.approx(1 - 4 / 44.8)
    # an override of the minutes moves the reduction: halve the referral cost -> baseline 51.4
    half = {**MINUTES, "M_REFERRAL_SEND": 6.2}
    assert rep_reduction(base, orch, half)["labour_reduction"] == pytest.approx(1 - 38 / 51.4)


def test_reduction_is_none_when_the_baseline_has_no_minutes():
    zero = _block(3, 0, {})
    assert rep_reduction(zero, _block(3, 0, {"O_SIGN_CLOSURE": 1}))["labour_reduction"] is None
    assert rep_metrics(zero)["minutes_per_closed_case"] is None


# ---- integration: evaluate, results.json, report, docs ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def small():
    from simulator.sim.evaluate import evaluate

    return evaluate(reps=3, cases=300, days=240, seed=11, resamples=100, jobs=1)


def test_evaluate_writes_the_effort_block(small):
    eff = small["effort"]
    assert eff["model"]["effort_hash"] == E.effort_hash()
    assert {a["id"] for a in eff["model"]["actions"]} == {a.id for a in ACTIONS}
    for scen in ("upper_range", "literature_anchored"):
        e = eff["scenarios"][scen]
        b, o = e["all"]["arms"]["baseline"], e["all"]["arms"]["orchestrated"]
        assert b["automation_share"]["mean"] == 0 and o["automation_share"]["mean"] > 0.3
        assert 0 < o["minutes_per_case"]["mean"] < b["minutes_per_case"]["mean"]
        cell = e["all"]["reduction"]["labour_reduction"]
        assert cell["ci_low"] <= cell["mean"] <= cell["ci_high"]
        # minutes by role and by grade each add up to the total
        for arm in (b, o):
            assert sum(arm[f"minutes_per_case.{r}"]["mean"] for r in ROLES) == pytest.approx(arm["minutes_per_case"]["mean"], abs=1e-4)
            assert sum(arm[f"minutes_per_case.status.{s}"]["mean"] for s in STATUSES) == pytest.approx(arm["minutes_per_case"]["mean"], abs=1e-4)
        assert e["tornado"] and e["tornado"][0]["swing"] >= e["tornado"][-1]["swing"]
        assert e["robustness"]["headline"] == pytest.approx(cell["mean"], abs=1e-4)
    # the programme-level reduction cannot exceed the in-reach one when the reach is below 1 (the unreached cases are manual)
    lit = eff["scenarios"]["literature_anchored"]
    assert lit["all"]["reduction"]["labour_reduction"]["mean"] < lit["in_reach"]["reduction"]["labour_reduction"]["mean"]
    # the baseline arm is identical in the two scenarios, so its effort is too
    assert (eff["scenarios"]["upper_range"]["all"]["arms"]["baseline"]["minutes_per_case"]["mean"]
            == lit["all"]["arms"]["baseline"]["minutes_per_case"]["mean"])


def test_evaluate_with_effort_is_deterministic_and_keeps_effort_out_of_the_other_keys(small):
    """The effort block is added to results.json; the arms and per_replication keys carry no effort data (their values are the pre-B17 ones: see the ticket notes)."""
    from simulator.sim.evaluate import evaluate

    again = evaluate(reps=3, cases=300, days=240, seed=11, resamples=100, jobs=1)
    assert json.dumps(small, sort_keys=True) == json.dumps(again, sort_keys=True)  # deterministic
    assert "effort" not in small["arms"]["baseline"] and "effort" not in small["per_replication"][0]["arms"]["baseline"]


def test_report_section_renders_and_states_the_target(small):
    from simulator.sim.report import effort_section, effort_target_met, render_markdown

    sec = effort_section(small)
    assert sec.startswith("## Human effort") and "Labour reduction" in sec and "The minutes table" in sec
    for scen in ("literature_anchored", "upper_range"):
        met = effort_target_met(small, scen)
        assert met in (True, False)
    assert ("**met**" in sec) or ("**not met**" in sec)
    assert "## Human effort" in render_markdown(small)
    assert effort_section({"meta": {}}) == ""  # results made before B17 render without the section


def test_target_flag_follows_the_confidence_interval():
    from simulator.sim.report import effort_target_met

    def res(lo, mean, hi):
        cell = {"mean": mean, "ci_low": lo, "ci_high": hi}
        return {"effort": {"scenarios": {"upper_range": {"all": {"reduction": {"labour_reduction": cell}}}}}}

    assert effort_target_met(res(0.41, 0.45, 0.49), "upper_range") is True
    assert effort_target_met(res(0.39, 0.45, 0.49), "upper_range") is False  # mean above 40% but the interval reaches below it
    assert effort_target_met(res(0.30, 0.35, 0.40), "upper_range") is False


EFFORT_KEYS = [
    "minutes_per_case", "minutes_per_closed_case", "human_actions_per_case", "automated_actions_per_case", "automation_share",
    "labour_reduction", "labour_reduction_per_closed_case", "human_actions_reduction",
    "minutes_per_case.<role>", "minutes_per_case.status.<grade>", "labour_reduction.<role>",
]


def test_metrics_doc_defines_every_effort_key(small):
    doc = (ROOT / "docs" / "results" / "metrics.md").read_text()
    section = doc[doc.index("## 11. Human effort"):]
    arm = small["effort"]["scenarios"]["upper_range"]["all"]["arms"]["baseline"]
    for key in arm:
        generic = re.sub(r"\.(coordinator|owner_clinician|specialist)$", ".<role>", key)
        generic = re.sub(r"\.status\.[a-z-]+$", ".status.<grade>", generic)
        assert generic in EFFORT_KEYS, key
        assert key in section or f"`{generic}`" in section, f"metrics.md section 11 does not define {key}"
    for key in small["effort"]["scenarios"]["upper_range"]["all"]["reduction"]:
        generic = re.sub(r"\.(coordinator|owner_clinician|specialist)$", ".<role>", key)
        assert generic in EFFORT_KEYS, key
        assert key in section or f"`{generic}`" in section, key


def test_committed_results_carry_the_current_effort_model():
    res = json.loads((ROOT / "docs" / "results" / "results.json").read_text())
    assert res["effort"]["model"]["effort_hash"] == E.effort_hash(), "docs/results/results.json predates simulator/sim/effort.py: run make evaluate"
    md = (ROOT / "docs" / "results" / "results.md").read_text()
    assert "## Human effort" in md
    lit = res["effort"]["scenarios"]["literature_anchored"]["all"]["reduction"]["labour_reduction"]
    assert f"{100 * lit['mean']:.1f}%" in md  # the quoted headline is the result
