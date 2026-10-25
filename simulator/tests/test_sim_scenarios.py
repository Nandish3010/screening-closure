"""Ticket B12: the literature-anchored scenario (params.conservative.yaml) next to the upper-range one."""

import json
from pathlib import Path

import pytest

from simulator.sim import DEFAULT_CONSERVATIVE, DEFAULT_PARAMS
from simulator.sim.cli import main
from simulator.sim.evaluate import LITERATURE, UPPER, evaluate, write_results
from simulator.sim.params import load_params
from simulator.sim.runner import load_context, make_patients, run_arm

SMALL = dict(reps=3, cases=200, days=200, seed=7, resamples=100)
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def small_result():
    return evaluate(jobs=1, **SMALL)


# ---- the overlay file ----------------------------------------------------------------------------

def test_overlay_only_overrides_existing_keys_and_every_override_has_a_source():
    base = load_params(DEFAULT_PARAMS, warn=False)
    over = load_params(DEFAULT_CONSERVATIVE, warn=False)
    assert len(over.keys()) >= 5
    for p in over.rows():
        assert p.key in base, f"overlay key {p.key!r} does not exist in params.yaml (typo?)"
        assert base.param(p.key).value != p.value, f"overlay key {p.key!r} does not change anything"
        assert p.source and p.source != "TODO", p.key
        assert "Upper-range value" in p.notes or "Upper-range" in p.notes, f"{p.key}: say what it overrides"


def test_overlay_pins_the_three_requested_caps():
    ctx = load_context([DEFAULT_PARAMS, DEFAULT_CONSERVATIVE], warn=False)
    mp = ctx.mp
    assert mp.sms_reminder_effect == 0.0  # lower bound of the published range
    esc, rec = mp.escalation_effect_prob, mp.contact_recovery_prob
    assert esc["owner_supervisor"] == esc["programme_coordinator"] == esc["owner"]  # no benefit beyond re-contact
    assert rec["owner_supervisor"] == rec["programme_coordinator"] == rec["owner"]
    assert mp.human_dropout_residual_multiplier == mp.dropout_residual_multiplier
    assert 0.0 < mp.programme_reach < 1.0
    up = load_context(warn=False).mp
    assert up.programme_reach == 1.0 and up.sms_reminder_effect == 0.167  # the B18 fit (was 0.25, Lam 2021)


# ---- programme reach -------------------------------------------------------------------------------

def _hash(ctx, arm, seed=11, cases=300, days=200):
    return run_arm(ctx, arm, cases, days, seed, make_patients(ctx, cases, seed))


def _with_reach(tmp_path, reach):
    f = tmp_path / f"reach{reach}.yaml"
    f.write_text(f"orchestration:\n  programme_reach: {{value: {reach}, source: test, status: assumed}}\n")
    return load_context([DEFAULT_PARAMS, f], warn=False)


def test_reach_zero_reproduces_the_baseline_arm_exactly(tmp_path):
    zero = _hash(_with_reach(tmp_path, 0.0), "orchestrated")
    base = _hash(load_context(warn=False), "baseline")
    assert zero.event_hash == base.event_hash and zero.n_events == base.n_events
    assert not any(c.orch for c in zero.cases)


def test_reach_one_is_the_upper_range_and_partial_reach_sits_between(tmp_path):
    ctx1 = _with_reach(tmp_path, 1.0)
    assert _hash(ctx1, "orchestrated").event_hash == _hash(load_context(warn=False), "orchestrated").event_hash
    half = _hash(_with_reach(tmp_path, 0.5), "orchestrated", cases=600)
    share = sum(1 for c in half.cases if c.orch) / len(half.cases)
    assert 0.42 < share < 0.58
    done = lambda res: sum(1 for r in res.records if r.t_complete is not None) / len(res.records)  # noqa: E731
    lo = done(_hash(load_context(warn=False), "baseline", cases=600))
    hi = done(_hash(load_context(warn=False), "orchestrated", cases=600))
    assert lo < done(half) < hi


def test_unreached_cases_have_no_owner_timers_or_escalation(tmp_path):
    res = _hash(_with_reach(tmp_path, 0.5), "orchestrated", cases=600)
    unreached = [c for c in res.cases if not c.orch]
    assert unreached
    for c in unreached:
        types = {e.type for e in c.log}
        assert not types & {"escalated", "overdue", "reminder_sent", "watchdog_flagged"}
        assert all(not e.payload.get("owner") for e in c.log if e.type == "accept_ownership")


# ---- evaluate: structure and the shared baseline ----------------------------------------------------

def test_results_have_both_scenarios_and_keep_the_upper_range_keys(small_result):
    r = small_result
    assert set(r["scenarios"]) == {UPPER, LITERATURE}
    up, lit = r["scenarios"][UPPER], r["scenarios"][LITERATURE]
    # existing top-level keys are the upper-range scenario
    assert r["arms"] == up["arms"] and r["difference"] == up["difference"] and r["per_replication"] == up["per_replication"]
    assert r["meta"]["scenarios"] == [UPPER, LITERATURE]
    # same baseline, byte for byte, in both scenarios
    # (the in-reach rates of the baseline arm are scored on the cases the programme would reach, which depends on
    # programme_reach and so differs between scenarios; the baseline process itself, its event logs, does not)
    def process(arm):
        return {k: v for k, v in arm.items() if not k.endswith("_in_reach") and k != "in_reach_share"}

    assert process(up["arms"]["baseline"]) == process(lit["arms"]["baseline"])
    assert [p["arms"]["baseline"] for p in up["per_replication"]] == [p["arms"]["baseline"] for p in lit["per_replication"]]
    assert lit["params_hash"] != up["params_hash"]
    assert up["overrides"] == [] and {o["key"] for o in lit["overrides"]} >= {"sms_reminder_effect", "orchestration.programme_reach"}
    for o in lit["overrides"]:
        assert o["source"] and o["upper_range_value"] != o["value"]


def test_literature_anchored_orchestrated_sits_between_baseline_and_upper_range(small_result):
    sc = small_result["scenarios"]
    for k in ("completion_90d", "completion_180d"):
        b = sc[UPPER]["arms"]["baseline"][k]["mean"]
        lo = sc[LITERATURE]["arms"]["orchestrated"][k]["mean"]
        hi = sc[UPPER]["arms"]["orchestrated"][k]["mean"]
        assert b < lo < hi, k
    assert sc[LITERATURE]["arms"]["orchestrated"]["escalations_per_100_cases"]["mean"] < \
        sc[UPPER]["arms"]["orchestrated"]["escalations_per_100_cases"]["mean"]


def test_overlay_that_touches_the_baseline_is_refused(tmp_path):
    f = tmp_path / "leaky.yaml"
    f.write_text("baseline:\n  noshow_self_rebook_prob: {value: 0.9, source: test, status: assumed}\n")
    with pytest.raises(ValueError, match="changes the baseline arm"):
        evaluate(jobs=1, overlay_paths=[f], **SMALL)


def test_no_overlay_gives_the_upper_range_only():
    r = evaluate(jobs=1, overlay_paths=None, **{**SMALL, "reps": 2})
    assert set(r["scenarios"]) == {UPPER} and r["meta"]["scenarios"] == [UPPER]


def test_scenario_results_are_reproducible_across_job_counts(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    ctx = load_context(warn=False)
    write_results(evaluate(jobs=1, **SMALL), a, ctx)
    write_results(evaluate(jobs=2, **SMALL), b, ctx)
    assert (a / "results.json").read_bytes() == (b / "results.json").read_bytes()


def test_cli_flags(tmp_path, capsys):
    main(["evaluate", "--reps", "2", "--cases", "100", "--days", "120", "--bootstrap", "50", "--jobs", "1",
          "--out-dir", str(tmp_path / "both")])
    main(["evaluate", "--reps", "2", "--cases", "100", "--days", "120", "--bootstrap", "50", "--jobs", "1",
          "--no-conservative", "--out-dir", str(tmp_path / "upper")])
    both = json.loads((tmp_path / "both" / "results.json").read_text())
    upper = json.loads((tmp_path / "upper" / "results.json").read_text())
    assert set(both["scenarios"]) == {UPPER, LITERATURE} and set(upper["scenarios"]) == {UPPER}
    assert both["arms"] == upper["arms"]  # the upper-range numbers do not depend on the overlay being present


# ---- results.md leads with the headline table --------------------------------------------------------

def test_results_md_leads_with_the_three_column_headline(small_result, tmp_path):
    write_results(small_result, tmp_path, load_context(warn=False))
    md = (tmp_path / "results.md").read_text()
    first_section = md.split("\n## ")[1]
    assert first_section.startswith("Headline")
    assert "| Metric | Baseline | Orchestrated, literature anchored | Orchestrated, upper range |" in first_section
    assert "literature-anchored scenario" in first_section and "mechanism ceiling" in first_section
    assert "Quote the literature-anchored scenario" in first_section
    assert md.index("## Headline") < md.index("## Summary") < md.index("## Arm comparison")


# ---- the committed results agree with the navigation risk ratio -------------------------------------

def test_committed_literature_scenario_matches_the_navigation_risk_ratio():
    res = json.loads((REPO / "docs" / "results" / "results.json").read_text())
    nav = next(p["value"] for p in res["parameters"] if p["key"] == "navigation_effect")
    sc = res["scenarios"]
    b = sc[LITERATURE]["arms"]["baseline"]["completion_180d"]["mean"]
    o = sc[LITERATURE]["arms"]["orchestrated"]["completion_180d"]["mean"]
    # B21: programme_reach is deliberately not refitted to 1.27 after the timing mixture (the pooled RR probably includes two of the
    # validation trials, so refitting would be circular; docs/results/validation.md). The ratio must stay inside the pooled 95% CI.
    assert nav == 1.27 and 1.10 <= o / b <= 1.46, "literature-anchored ratio left the pooled navigation CI (1.10 to 1.46)"
    reach = next(x["value"] for x in sc[LITERATURE]["overrides"] if x["key"] == "orchestration.programme_reach")
    assert reach == 0.55, "programme_reach changed: B21 keeps the B12 fit; record any refit and its source in validation.md"
    up = sc[UPPER]["arms"]["orchestrated"]["completion_180d"]["mean"] / b
    assert up > 1.5  # the upper range is the mechanism ceiling


def test_no_todo_in_generated_docs():
    for name in ("results.md", "results.json"):
        assert "TODO" not in (REPO / "docs" / "results" / name).read_text(), name
    assert "TODO" not in DEFAULT_PARAMS.read_text()
