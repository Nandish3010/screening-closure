"""One-at-a-time parameter sensitivity sweep (ticket B14): `make sensitivity`.

Base case: the literature-anchored scenario (simulator/params.yaml with the overlay simulator/params.conservative.yaml
on top). Every parameter whose effective leaf carries a `range: [low, high]` is moved to its low end and to its high
end, one at a time, with everything else held at the base case. Which parameters have a range, and why: a parameter
with status `assumed` or `calibrated`, or a sourced one with a range stated in its source, gets one (the rules for
making a range are in sensitivity_rules.py); the rest are listed in the output as not swept, with the reason.

For each point (base, and each parameter at each end) the sweep runs `reps` replications of `cases` cases per arm,
the same patients and random numbers at every point (common random numbers), and records three outcomes per arm:
completion within 180 days, share still open at 90 days, and silent loss (lost and never surfaced to a human, all
cases). The headline is the orchestrated-minus-baseline difference of the first. The swing of a parameter is the
span of that difference over its low end, the base case and its high end; the tornado ranks parameters by it.

Output: sensitivity.json (sorted keys, floats rounded to 6 places, no timestamps, no timings, no git commit, so a
rerun is byte-identical), plus sensitivity.md and sensitivity.svg rendered from it (sensitivity_report.py).
"""

from __future__ import annotations

import functools
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from . import DEFAULT_CONSERVATIVE, DEFAULT_PARAMS
from .engine import ARMS, BASELINE, ORCHESTRATED
from .evaluate import _round, rep_seed
from .params import Param, Params, load_params
from .runner import context_from_params, make_patients, metrics_of, run_arm
from .sensitivity_rules import RULE_BASES, STATED_PREFIX, default_range

SCHEMA_VERSION = 1
METRICS = ("completion_180d", "open_90d", "silent_loss_rate")
RANK_METRIC = "completion_180d"
SCENARIO = "literature_anchored"
DEFAULTS = dict(reps=5, cases=1000, days=360, seed=20261017)

# Parameters with status assumed or calibrated that are not quantities, so have no range.
NOT_QUANTITIES = {
    "simulation.start_date": "a calendar date (it must be a working day), not a quantity",
    "calendar.holidays": "a list of dates, empty in the base case",
    "baseline.default_language": "a category (the language of the baseline's single letter), not a quantity",
    "reopen.resume_state": "a pathway state name, not a quantity",
}
SOURCED_NO_RANGE = "sourced; its source states no plausible range"

# Parameters the BASELINE arm never reads: moving one leaves every baseline event log unchanged, so the sweep runs only the
# orchestrated arm at those points and takes the baseline numbers from the base case (about a sixth of the run time).
# A test (test_sensitivity.py) moves each of them to both ends and checks that the baseline event-log hash does not change.
# The `metrics.*` parameters are not in the list: they change how the baseline's log is scored, not the log.
BASELINE_INVARIANT = frozenset("""
appointment.reminder_days_before handoff.declined_reengage_prob handoff.reroute_delay_median_days handoff.response_sla_working_days
orchestration.consent_review_delay_median_days orchestration.contact_recovery_delay_median_days orchestration.contact_recovery_prob.on_failure
orchestration.contact_recovery_prob.owner orchestration.contact_recovery_prob.owner_supervisor orchestration.contact_recovery_prob.programme_coordinator
orchestration.delivery_failure_detect_days.letter orchestration.delivery_failure_detect_days.phone orchestration.delivery_failure_detect_days.sms
orchestration.delivery_failure_detect_days.whatsapp orchestration.escalation_effect_prob.owner orchestration.escalation_effect_prob.owner_supervisor
orchestration.escalation_effect_prob.programme_coordinator orchestration.ladder_exhausted_wait_working_days orchestration.programme_reach
orchestration.reaction_delay_median_days orchestration.signoff_sla_working_days orchestration.timer_miss_prob orchestration.transfer_reestablish_days
orchestration.watchdog_effect_prob orchestration.watchdog_idle_days orchestration.watchdog_reaction_delay_days
patient_response.coordinator_route_response_rate patient_response.dropout_residual_multiplier patient_response.human_dropout_residual_multiplier
patient_response.response_delay_median_days.phone patient_response.response_delay_median_days.whatsapp
patient_response.response_rate_by_channel.phone patient_response.response_rate_by_channel.whatsapp patient_response.staff_call_response_rate
patients.channel_share.letter patients.channel_share.phone patients.channel_share.sms patients.channel_share.whatsapp
patients.dropout_prob_per_lapse sms_reminder_effect
""".split())


def default_stack() -> list[Path]:
    return [DEFAULT_PARAMS, DEFAULT_CONSERVATIVE]


def swept(params: Params) -> list[Param]:
    return [p for p in params.rows() if p.range is not None]


def not_swept(params: Params) -> list[dict]:
    out = []
    for p in params.rows():
        if p.range is not None:
            continue
        if p.status in ("assumed", "calibrated"):
            reason = NOT_QUANTITIES.get(p.key, "no range defined")
        else:
            reason = SOURCED_NO_RANGE
        out.append({"key": p.key, "status": p.status, "reason": reason})
    return out


def _end_value(p: Param, end: str):
    v = p.range[0 if end == "low" else 1]
    return list(v) if isinstance(v, tuple) else v


@functools.lru_cache(maxsize=4)
def _base_params(stack: tuple[str, ...]) -> Params:
    return load_params(*stack, warn=False)


def _point_job(job: tuple) -> tuple:
    stack, pathway, key, end, value, rep, seed, cases, days, arms = job
    params = _base_params(stack)
    if key is not None:
        params = params.with_value(key, value)
    ctx = context_from_params(params, pathway)
    patients = make_patients(ctx, cases, seed)
    out = {}
    for arm in arms:
        m = metrics_of(ctx, run_arm(ctx, arm, cases, days, seed, patients))
        out[arm] = {k: m[k] for k in METRICS}
    return key, end, rep, out


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _point(reps_out: list[dict]) -> dict:
    """{metric: {baseline, orchestrated, difference}} as means over replications (difference paired by replication)."""
    res = {}
    for m in METRICS:
        pairs = [(r[BASELINE][m], r[ORCHESTRATED][m]) for r in reps_out if r[BASELINE][m] is not None and r[ORCHESTRATED][m] is not None]
        res[m] = {
            "baseline": _mean([b for b, _ in pairs]), "orchestrated": _mean([o for _, o in pairs]),
            "difference": _mean([o - b for b, o in pairs]),
        }
    return res


def sweep(reps: int = 5, cases: int = 1000, days: int = 360, seed: int = 20261017, jobs: int = 1,
          params_paths: list | None = None, pathway_path: str | None = None, keys: list[str] | None = None) -> dict:
    """Run the sweep and return the result dict (what sensitivity.json holds). `keys` restricts the swept parameters
    (tests only; the committed output sweeps every parameter that has a range)."""
    stack = tuple(str(p) for p in (params_paths or default_stack()))
    base = _base_params(stack)
    chosen = [p for p in swept(base) if keys is None or p.key in keys]
    seeds = [rep_seed(seed, i) for i in range(reps)]
    todo = [(stack, pathway_path, None, "base", None, i, seeds[i], cases, days, ARMS) for i in range(reps)]
    for p in chosen:
        arms = (ORCHESTRATED,) if p.key in BASELINE_INVARIANT else ARMS
        for end in ("low", "high"):
            val = _end_value(p, end)
            todo += [(stack, pathway_path, p.key, end, val, i, seeds[i], cases, days, arms) for i in range(reps)]
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            done = list(ex.map(_point_job, todo, chunksize=4))
    else:
        done = [_point_job(j) for j in todo]
    base_by_rep = {rep: out for key, _, rep, out in done if key is None}
    grouped: dict[tuple, list[tuple[int, dict]]] = {}
    for key, end, rep, out in done:
        if BASELINE not in out:  # orchestrated-only point: the baseline arm is the base case's
            out = {BASELINE: base_by_rep[rep][BASELINE], **out}
        grouped.setdefault((key, end), []).append((rep, out))
    pts = {k: _point([o for _, o in sorted(v, key=lambda t: t[0])]) for k, v in grouped.items()}
    base_pt = pts[(None, "base")]

    # noise floor: spread of the per-replication headline difference at the base case
    diffs = [o[ORCHESTRATED][RANK_METRIC] - o[BASELINE][RANK_METRIC] for _, o in sorted(grouped[(None, "base")], key=lambda t: t[0])]
    mean_d = sum(diffs) / len(diffs)
    sd = (sum((d - mean_d) ** 2 for d in diffs) / (len(diffs) - 1)) ** 0.5 if len(diffs) > 1 else 0.0

    rows = []
    for p in chosen:
        low, high = pts[(p.key, "low")], pts[(p.key, "high")]
        swing = {}
        for m in METRICS:
            ds = [low[m]["difference"], base_pt[m]["difference"], high[m]["difference"]]
            swing[m] = max(ds) - min(ds)
        rows.append({
            "key": p.key, "status": p.status, "base_value": p.value, "range_basis": p.range_basis,
            "low": {"value": _end_value(p, "low"), "metrics": low},
            "high": {"value": _end_value(p, "high"), "metrics": high},
            "swing": swing,
        })
    rows.sort(key=lambda r: (-r["swing"][RANK_METRIC], r["key"]))

    points = [(r["key"], end, r[end]["metrics"]) for r in rows for end in ("low", "high")]
    ranked = sorted(points, key=lambda t: (t[2][RANK_METRIC]["difference"], t[0], t[1]))
    lo_k, lo_e, lo_m = ranked[0]
    hi_k, hi_e, hi_m = ranked[-1]

    def flips(metric: str, worse_if_negative: bool) -> list[dict]:
        out = []
        for k, e, m in points:
            d = m[metric]["difference"]
            if (d < 0) if worse_if_negative else (d > 0):
                out.append({"key": k, "end": e, "difference": d})
        return out

    result = {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "design": "one at a time: each parameter with a range moved to its low end and to its high end, all others held at the base case",
            "scenario": SCENARIO,
            "seed": seed, "replications": reps, "cases_per_replication": cases, "follow_up_days": days,
            "params_files": [Path(f).name for f in stack],
            "params_hash": base.sweep_hash(),
            "pathway": Path(pathway_path).name if pathway_path else "colorectal_fit.yaml",
            "rank_metric": RANK_METRIC, "metrics": list(METRICS),
            "swing_definition": "max minus min of the orchestrated-minus-baseline difference over the parameter's low end, the base case and its high end",
            "default_rules": dict(sorted(RULE_BASES.items())), "stated_range_prefix": STATED_PREFIX,
            "n_swept": len(rows), "n_points": len(points) + 1,
        },
        "base": {
            "metrics": base_pt,
            "headline_difference_sd_across_replications": sd,
            "headline_difference_se": sd / len(diffs) ** 0.5,
        },
        "parameters": rows,
        "not_swept": not_swept(base) if keys is None else [],
        "summary": {
            "top5": [r["key"] for r in rows[:5]],
            "headline_difference_min": {"key": lo_k, "end": lo_e, "difference": lo_m[RANK_METRIC]["difference"]},
            "headline_difference_max": {"key": hi_k, "end": hi_e, "difference": hi_m[RANK_METRIC]["difference"]},
            "flips": {
                RANK_METRIC: flips(RANK_METRIC, True),
                "open_90d": flips("open_90d", False),
                "silent_loss_rate": flips("silent_loss_rate", False),
            },
        },
    }
    return _round(result)


def write_outputs(result: dict, out_dir: Path) -> list[Path]:
    from .sensitivity_report import render_markdown, render_svg

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [out_dir / "sensitivity.json", out_dir / "sensitivity.md", out_dir / "sensitivity.svg"]
    paths[0].write_text(json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False) + "\n")
    paths[1].write_text(render_markdown(result))
    paths[2].write_text(render_svg(result))
    return paths
