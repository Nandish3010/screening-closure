"""Calibration of the B21 timing mixture (fast and slow booking lanes) on CALIBRATION data only.

    python -m simulator.sim.timing_fit fit  [--reps 10 --cases 2000 --jobs 8]   # fit, print, write docs/results/timing_fit.json
    python -m simulator.sim.timing_fit show                                    # the fit report of the current params.yaml

Two separate fits, in this order (the second depends on the first only through the 0.6% of managed cases in the slow lane):

1. Fast lane (`appointment.fast_lane_lead_median_days`, `fast_lane_lead_sigma`): the programme-managed configuration
   (baseline arm, `baseline.programme_managed` true, no forgotten staff steps, no rejected or unanswered referral) must
   reproduce the Public Health Scotland KPI 4 bands: of the people who had a colonoscopy, 28.8% within 28 days and 70.4%
   within 56 days of the referral. The Scottish clock starts at the screening-test referral, when the case is handed to the
   board that then contacts and books the patient; the simulator's analogue is the `accept_ownership` event.
2. Slow lane and mix (`appointment.fast_lane_share.unmanaged`, `slow_lane_lead_median_days`, `slow_lane_lead_sigma`): the
   unmanaged baseline must reproduce the Mohl 2023 completion curve, 43.3%, 51.4% and 56.1% at 90, 180 and 360 days from the
   positive result.

`appointment.fast_lane_share.managed` is not fitted (sourced: NHS England BCSP-S06, 99.4% offered an appointment within 14 days).
Nothing here reads tools/calibration/validation_targets.json: the held-out targets are never used to set a parameter.
Least squares by a Nelder-Mead search on transformed parameters (log for days and spreads, logit for the share); every
evaluation uses the same replication seeds as `make evaluate` (common random numbers), so the objective is deterministic.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from . import DEFAULT_PARAMS, REPO_ROOT
from .evaluate import rep_seed
from .params import load_params
from .runner import context_from_params, make_patients, run_arm

OUT = REPO_ROOT / "docs" / "results" / "timing_fit.json"
SEED = 20261017
PHS_BANDS = {28: 0.288072, 56: 0.704075}  # Public Health Scotland KPI 4, Scotland, all persons (docs/results/calibration.md)
MOHL = {90: 0.433, 180: 0.514, 360: 0.561}  # Mohl et al. 2023 (colonoscopy_completion_3m/6m/12m in params.yaml)
MANAGED = {"baseline.programme_managed": True, "staff.forget_prob": 0.0, "referral_rejection_rate": 0.0, "handoff.no_response_prob": 0.0}
FAST_KEYS = ("appointment.fast_lane_lead_median_days", "appointment.fast_lane_lead_sigma")
SLOW_KEYS = ("appointment.fast_lane_share.unmanaged", "appointment.slow_lane_lead_median_days", "appointment.slow_lane_lead_sigma")


def _one(job: tuple) -> dict:
    overrides, rep, cases, days = job
    params = load_params(str(DEFAULT_PARAMS), warn=False)
    for k, v in overrides.items():
        params = params.with_value(k, v)
    ctx = context_from_params(params)
    seed = rep_seed(SEED, rep)
    res = run_arm(ctx, "baseline", cases, days, seed, make_patients(ctx, cases, seed))
    n = len(res.cases)
    from_result, from_referral = [], []
    for c in res.cases:
        t_acc = t_done = None
        for e in c.log:
            if e.type == "accept_ownership" and t_acc is None:
                t_acc = e.at
            elif e.type == "complete_confirmatory" and t_done is None:
                t_done = e.at
        if t_done is not None:
            from_result.append((t_done - c.log[0].at).total_seconds() / 86400.0)
            from_referral.append((t_done - t_acc).total_seconds() / 86400.0)
    return {"n": n, "from_result": from_result, "from_referral": from_referral}


class _Sim:
    def __init__(self, reps: int, cases: int, jobs: int):
        self.reps, self.cases, self.jobs = reps, cases, jobs
        self.pool = ProcessPoolExecutor(max_workers=jobs) if jobs > 1 else None

    def run(self, overrides: dict) -> list[dict]:
        jobs = [(overrides, r, self.cases, 360) for r in range(self.reps)]
        return list(self.pool.map(_one, jobs)) if self.pool else [_one(j) for j in jobs]


def phs_shares(out: list[dict]) -> dict[int, float]:
    t = [x for o in out for x in o["from_referral"]]
    return {d: sum(1 for x in t if x <= d) / len(t) for d in PHS_BANDS}


def mohl_curve(out: list[dict]) -> dict[int, float]:
    n = sum(o["n"] for o in out)
    return {d: sum(1 for o in out for x in o["from_result"] if x <= d) / n for d in MOHL}


def nelder_mead(f, x0: list[float], step: float = 0.3, iters: int = 80, tol: float = 1e-7):
    n = len(x0)
    pts = [list(x0)] + [[x0[j] + (step if j == i else 0.0) for j in range(n)] for i in range(n)]
    vals = [f(p) for p in pts]
    for _ in range(iters):
        order = sorted(range(n + 1), key=lambda i: vals[i])
        pts, vals = [pts[i] for i in order], [vals[i] for i in order]
        if vals[-1] - vals[0] < tol:
            break
        cen = [sum(p[j] for p in pts[:-1]) / n for j in range(n)]
        refl = [cen[j] + (cen[j] - pts[-1][j]) for j in range(n)]
        fr = f(refl)
        if fr < vals[0]:
            exp = [cen[j] + 2 * (cen[j] - pts[-1][j]) for j in range(n)]
            fe = f(exp)
            pts[-1], vals[-1] = (exp, fe) if fe < fr else (refl, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = refl, fr
        else:
            con = [cen[j] + 0.5 * (pts[-1][j] - cen[j]) for j in range(n)]
            fc = f(con)
            if fc < vals[-1]:
                pts[-1], vals[-1] = con, fc
            else:
                for i in range(1, n + 1):
                    pts[i] = [pts[0][j] + 0.5 * (pts[i][j] - pts[0][j]) for j in range(n)]
                    vals[i] = f(pts[i])
    i = min(range(n + 1), key=lambda k: vals[k])
    return pts[i], vals[i]


def _logit(p: float) -> float:
    return math.log(p / (1 - p))


def _expit(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def current() -> dict:
    p = load_params(str(DEFAULT_PARAMS), warn=False)
    return {k: p.float(k) for k in FAST_KEYS + SLOW_KEYS}


def fit(reps: int, cases: int, jobs: int, fast_fixed: tuple | None = None, iters: int = 80, slow_start: tuple | None = None) -> dict:
    sim = _Sim(reps, cases, jobs)
    start = current()

    def fast_obj(x):
        o = {FAST_KEYS[0]: round(math.exp(x[0]), 3), FAST_KEYS[1]: round(math.exp(x[1]), 4)}
        s = phs_shares(sim.run({**MANAGED, **o}))
        v = sum((s[d] - PHS_BANDS[d]) ** 2 for d in PHS_BANDS)
        print("fast", o, {d: round(x, 4) for d, x in s.items()}, f"{v:.2e}", file=sys.stderr, flush=True)
        return v

    if fast_fixed is None:
        xf, _ = nelder_mead(fast_obj, [math.log(start[FAST_KEYS[0]]), math.log(start[FAST_KEYS[1]])], iters=iters)
        fast = {FAST_KEYS[0]: round(math.exp(xf[0]), 1), FAST_KEYS[1]: round(math.exp(xf[1]), 2)}
    else:  # resume: the fast lane was fitted by an earlier run (its log is the record)
        fast = {FAST_KEYS[0]: fast_fixed[0], FAST_KEYS[1]: fast_fixed[1]}

    def slow_obj(x):
        o = {**fast, SLOW_KEYS[0]: round(_expit(x[0]), 4), SLOW_KEYS[1]: round(math.exp(x[1]), 2), SLOW_KEYS[2]: round(math.exp(x[2]), 3)}
        if o[SLOW_KEYS[1]] < fast[FAST_KEYS[0]] or o[SLOW_KEYS[2]] < fast[FAST_KEYS[1]]:
            return 1.0  # structure: the slow lane is slower and more spread than the fast lane (otherwise the lanes swap)
        c = mohl_curve(sim.run(o))
        v = sum((c[d] - MOHL[d]) ** 2 for d in MOHL)
        print("slow", o, {d: round(x, 4) for d, x in c.items()}, f"{v:.2e}", file=sys.stderr, flush=True)
        return v

    s0 = slow_start or (start[SLOW_KEYS[0]], start[SLOW_KEYS[1]], start[SLOW_KEYS[2]])
    xs, _ = nelder_mead(slow_obj, [_logit(s0[0]), math.log(s0[1]), math.log(s0[2])], step=0.15, iters=iters)
    slow = {SLOW_KEYS[0]: round(_expit(xs[0]), 2), SLOW_KEYS[1]: round(math.exp(xs[1]), 0), SLOW_KEYS[2]: round(math.exp(xs[2]), 2)}
    return {**fast, **slow}


def report(values: dict, reps: int, cases: int, jobs: int) -> dict:
    """What the given (rounded) values reproduce, at the fitting size."""
    sim = _Sim(reps, cases, jobs)
    s = phs_shares(sim.run({**MANAGED, **values}))
    c = mohl_curve(sim.run(values))
    return {
        "note": "B21 timing-mixture calibration (simulator/sim/timing_fit.py). Calibration data only: Public Health Scotland KPI 4 "
                "bands (fast lane) and the Mohl 2023 curve (slow lane and unmanaged mix). No validation target is read.",
        "seed": SEED, "reps": reps, "cases": cases,
        "values": values,
        "fast_lane_target": {"configuration": MANAGED, "clock": "accept_ownership to complete_confirmatory, completers",
                             "observed": {str(d): v for d, v in PHS_BANDS.items()}, "simulated": {str(d): round(v, 4) for d, v in s.items()}},
        "slow_lane_target": {"configuration": "baseline, unmanaged", "clock": "positive result to complete_confirmatory, all cases",
                             "observed": {str(d): v for d, v in MOHL.items()}, "simulated": {str(d): round(v, 4) for d, v in c.items()}},
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m simulator.sim.timing_fit")
    ap.add_argument("cmd", choices=("fit", "show"))
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--cases", type=int, default=2000)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--iters", type=int, default=80)
    ap.add_argument("--fast", help="MEDIAN,SIGMA: skip the fast-lane fit and use these (resume)")
    ap.add_argument("--slow-start", help="SHARE,MEDIAN,SIGMA: start of the slow-lane search (resume)")
    a = ap.parse_args(argv)
    ff = tuple(float(x) for x in a.fast.split(",")) if a.fast else None
    ss = tuple(float(x) for x in a.slow_start.split(",")) if a.slow_start else None
    values = fit(a.reps, a.cases, a.jobs, ff, a.iters, ss) if a.cmd == "fit" else current()
    rep = report(values, a.reps, a.cases, a.jobs)
    print(json.dumps(rep, indent=2))
    if a.cmd == "fit":
        OUT.write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
