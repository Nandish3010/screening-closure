"""Validation of the simulator against data it was not calibrated on (ticket B20).

    python -m simulator.sim.validation run [--reps 20 --cases 2000 --days 360 --jobs 8 --out-dir docs/results]
    python -m simulator.sim.validation check          # regenerate into a temp dir and diff against the committed files

Targets come from `tools/calibration/validation_targets.json` (built from the local external files by
`python3 -I tools/calibration/validation_targets.py build`; this module never reads an external file). For every target it runs the
named arm for R replications (same replication seeds as `make evaluate`), under the documented setting variants, and compares:

* completion at the observed days (difference in percentage points; tolerance +-5 points),
* the share of completers within a day count (Scotland's 4 and 8 week bands; same tolerance),
* median, 25th and 75th percentile of time to colonoscopy among completers (ratio simulated / observed; tolerance +-25%),
* a coverage check: does the observed value fall inside the simulator's 95% replication interval (2.5th to 97.5th percentile of the
  per-replication values)? A second, wider reading is also given: does the simulated mean fall inside the observed Wilson interval?

and, for trials, the orchestrated / baseline completion ratio against the trial's risk ratio and its 95% interval (Katz log method).
All arithmetic is in the small pure functions at the top so tests can check it by hand.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from . import DEFAULT_CONSERVATIVE, DEFAULT_PARAMS, REPO_ROOT
from .evaluate import rep_seed
from .params import load_params
from .runner import context_from_params, make_patients, run_arm

TARGETS_PATH = REPO_ROOT / "tools" / "calibration" / "validation_targets.json"
DEFAULT_OUT = REPO_ROOT / "docs" / "results"
TOL_POINTS = 0.05      # completion: +-5 percentage points
TOL_RATIO = 0.25       # median and quartiles: simulated / observed within 0.75 to 1.25
SCHEMA = 1
SCENARIO_STACKS = {"upper_range": [str(DEFAULT_PARAMS)], "literature_anchored": [str(DEFAULT_PARAMS), str(DEFAULT_CONSERVATIVE)]}


# ------------------------------------------------------------------ arithmetic (hand-checked in simulator/tests/test_validation.py)
def percentile(values: list[float], q: float) -> float | None:
    """Linear interpolation between order statistics (q in [0, 1]); None for an empty list."""
    if not values:
        return None
    v = sorted(values)
    pos = q * (len(v) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def wilson(k: float, n: float, z: float = 1.959964) -> tuple[float, float]:
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def risk_ratio(k1: float, n1: float, k0: float, n0: float) -> tuple[float, float, float]:
    """Risk ratio (treated over control) with the Katz log-method 95% interval."""
    rr = (k1 / n1) / (k0 / n0)
    se = math.sqrt(1 / k1 - 1 / n1 + 1 / k0 - 1 / n0)
    return rr, rr * math.exp(-1.959964 * se), rr * math.exp(1.959964 * se)


def diff_points(sim: float, obs: float) -> float:
    return sim - obs


def within_points(sim: float, obs: float, tol: float = TOL_POINTS) -> bool:
    return abs(sim - obs) <= tol + 1e-12


def ratio_of(sim: float, obs: float) -> float:
    return sim / obs


def within_ratio(sim: float, obs: float, tol: float = TOL_RATIO) -> bool:
    r = sim / obs
    return (1 - tol) - 1e-12 <= r <= (1 + tol) + 1e-12


def interval(values: list[float]) -> tuple[float, float]:
    """The simulator's 95% replication interval: 2.5th and 97.5th percentile of the per-replication values."""
    return percentile(values, 0.025), percentile(values, 0.975)


def covered(obs: float, lo: float, hi: float) -> bool:
    return lo - 1e-12 <= obs <= hi + 1e-12


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _r(x):
    if isinstance(x, float):
        return round(x, 6)
    if isinstance(x, dict):
        return {k: _r(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_r(v) for v in x]
    return x


# ------------------------------------------------------------------ simulation
def _stack_key(cfg: dict) -> str:
    return json.dumps(cfg, sort_keys=True)


def _run_one(job: tuple) -> dict:
    """One replication of one configuration: per-case completion times -> the quantities every comparison needs."""
    cfg, rep, seed, cases, days, days_wanted = job
    params = load_params(*SCENARIO_STACKS[cfg["scenario"]], warn=False)
    for k, v in cfg["overrides"].items():
        params = params.with_value(k, v)
    ctx = context_from_params(params)
    res = run_arm(ctx, cfg["arm"], cases, days, seed, make_patients(ctx, cases, seed))
    t = sorted(r.t_complete for r in res.records if r.t_complete is not None)
    n = len(res.records)
    comp = {str(d): sum(1 for x in t if x <= d) / n for d in days_wanted}
    share = {str(d): (sum(1 for x in t if x <= d) / len(t) if t else None) for d in days_wanted}
    return {"rep": rep, "completion": comp, "completer_share": share, "n_completers": len(t),
            "median": percentile(t, 0.5), "p25": percentile(t, 0.25), "p75": percentile(t, 0.75)}


def _compare_rows(sim_reps: list[dict], obs: dict, in_sample: set[str], n_obs: int) -> list[dict]:
    rows = []

    def add(kind: str, day, label: str, sim_vals: list[float], o: float, k: float | None, n: float | None, is_ratio: bool):
        m = mean(sim_vals)
        lo, hi = interval(sim_vals)
        row = {"metric": label, "kind": kind, "day": day, "observed": o, "sim_mean": m, "sim_lo": lo, "sim_hi": hi,
               "in_sample": label in in_sample}
        if is_ratio:
            row.update(ratio=ratio_of(m, o), within_tolerance=within_ratio(m, o))
        else:
            row.update(diff_points=diff_points(m, o) * 100.0, within_tolerance=within_points(m, o))
        row["covered_by_replication_interval"] = covered(o, lo, hi)
        if k is not None and n:
            wlo, whi = wilson(k, n)
            row.update(obs_lo=wlo, obs_hi=whi, sim_inside_obs_interval=covered(m, wlo, whi))
        rows.append(row)

    for c in obs.get("completion", []):
        d = c["day"]
        o = c["p"] if "p" in c else c["k"] / c["n"]
        add("completion", d, f"completion@{d}", [r["completion"][str(d)] for r in sim_reps], o, c.get("k", o * c["n"]), c["n"], False)
    for c in obs.get("completer_share", []):
        d = c["day"]
        add("completer_share", d, f"completers_within@{d}", [r["completer_share"][str(d)] for r in sim_reps], c["p"], None, None, False)
    for key, label in (("median", "median"), ("p25", "p25"), ("p75", "p75")):
        if key in obs:
            add("time", None, label, [r[key] for r in sim_reps], obs[key], None, None, True)
    return rows


def run_validation(targets: dict, reps: int = 20, cases: int = 2000, days: int = 360, seed: int = 20261017, jobs: int = 1) -> dict:
    """Run every distinct configuration once per replication, then compare. Deterministic for any `jobs`."""
    needed_days = sorted({c["day"] for t in targets["cohort_targets"] for c in t["observed"].get("completion", [])}
                         | {c["day"] for t in targets["cohort_targets"] for c in t["observed"].get("completer_share", [])}
                         | {t["day"] for t in targets["effect_trials"]})
    cfgs: dict[str, dict] = {}

    def want(cfg: dict) -> str:
        cfgs.setdefault(_stack_key(cfg), cfg)
        return _stack_key(cfg)

    for t in targets["cohort_targets"]:
        for v in t["variants"]:
            v["_key"] = want({"arm": t["arm"], "scenario": v.get("scenario", "upper_range"), "overrides": v["overrides"]})
    for tr in targets["effect_trials"]:
        tr["_k_base"] = want({"arm": "baseline", "scenario": tr["scenario"], "overrides": {}})
        tr["_k_orch"] = want({"arm": "orchestrated", "scenario": tr["scenario"], "overrides": {}})
    keys = sorted(cfgs)
    job_list = [(cfgs[k], i, rep_seed(seed, i), cases, days, needed_days) for k in keys for i in range(reps)]
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            out = list(ex.map(_run_one, job_list, chunksize=1))
    else:
        out = [_run_one(j) for j in job_list]
    sims = {k: out[i * reps:(i + 1) * reps] for i, k in enumerate(keys)}

    res_targets = []
    for t in targets["cohort_targets"]:
        in_sample = set(t.get("in_sample_rows", []))
        variants = []
        for v in t["variants"]:
            rows = _compare_rows(sims[v["_key"]], t["observed"], in_sample, t["n"])
            held = [r for r in rows if not r["in_sample"]]
            variants.append({"name": v["name"], "overrides": v["overrides"], "scenario": v.get("scenario", "upper_range"), "rows": rows,
                             "n_held_out_rows": len(held), "n_held_out_within": sum(1 for r in held if r["within_tolerance"]),
                             "verdict": "within tolerance" if held and all(r["within_tolerance"] for r in held) else "not within tolerance"})
        prim = next(x for x in variants if x["name"] == t["primary_variant"])
        res_targets.append({k: t[k] for k in ("id", "title", "arm", "n", "independence", "independence_note", "setting_note", "source", "primary_variant")}
                           | {"in_sample_rows": sorted(in_sample), "context": t.get("context", {}), "observed": t["observed"],
                              "variants": variants, "verdict": prim["verdict"]})
    res_trials = []
    for tr in targets["effect_trials"]:
        d = str(tr["day"])
        b, o = sims[tr["_k_base"]], sims[tr["_k_orch"]]
        ratios = [oo["completion"][d] / bb["completion"][d] for bb, oo in zip(b, o)]
        rr, lo, hi = risk_ratio(tr["treated"]["k"], tr["treated"]["n"], tr["control"]["k"], tr["control"]["n"])
        m = mean(ratios)
        slo, shi = interval(ratios)
        res_trials.append({k: tr[k] for k in ("id", "title", "intervention", "day", "treated", "control", "scenario", "independence", "independence_note", "source")}
                           | {"trial_rr": rr, "trial_lo": lo, "trial_hi": hi, "sim_ratio": m, "sim_lo": slo, "sim_hi": shi,
                              "sim_baseline": mean([x["completion"][d] for x in b]), "sim_orchestrated": mean([x["completion"][d] for x in o]),
                              "trial_control": tr["control"]["k"] / tr["control"]["n"], "trial_treated": tr["treated"]["k"] / tr["treated"]["n"],
                              "inside_trial_ci": covered(m, lo, hi), "trial_rr_inside_sim_interval": covered(rr, slo, shi),
                              "verdict": "inside the trial's 95% CI" if covered(m, lo, hi) else "outside the trial's 95% CI"})
    return _r({"schema": SCHEMA, "design": {"reps": reps, "cases": cases, "days": days, "seed": seed, "tolerance_points": TOL_POINTS * 100, "tolerance_ratio": TOL_RATIO,
                                            "params_files": {k: [Path(p).name for p in v] for k, v in SCENARIO_STACKS.items()},
                                            "params_hash": {k: load_params(*v, warn=False).hash()[:16] for k, v in SCENARIO_STACKS.items()},
                                            "targets_sha256": _sha(TARGETS_PATH)},
               "cohort_targets": res_targets, "effect_trials": res_trials,
               "attempted_not_obtained": targets["attempted_not_obtained"]})


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_targets(path: Path = TARGETS_PATH) -> dict:
    return json.loads(path.read_text())


def write(result: dict, out_dir: Path) -> tuple[Path, Path]:
    from .validation_report import render
    out_dir.mkdir(parents=True, exist_ok=True)
    j, m = out_dir / "validation.json", out_dir / "validation.md"
    j.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    m.write_text(render(result))
    return j, m


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m simulator.sim.validation", description=__doc__)
    ap.add_argument("cmd", choices=["run", "check"])
    ap.add_argument("--reps", type=int, default=20)
    ap.add_argument("--cases", type=int, default=2000)
    ap.add_argument("--days", type=int, default=360)
    ap.add_argument("--seed", type=int, default=20261017)
    ap.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    a = ap.parse_args(argv)
    if a.cmd == "run":
        r = run_validation(load_targets(), a.reps, a.cases, a.days, a.seed, a.jobs)
        j, m = write(r, Path(a.out_dir))
        print(f"wrote {j} and {m}")
        return 0
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        write(run_validation(load_targets(), a.reps, a.cases, a.days, a.seed, a.jobs), Path(d1))
        write(run_validation(load_targets(), a.reps, a.cases, a.days, a.seed, 1), Path(d2))
        bad = []
        for name in ("validation.json", "validation.md"):
            x, y, c = (Path(d1) / name).read_bytes(), (Path(d2) / name).read_bytes(), (Path(a.out_dir) / name)
            if x != y:
                bad.append(f"{name}: multi-process and single-process runs differ")
            if not c.exists() or c.read_bytes() != x:
                bad.append(f"{name}: committed file differs from a fresh run (run `make validate`)")
        if bad:
            print("validate-check: FAIL\n  " + "\n  ".join(bad))
            return 1
    print("validate-check: PASS, validation.json and validation.md are reproduced byte for byte (multi-process and single-process)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
