"""Evaluation under finite capacity (ticket B19, milestone M6): `make evaluate-capacity` -> docs/results/capacity.json.

The same replications, cohorts and random numbers as `evaluate`, for both scenarios (upper range and literature anchored),
each run

  * unconstrained    the parameter stack as `evaluate` runs it (the reference);
  * at base capacity the stack plus simulator/params.capacity.yaml (finite colonoscopy slots and escalation-handling minutes);
  * capacity sensitivity   slot supply at 70, 85, 100 and 115% of base (staffing at base), both arms;
  * decomposition          the orchestrated arm with only the slots limited, and with only the staff limited;
  * staffing sensitivity   escalation budgets at 50, 75, 100, 150, 200 and 300% of base (slots at base), orchestrated arm only
                           (the baseline arm raises no escalations, so its run is the base-capacity one, enforced by a test).

Metrics: the headline outcomes under capacity, the slot metrics (utilisation, request-to-slot wait) and the escalation-queue metrics
(queue length, delay, the staff budgets' utilisation); definitions in docs/results/metrics.md section 12. Aggregation is
`evaluate`'s (percentile bootstrap over replications, differences paired by replication), and the file has sorted keys and no
timestamps, so two runs give the same bytes whatever the process count.
"""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from . import DEFAULT_CONSERVATIVE, DEFAULT_PARAMS
from .engine import ARMS, BASELINE, ORCHESTRATED
from .evaluate import LITERATURE, UPPER, SCENARIO_LABELS, _round, _summ, rep_seed
from .params import load_params
from .runner import context_from_params, git_commit, make_patients, metrics_of, run_arm

DEFAULT_CAPACITY = DEFAULT_PARAMS.with_name("params.capacity.yaml")
SCHEMA_VERSION = 1
CAPACITY_LEVELS = (0.70, 0.85, 1.00, 1.15)
STAFFING_LEVELS = (0.50, 0.75, 1.00, 1.50, 2.00, 3.00)
HUGE = 1000.0  # a limit "too large to bind" (decomposition runs)

# outcome metrics reported under capacity (definitions: docs/results/metrics.md sections 1 to 10)
OUTCOME_KEYS = (
    "completion_90d", "completion_180d", "completion_360d",
    "time_to_colonoscopy_median", "time_to_colonoscopy_km_median", "time_to_colonoscopy_rmst",
    "open_90d", "closed_share", "time_to_closure_median",
    "lost_to_follow_up_rate", "silent_loss_rate", "delay_harm_share",
    "escalations_per_100_cases", "escalation_acted_on_rate",
)
# capacity metrics (docs/results/metrics.md section 12)
SLOT_KEYS = (
    "slots_offered", "slots_booked", "slot_utilisation", "slot_used_share", "bookings_per_100_cases", "cancellations_per_100_cases",
    "dna_backfilled_share", "request_to_slot_wait_median", "request_to_slot_wait_p90", "capacity_delay_mean", "capacity_delay_p90",
    "booked_late_share",
)
QUEUE_KEYS = (
    "esc_jobs_per_100_cases", "esc_delay_mean", "esc_delay_p90", "esc_delayed_share", "esc_stale_share", "esc_queue_mean",
    "esc_queue_max", "esc_supervisor_utilisation", "esc_coordinator_utilisation",
)
CAPACITY_KEYS = SLOT_KEYS + QUEUE_KEYS
ALL_KEYS = OUTCOME_KEYS + CAPACITY_KEYS


def _key(level: float) -> str:
    return f"{round(100 * level):d}"


def _stacks(params_paths, conservative_paths, capacity_path) -> list[tuple[str, list]]:
    base = list(params_paths) if params_paths else [DEFAULT_PARAMS]
    return [(UPPER, base), (LITERATURE, base + list(conservative_paths))] if conservative_paths else [(UPPER, base)]


def _context(stack: list, capacity_path, scale: float | None = None, staff_scale: float | None = None, pathway_path=None):
    paths = list(stack) + ([capacity_path] if capacity_path else [])
    params = load_params(*paths, warn=False)
    if scale is not None:
        params = params.with_value("capacity.scale", float(scale))
    if staff_scale is not None:
        params = params.with_value("capacity.staff.scale", float(staff_scale))
    return context_from_params(params, pathway_path)


def _arm_metrics(ctx, res) -> dict:
    m = metrics_of(ctx, res)
    out = {k: m.get(k) for k in OUTCOME_KEYS}
    if res.capacity is not None:
        out.update(res.capacity.metrics())
    return out


def _run_rep(job: tuple) -> dict:
    stacks, capacity_path, pathway_path, rep, seed, cases, days = job
    out = {"rep": rep, "seed": seed, "scenarios": {}}
    for name, stack in stacks:
        ref_ctx = _context(stack, None, pathway_path=pathway_path)
        patients = make_patients(ref_ctx, cases, seed)
        sc: dict = {"unconstrained": {}, "capacity": {}, "staffing": {}}
        for arm in ARMS:
            sc["unconstrained"][arm] = _arm_metrics(ref_ctx, run_arm(ref_ctx, arm, cases, days, seed, patients))
        for level in CAPACITY_LEVELS:
            ctx = _context(stack, capacity_path, scale=level, pathway_path=pathway_path)
            sc["capacity"][_key(level)] = {}
            for arm in ARMS:
                res = run_arm(ctx, arm, cases, days, seed, patients)
                sc["capacity"][_key(level)][arm] = {**_arm_metrics(ctx, res), "event_hash": res.event_hash[:16]}
        for level in STAFFING_LEVELS:
            if level == 1.0:
                sc["staffing"][_key(level)] = {ORCHESTRATED: sc["capacity"]["100"][ORCHESTRATED]}
                continue
            ctx = _context(stack, capacity_path, staff_scale=level, pathway_path=pathway_path)
            res = run_arm(ctx, ORCHESTRATED, cases, days, seed, patients)
            sc["staffing"][_key(level)] = {ORCHESTRATED: {**_arm_metrics(ctx, res), "event_hash": res.event_hash[:16]}}
        # decomposition: which of the two limits does what (orchestrated arm only; the other limit is made too large to bind)
        for tag, kw in (("slots_only", {"staff_scale": HUGE}), ("staff_only", {"scale": HUGE})):
            ctx = _context(stack, capacity_path, pathway_path=pathway_path, **kw)
            res = run_arm(ctx, ORCHESTRATED, cases, days, seed, patients)
            sc[tag] = {ORCHESTRATED: {**_arm_metrics(ctx, res), "event_hash": res.event_hash[:16]}}
        out["scenarios"][name] = sc
    return out


def _agg_arm(reps: list[dict], pick, seed: int, tag: tuple, resamples: int) -> dict:
    n = len(reps)
    out = {}
    for k in ALL_KEYS:
        vals = [pick(r).get(k) for r in reps]
        out[k] = _summ([v for v in vals if v is not None], (seed, "bootstrap", "capacity", *tag, k), resamples, n)
    return out


def _agg_diff(reps: list[dict], pick_o, pick_b, seed: int, tag: tuple, resamples: int, keys=ALL_KEYS) -> dict:
    n = len(reps)
    out = {}
    for k in keys:
        paired = []
        for r in reps:
            o, b = pick_o(r).get(k), pick_b(r).get(k)
            if o is not None and b is not None:
                paired.append(o - b)
        out[k] = _summ(paired, (seed, "bootstrap", "capacity", "diff", *tag, k), resamples, n)
    return out


def _block(reps, name, seed, resamples, get) -> dict:
    """Both arms and their paired difference for one configuration; `get(rep, arm)` returns that arm's metrics."""
    arms = {a: _agg_arm(reps, lambda r, a=a: get(r, a), seed, (name, a), resamples) for a in ARMS}
    diff = _agg_diff(reps, lambda r: get(r, ORCHESTRATED), lambda r: get(r, BASELINE), seed, (name,), resamples)
    return {"arms": arms, "difference": diff}


def evaluate_capacity(reps: int = 20, cases: int = 2000, days: int = 360, seed: int = 20261017, params_paths=None,
                      conservative_paths=(DEFAULT_CONSERVATIVE,), capacity_path=DEFAULT_CAPACITY, pathway_path=None,
                      resamples: int = 1000, jobs: int = 1) -> dict:
    stacks = _stacks(params_paths, list(conservative_paths or []), capacity_path)
    job_list = [(stacks, str(capacity_path), pathway_path, i, rep_seed(seed, i), cases, days) for i in range(reps)]
    if jobs > 1 and reps > 1:
        with ProcessPoolExecutor(max_workers=min(jobs, reps)) as ex:
            rr = list(ex.map(_run_rep, job_list))
    else:
        rr = [_run_rep(j) for j in job_list]

    scenarios = {}
    for name, stack in stacks:
        def at(level_key, kind="capacity"):
            return lambda r, a: r["scenarios"][name][kind][level_key][a]

        unc = _block(rr, f"{name}|unconstrained", seed, resamples, lambda r, a: r["scenarios"][name]["unconstrained"][a])
        cap = {_key(lv): _block(rr, f"{name}|capacity|{_key(lv)}", seed, resamples, at(_key(lv))) for lv in CAPACITY_LEVELS}
        staffing = {}
        for lv in STAFFING_LEVELS:
            k = _key(lv)
            get_o = lambda r, k=k: r["scenarios"][name]["staffing"][k][ORCHESTRATED]  # noqa: E731
            get_b = lambda r: r["scenarios"][name]["capacity"]["100"][BASELINE]  # noqa: E731
            staffing[k] = {
                "arm": _agg_arm(rr, get_o, seed, (name, "staffing", k), resamples),
                "difference": _agg_diff(rr, get_o, get_b, seed, (name, "staffing", k), resamples),
                "event_hash": [get_o(r).get("event_hash") for r in rr],
            }
        decomposition = {}
        for tag, ref in (("slots_only", lambda r: r["scenarios"][name]["capacity"]["100"][BASELINE]),
                         ("staff_only", lambda r: r["scenarios"][name]["unconstrained"][BASELINE])):
            get_o = lambda r, tag=tag: r["scenarios"][name][tag][ORCHESTRATED]  # noqa: E731
            decomposition[tag] = {
                "arm": _agg_arm(rr, get_o, seed, (name, tag), resamples),
                "difference": _agg_diff(rr, get_o, ref, seed, (name, tag), resamples),
            }
        ctx = _context(stack, capacity_path, pathway_path=pathway_path)
        scenarios[name] = {
            "label": SCENARIO_LABELS[name],
            "params_files": [Path(p).name for p in ctx.params.files],
            "params_hash": ctx.params.hash(),
            "unconstrained": unc,
            "base": cap["100"],
            "capacity_sensitivity": cap,
            "staffing_sensitivity": staffing,
            "decomposition": decomposition,
            "baseline_hash_by_rep": [r["scenarios"][name]["capacity"]["100"][BASELINE]["event_hash"] for r in rr],
        }
    ref = _context(stacks[0][1], capacity_path, pathway_path=pathway_path)
    cp = ref.mp.capacity
    result = {
        "meta": {
            "schema_version": SCHEMA_VERSION, "seed": seed, "replications": reps, "cases_per_replication": cases,
            "follow_up_days": days, "bootstrap_resamples": resamples, "git_commit": git_commit(),
            "capacity_levels": list(CAPACITY_LEVELS), "staffing_levels": list(STAFFING_LEVELS),
            "utilisation_window_days": [cp.utilisation_window_start_day, cp.utilisation_window_end_day],
            "scenarios": [n for n, _ in stacks],
            "ci_method": "percentile bootstrap over replications; differences are paired by replication (common random numbers)",
        },
        "parameters": [
            {"key": p.key, "value": p.value, "source": p.source, "status": p.status, "notes": p.notes}
            for p in ref.params.rows() if p.key.startswith("capacity.")
        ],
        "scenarios": scenarios,
    }
    return _round(result)


def write_capacity(result: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "capacity.json"
    p.write_text(json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False) + "\n")
    return p
