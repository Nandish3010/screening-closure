"""Replicated evaluation: N replications per arm, bootstrap 95% CIs, results.json + results.md.

Two scenarios share one cohort per replication and one baseline arm:
  upper_range          the parameter files as given (params.yaml plus any user layers): the mechanism ceiling;
  literature_anchored  the same files with the conservative overlay on top (params.conservative.yaml): the
                       orchestrated arm's behavioural effects are capped at literature levels.
The top-level keys of results.json (`meta`, `arms`, `difference`, `per_replication`, `parameters`) describe the
upper-range scenario, exactly as before the second scenario existed; `scenarios` holds both side by side.

Reproducibility: every random draw derives from the base seed, replications run independently (so
`--jobs` never changes a number), floats are rounded to 6 places and results.json has sorted keys,
no timestamps, no paths outside the repo and no timings. `results.md` carries a `generated_at`.
"""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from . import DEFAULT_CONSERVATIVE, REPO_ROOT
from .effort import aggregate_effort, arm_effort, model_description
from .engine import ARMS, BASELINE, ORCHESTRATED
from .metrics import percentile
from .rng import derive_seed, stream
from .runner import Context, git_commit, load_context, make_patients, metrics_of, run_arm

SCHEMA_VERSION = 2
UPPER, LITERATURE = "upper_range", "literature_anchored"
SCENARIO_LABELS = {
    UPPER: "Upper range (mechanism ceiling)",
    LITERATURE: "Literature anchored (orchestrated effects capped at published levels)",
}


def rep_seed(base_seed: int, rep: int) -> int:
    return derive_seed(base_seed, "replication", rep) & 0x7FFFFFFF


def _rel(path: str) -> str:
    p = Path(path).resolve()
    try:
        return str(p.relative_to(REPO_ROOT))
    except ValueError:
        return p.name


def _run_rep(job: tuple) -> dict:
    """One replication: one cohort per scenario (identical by construction), both arms. Top-level so it can
    run in a worker process."""
    scenario_paths, pathway_path, rep, seed, cases, days = job
    out = {"rep": rep, "seed": seed, "scenarios": {}}
    for name, params_paths in scenario_paths:
        ctx = load_context(params_paths, pathway_path, warn=False)
        patients = make_patients(ctx, cases, seed)
        arms = {}
        for arm in ARMS:
            res = run_arm(ctx, arm, cases, days, seed, patients)
            arms[arm] = {
                "metrics": metrics_of(ctx, res),
                "event_hash": res.event_hash[:16],
                "n_events": res.n_events,
                "counters": dict(res.counters),
                "effort": arm_effort(res.cases, ctx.mp.programme_reach, days),
            }
        out["scenarios"][name] = {"arms": arms}
    return out


def bootstrap_ci(values: list[float], seed_parts: tuple, resamples: int) -> tuple[float, float]:
    rng = stream(*seed_parts)
    n = len(values)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(resamples))
    return percentile(means, 0.025), percentile(means, 0.975)  # type: ignore[return-value]


def _summ(values: list[float], seed_parts: tuple, resamples: int, n_reps: int) -> dict | None:
    if not values:
        return None
    mean = sum(values) / len(values)
    if len(values) >= 2:
        lo, hi = bootstrap_ci(values, seed_parts, resamples)
    else:
        lo = hi = None  # one replication has no spread: say so (null), do not report a zero-width interval
    return {"mean": mean, "ci_low": lo, "ci_high": hi, "n_reps": len(values), "of_reps": n_reps}


def _round(obj):
    if isinstance(obj, float):
        return round(obj, 6)
    if isinstance(obj, dict):
        return {k: _round(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round(v) for v in obj]
    return obj


def aggregate(reps: list[dict], base_seed: int, resamples: int) -> dict:
    keys: list[str] = []
    for r in reps:
        for arm in ARMS:
            for k in r["arms"][arm]["metrics"]:
                if k not in keys:
                    keys.append(k)
    keys.sort()
    n = len(reps)
    arms: dict[str, dict] = {a: {} for a in ARMS}
    diff: dict[str, dict] = {}
    for k in keys:
        per_arm = {}
        for a in ARMS:
            vals = [r["arms"][a]["metrics"].get(k) for r in reps]
            per_arm[a] = vals
            s = _summ([v for v in vals if v is not None], (base_seed, "bootstrap", a, k), resamples, n)
            arms[a][k] = s
        paired = [o - b for b, o in zip(per_arm[BASELINE], per_arm[ORCHESTRATED]) if b is not None and o is not None]
        diff[k] = _summ(paired, (base_seed, "bootstrap", "diff", k), resamples, n)
    return {"arms": arms, "difference": diff}


def _scenario_paths(params_paths, overlay_paths) -> list[tuple[str, list | None]]:
    """[(scenario name, params file stack)]. The overlay is layered on top of the upper-range stack."""
    from . import DEFAULT_PARAMS

    base = list(params_paths) if params_paths else [DEFAULT_PARAMS]
    out: list[tuple[str, list | None]] = [(UPPER, params_paths)]
    if overlay_paths:
        out.append((LITERATURE, base + list(overlay_paths)))
    return out


def _check_same_baseline(reps: list[dict]) -> None:
    """The literature-anchored overlay may only touch the orchestrated arm. Refuse to report otherwise."""
    for r in reps:
        sc = r["scenarios"]
        if UPPER in sc and LITERATURE in sc:
            a, b = sc[UPPER]["arms"][BASELINE], sc[LITERATURE]["arms"][BASELINE]
            if a["event_hash"] != b["event_hash"] or a["n_events"] != b["n_events"]:
                raise ValueError(
                    f"the conservative overlay changes the baseline arm in replication {r['rep']} "
                    f"(event-log hash {a['event_hash']} vs {b['event_hash']}); it may only override orchestrated-arm keys"
                )


def _meta_for(ctx, params_files) -> dict:
    status_counts: dict[str, int] = {}
    for p in ctx.params.rows():
        status_counts[p.status] = status_counts.get(p.status, 0) + 1
    return {
        "params_hash": ctx.params.hash(),
        "params_files": [_rel(f) for f in ctx.params.files],
        "parameter_status_counts": dict(sorted(status_counts.items())),
    }


def _overrides(upper_ctx, lit_ctx) -> list[dict]:
    """Every parameter the overlay changes (or adds), with the upper-range value next to the new one."""
    up = {p.key: p for p in upper_ctx.params.rows()}
    out = []
    for p in lit_ctx.params.rows():
        u = up.get(p.key)
        if u is None or (u.value, u.source, u.status) != (p.value, p.source, p.status):
            out.append({
                "key": p.key, "value": p.value, "upper_range_value": None if u is None else u.value,
                "source": p.source, "status": p.status, "notes": p.notes,
            })
    return out


def evaluate(
    reps: int = 20,
    cases: int = 2000,
    days: int = 360,
    seed: int = 20261017,
    params_paths: list | None = None,
    pathway_path: str | None = None,
    resamples: int = 1000,
    jobs: int = 1,
    overlay_paths: list | tuple | None = (DEFAULT_CONSERVATIVE,),
) -> dict:
    """`overlay_paths`: parameter files layered over the upper-range stack to define the literature-anchored
    scenario; pass None or [] to evaluate the upper-range scenario only."""
    ctx = load_context(params_paths, pathway_path, warn=False)
    scen = _scenario_paths(params_paths, overlay_paths)
    jobs_list = [(scen, pathway_path, i, rep_seed(seed, i), cases, days) for i in range(reps)]
    if jobs > 1 and reps > 1:
        with ProcessPoolExecutor(max_workers=min(jobs, reps)) as ex:
            rep_results = list(ex.map(_run_rep, jobs_list))
    else:
        rep_results = [_run_rep(j) for j in jobs_list]
    _check_same_baseline(rep_results)

    def view(name: str) -> list[dict]:
        return [{"rep": r["rep"], "seed": r["seed"], "arms": r["scenarios"][name]["arms"]} for r in rep_results]

    def per_rep(name: str) -> list[dict]:
        return [
            {"rep": r["rep"], "seed": r["seed"],
             "arms": {a: {"event_hash": r["scenarios"][name]["arms"][a]["event_hash"],
                          "n_events": r["scenarios"][name]["arms"][a]["n_events"],
                          "counters": r["scenarios"][name]["arms"][a]["counters"]} for a in ARMS}}
            for r in rep_results
        ]

    agg = aggregate(view(UPPER), seed, resamples)
    effort = {
        "model": model_description(),
        "scenarios": {
            name: aggregate_effort(
                [{a: r["scenarios"][name]["arms"][a]["effort"] for a in ARMS} for r in rep_results], seed, resamples, _summ, name,
            )
            for name, _ in scen
        },
    }
    meta_up = _meta_for(ctx, params_paths)
    result = {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "seed": seed,
            "replications": reps,
            "cases_per_replication": cases,
            "follow_up_days": days,
            "bootstrap_resamples": resamples,
            "params_hash": meta_up["params_hash"],
            "params_files": meta_up["params_files"],
            "pathway": ctx.pathway.name,
            "pathway_version": ctx.pathway.version,
            "pathway_hash": ctx.pathway_hash(),
            "git_commit": git_commit(),
            "parameter_status_counts": meta_up["parameter_status_counts"],
            "ci_method": "percentile bootstrap over replications; differences are paired by replication (common random numbers)",
            "scenarios": [n for n, _ in scen],
        },
        "arms": agg["arms"],
        "difference": agg["difference"],
        "per_replication": per_rep(UPPER),
        "effort": effort,
        "parameters": [
            {"key": p.key, "value": p.value, "source": p.source, "status": p.status, "notes": p.notes}
            for p in ctx.params.rows()
        ],
        "scenarios": {
            UPPER: {
                "label": SCENARIO_LABELS[UPPER],
                "params_files": meta_up["params_files"],
                "params_hash": meta_up["params_hash"],
                "parameter_status_counts": meta_up["parameter_status_counts"],
                "overrides": [],
                "arms": agg["arms"],
                "difference": agg["difference"],
                "per_replication": per_rep(UPPER),
            }
        },
    }
    if params_paths:
        # T-B13: say which parameters the user's layered files changed against the shipped defaults. Absent on a default run, so
        # the committed results files keep their bytes.
        user_overrides = [
            {"key": o["key"], "value": o["value"], "default_value": o["upper_range_value"], "source": o["source"], "status": o["status"]}
            for o in _overrides(load_context(None, pathway_path, warn=False), ctx)
        ]
        if user_overrides:
            result["meta"]["parameter_overrides"] = user_overrides
    if len(scen) > 1:
        lit_ctx = load_context(scen[1][1], pathway_path, warn=False)
        meta_lit = _meta_for(lit_ctx, scen[1][1])
        agg_lit = aggregate(view(LITERATURE), seed, resamples)
        result["scenarios"][LITERATURE] = {
            "label": SCENARIO_LABELS[LITERATURE],
            "params_files": meta_lit["params_files"],
            "params_hash": meta_lit["params_hash"],
            "parameter_status_counts": meta_lit["parameter_status_counts"],
            "overrides": _overrides(ctx, lit_ctx),
            "arms": agg_lit["arms"],
            "difference": agg_lit["difference"],
            "per_replication": per_rep(LITERATURE),
        }
    return _round(result)


def write_results(result: dict, out_dir: Path, ctx: Context | None = None) -> tuple[Path, Path]:
    from .report import render_markdown

    out_dir.mkdir(parents=True, exist_ok=True)
    jpath = out_dir / "results.json"
    jpath.write_text(json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False) + "\n")
    mpath = out_dir / "results.md"
    from .capacity_report import load_capacity, section_block

    from . import abudhabi_report, refusal_report

    refusal_block = refusal_report.section_block(refusal_report.load_refusal(out_dir), result)
    abudhabi_block = abudhabi_report.section_block(abudhabi_report.load_abudhabi(out_dir))
    mpath.write_text(render_markdown(result, section_block(load_capacity(out_dir)), refusal_block, abudhabi_block))
    return jpath, mpath
