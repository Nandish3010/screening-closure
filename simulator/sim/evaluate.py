"""Replicated evaluation: N replications per arm, bootstrap 95% CIs, results.json + results.md.

Reproducibility: every random draw derives from the base seed, replications run independently (so
`--jobs` never changes a number), floats are rounded to 6 places and results.json has sorted keys,
no timestamps, no paths outside the repo and no timings. `results.md` carries a `generated_at`.
"""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from . import REPO_ROOT
from .engine import ARMS, BASELINE, ORCHESTRATED
from .metrics import percentile
from .rng import derive_seed, stream
from .runner import Context, git_commit, load_context, make_patients, metrics_of, run_arm

SCHEMA_VERSION = 1


def rep_seed(base_seed: int, rep: int) -> int:
    return derive_seed(base_seed, "replication", rep) & 0x7FFFFFFF


def _rel(path: str) -> str:
    p = Path(path).resolve()
    try:
        return str(p.relative_to(REPO_ROOT))
    except ValueError:
        return p.name


def _run_rep(job: tuple) -> dict:
    """One replication: one cohort, both arms. Top-level so it can run in a worker process."""
    params_paths, pathway_path, rep, seed, cases, days = job
    ctx = load_context(params_paths, pathway_path, warn=False)
    patients = make_patients(ctx, cases, seed)
    out = {"rep": rep, "seed": seed, "arms": {}}
    for arm in ARMS:
        res = run_arm(ctx, arm, cases, days, seed, patients)
        out["arms"][arm] = {
            "metrics": metrics_of(ctx, res),
            "event_hash": res.event_hash[:16],
            "n_events": res.n_events,
            "counters": dict(res.counters),
        }
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
        lo = hi = mean
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


def evaluate(
    reps: int = 20,
    cases: int = 2000,
    days: int = 360,
    seed: int = 20261017,
    params_paths: list | None = None,
    pathway_path: str | None = None,
    resamples: int = 1000,
    jobs: int = 1,
) -> dict:
    ctx = load_context(params_paths, pathway_path, warn=False)
    jobs_list = [(params_paths, pathway_path, i, rep_seed(seed, i), cases, days) for i in range(reps)]
    if jobs > 1 and reps > 1:
        with ProcessPoolExecutor(max_workers=min(jobs, reps)) as ex:
            rep_results = list(ex.map(_run_rep, jobs_list))
    else:
        rep_results = [_run_rep(j) for j in jobs_list]
    agg = aggregate(rep_results, seed, resamples)
    status_counts: dict[str, int] = {}
    for p in ctx.params.rows():
        status_counts[p.status] = status_counts.get(p.status, 0) + 1
    result = {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "seed": seed,
            "replications": reps,
            "cases_per_replication": cases,
            "follow_up_days": days,
            "bootstrap_resamples": resamples,
            "params_hash": ctx.params.hash(),
            "params_files": [_rel(f) for f in ctx.params.files],
            "pathway": ctx.pathway.name,
            "pathway_version": ctx.pathway.version,
            "pathway_hash": ctx.pathway_hash(),
            "git_commit": git_commit(),
            "parameter_status_counts": dict(sorted(status_counts.items())),
            "ci_method": "percentile bootstrap over replications; differences are paired by replication (common random numbers)",
        },
        "arms": agg["arms"],
        "difference": agg["difference"],
        "per_replication": [
            {"rep": r["rep"], "seed": r["seed"],
             "arms": {a: {"event_hash": r["arms"][a]["event_hash"], "n_events": r["arms"][a]["n_events"],
                          "counters": r["arms"][a]["counters"]} for a in ARMS}}
            for r in rep_results
        ],
        "parameters": [
            {"key": p.key, "value": p.value, "source": p.source, "status": p.status, "notes": p.notes}
            for p in ctx.params.rows()
        ],
    }
    return _round(result)


def write_results(result: dict, out_dir: Path, ctx: Context | None = None) -> tuple[Path, Path]:
    from .report import render_markdown

    out_dir.mkdir(parents=True, exist_ok=True)
    jpath = out_dir / "results.json"
    jpath.write_text(json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False) + "\n")
    mpath = out_dir / "results.md"
    mpath.write_text(render_markdown(result))
    return jpath, mpath
