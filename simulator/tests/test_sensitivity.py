"""Ticket B14: the one-at-a-time sensitivity sweep (`make sensitivity`)."""

import json
from pathlib import Path

import pytest

from simulator.sim.params import ParamError, load_params, parse_params
from simulator.sim.runner import context_from_params
from simulator.sim.sensitivity import (DEFAULTS, NOT_QUANTITIES, RANK_METRIC, default_stack, not_swept, swept, sweep,
                                       write_outputs)
from simulator.sim.sensitivity_report import render_markdown, render_svg
from simulator.sim.sensitivity_rules import RULE_BASES, STATED_PREFIX, default_range

RESULTS = Path(__file__).resolve().parents[2] / "docs" / "results"


@pytest.fixture(scope="module")
def stack():
    return load_params(*default_stack(), warn=False)


@pytest.fixture(scope="module")
def committed():
    return json.loads((RESULTS / "sensitivity.json").read_text())


# ---- ranges ----------------------------------------------------------------------------------------------------

def test_every_assumed_or_calibrated_parameter_has_a_range_unless_it_is_not_a_quantity(stack):
    missing = [p.key for p in stack.rows() if p.status in ("assumed", "calibrated") and p.range is None and p.key not in NOT_QUANTITIES]
    assert not missing, f"add range and range_basis (see simulator/sim/sensitivity_rules.py): {missing}"
    assert set(NOT_QUANTITIES) <= {p.key for p in stack.rows() if p.range is None}


def test_every_swept_key_has_a_range_that_contains_its_value_and_says_where_it_comes_from(stack):
    sw = swept(stack)
    assert len(sw) >= 90
    assert {p.key for p in sw}.isdisjoint(n["key"] for n in not_swept(stack))
    for p in sw:
        assert p.range_basis, p.key
        assert p.range_basis.startswith(STATED_PREFIX) or p.range_basis in RULE_BASES.values(), p.key
        lo, hi = p.range
        assert lo != hi, f"{p.key}: empty range"


def test_the_sourced_parameters_that_are_swept_are_exactly_those_with_a_stated_range(stack):
    sourced = {p.key for p in swept(stack) if p.status.startswith("sourced")}
    assert sourced == {"referral_rejection_rate", "wrong_contact_rate", "sms_reminder_effect"}
    for p in swept(stack):
        if p.status.startswith("sourced"):
            assert p.range_basis.startswith(STATED_PREFIX)


def test_default_rule_ranges_are_what_the_labelled_rule_gives_for_the_effective_value(stack):
    for p in swept(stack):
        if p.range_basis.startswith(STATED_PREFIX):
            continue
        want = default_range(p.range_basis, p.key, p.value)
        got = [list(r) if isinstance(r, tuple) else r for r in p.range]
        assert got == want, f"{p.key}: range {got} is not the {p.range_basis!r} range {want} of {p.value}"


def test_every_end_of_every_range_is_a_valid_model(stack):
    for p in swept(stack):
        for i, end in enumerate(p.range):
            v = list(end) if isinstance(end, tuple) else end
            context_from_params(stack.with_value(p.key, v))  # raises ParamError if the model rejects the value


def test_ranges_do_not_change_the_parameter_hash_or_the_simulator(stack):
    plain = {k: v for k, v in ((p.key, p.value) for p in stack.rows())}
    again = load_params(*default_stack(), warn=False)
    assert again.hash() == stack.hash() and plain == {p.key: p.value for p in again.rows()}
    changed = stack.with_value("staff.forget_prob", 0.02)
    assert changed.param("staff.forget_prob").range == stack.param("staff.forget_prob").range
    assert changed.sweep_hash() != stack.sweep_hash()


def test_range_validation():
    ok = {"a": {"value": 2, "source": "s", "status": "assumed", "range": [1, 4], "range_basis": "b"}}
    assert parse_params(ok)["a"].range == (1, 4)
    for bad in (
        {"a": {"value": 2, "source": "s", "status": "assumed", "range": [1, 4]}},                            # no basis
        {"a": {"value": 5, "source": "s", "status": "assumed", "range": [1, 4], "range_basis": "b"}},         # value outside
        {"a": {"value": 2, "source": "s", "status": "assumed", "range": [4, 1], "range_basis": "b"}},         # unordered
        {"a": {"value": 2, "source": "s", "status": "assumed", "range": [1], "range_basis": "b"}},            # not a pair
        {"a": {"value": 2, "source": "s", "status": "assumed", "range_basis": "b"}},                          # basis only
    ):
        with pytest.raises(ParamError):
            parse_params(bad)
    lst = {"a": {"value": [7, 1], "source": "s", "status": "assumed", "range": [[3, 0.5], [14, 2]], "range_basis": "b"}}
    assert parse_params(lst)["a"].range == ((3, 0.5), (14, 2))


# ---- the sweep ---------------------------------------------------------------------------------------------------

SMALL = dict(reps=2, cases=150, days=200, seed=11, keys=["staff.forget_prob", "patients.refusal_prob", "orchestration.programme_reach"])


def test_sweep_is_deterministic_and_independent_of_the_job_count(tmp_path):
    a = sweep(jobs=1, **SMALL)
    b = sweep(jobs=2, **SMALL)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert [r["key"] for r in a["parameters"]] == sorted((r["key"] for r in a["parameters"]),
                                                          key=lambda k: (-next(r for r in a["parameters"] if r["key"] == k)["swing"][RANK_METRIC], k))
    write_outputs(a, tmp_path / "a")
    write_outputs(b, tmp_path / "b")
    for name in ("sensitivity.json", "sensitivity.md", "sensitivity.svg"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    assert "generated_at" not in (tmp_path / "a" / "sensitivity.json").read_text()
    assert sweep(jobs=1, **{**SMALL, "seed": 12}) != a


def test_sweep_moves_one_parameter_at_a_time_and_swing_is_the_span():
    r = sweep(jobs=1, **SMALL)
    base = r["base"]["metrics"][RANK_METRIC]["difference"]
    assert r["meta"]["n_swept"] == 3 and r["meta"]["n_points"] == 7
    for row in r["parameters"]:
        ds = [row["low"]["metrics"][RANK_METRIC]["difference"], base, row["high"]["metrics"][RANK_METRIC]["difference"]]
        assert row["swing"][RANK_METRIC] == pytest.approx(max(ds) - min(ds), abs=3e-6)
    reach = next(x for x in r["parameters"] if x["key"] == "orchestration.programme_reach")
    # a higher programme reach means more cases get the orchestrated process: a larger gain over baseline
    assert reach["high"]["metrics"][RANK_METRIC]["difference"] > reach["low"]["metrics"][RANK_METRIC]["difference"]
    # the baseline arm does not read programme_reach
    assert reach["low"]["metrics"][RANK_METRIC]["baseline"] == reach["high"]["metrics"][RANK_METRIC]["baseline"]


# ---- the committed outputs -----------------------------------------------------------------------------------------

def test_committed_json_is_the_sweep_of_the_current_parameters_and_ranges(committed, stack):
    meta = committed["meta"]
    assert meta["params_hash"] == stack.sweep_hash(), "parameters or ranges changed: rerun `make sensitivity`"
    assert (meta["replications"], meta["cases_per_replication"], meta["follow_up_days"], meta["seed"]) == (
        DEFAULTS["reps"], DEFAULTS["cases"], DEFAULTS["days"], DEFAULTS["seed"])
    assert [r["key"] for r in committed["parameters"]].__len__() == meta["n_swept"] == len(swept(stack))
    assert {r["key"] for r in committed["parameters"]} == {p.key for p in swept(stack)}
    assert {n["key"] for n in committed["not_swept"]} == {n["key"] for n in not_swept(stack)}
    swings = [r["swing"][RANK_METRIC] for r in committed["parameters"]]
    assert swings == sorted(swings, reverse=True)


def test_committed_json_is_canonical(committed):
    text = (RESULTS / "sensitivity.json").read_text()
    assert json.dumps(committed, sort_keys=True, indent=2, ensure_ascii=False) + "\n" == text
    assert "generated_at" not in text and "git_commit" not in text


def test_committed_summary_matches_the_rows(committed):
    rows, s = committed["parameters"], committed["summary"]
    pts = [(r["key"], e, r[e]["metrics"][RANK_METRIC]["difference"]) for r in rows for e in ("low", "high")]
    assert s["headline_difference_min"]["difference"] == min(d for _, _, d in pts)
    assert s["headline_difference_max"]["difference"] == max(d for _, _, d in pts)
    assert s["top5"] == [r["key"] for r in rows[:5]]
    assert len(s["flips"][RANK_METRIC]) == sum(1 for _, _, d in pts if d < 0)


def test_committed_markdown_and_svg_match_the_json(committed):
    md = (RESULTS / "sensitivity.md").read_text()
    assert md == render_markdown(committed)
    assert (RESULTS / "sensitivity.svg").read_text() == render_svg(committed)
    # every parameter, in order, and the numbers of its row
    rows = [line for line in md.splitlines() if line.startswith("| ") and "|" in line and line.split("|")[2].strip().startswith("`")]
    table = [r for r in rows if r.split("|")[1].strip().isdigit()]
    assert [r.split("|")[2].strip() for r in table] == [f"`{p['key']}`" for p in committed["parameters"]]
    for r, p in zip(table, committed["parameters"]):
        cells = [c.strip() for c in r.strip().strip("|").split("|")]
        assert cells[7] == f"{100 * p['swing'][RANK_METRIC]:.1f}"
        assert cells[5] == f"{100 * p['low']['metrics'][RANK_METRIC]['difference']:+.1f}"
    for k in committed["summary"]["top5"]:
        assert f"`{k}`" in md.split("## Tornado")[0]
    assert "Does the direction ever flip?" in md and "Range of the headline difference" in md
    assert md.count("![") == 1 and "(sensitivity.svg)" in md


def test_svg_is_well_formed_and_hand_written(committed):
    import xml.dom.minidom

    svg = (RESULTS / "sensitivity.svg").read_text()
    xml.dom.minidom.parseString(svg)
    assert svg.startswith("<svg") and "<title" in svg and "<desc" in svg
    for k in committed["summary"]["top5"]:
        assert k in svg


# ---- the orchestrated-only shortcut -----------------------------------------------------------------------------------

def test_baseline_invariant_parameters_really_leave_the_baseline_event_log_unchanged(stack):
    from simulator.sim.evaluate import rep_seed
    from simulator.sim.runner import make_patients, run_arm
    from simulator.sim.sensitivity import BASELINE_INVARIANT, _end_value

    assert BASELINE_INVARIANT <= {p.key for p in swept(stack)}
    seeds = [rep_seed(3, i) for i in range(2)]

    def hashes(params):
        ctx = context_from_params(params)
        return [run_arm(ctx, "baseline", 250, 300, s, make_patients(ctx, 250, s)).event_hash for s in seeds]

    base = hashes(stack)
    for key in sorted(BASELINE_INVARIANT):
        p = stack.param(key)
        for end in ("low", "high"):
            assert hashes(stack.with_value(key, _end_value(p, end))) == base, f"{key} at its {end} end changes the baseline arm"
    assert not any(k.startswith("metrics.") for k in BASELINE_INVARIANT)


def test_skipping_the_baseline_arm_for_invariant_parameters_changes_no_number(monkeypatch):
    from simulator.sim import sensitivity

    kw = dict(reps=2, cases=120, days=200, seed=5, jobs=1, keys=["orchestration.programme_reach", "sms_reminder_effect", "staff.forget_prob"])
    fast = sweep(**kw)
    monkeypatch.setattr(sensitivity, "BASELINE_INVARIANT", frozenset())
    full = sweep(**kw)
    assert json.dumps(fast, sort_keys=True) == json.dumps(full, sort_keys=True)
