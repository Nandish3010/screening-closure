"""Command line: `python -m simulator.sim.cli run ...` and `... evaluate ...` (run from the repo root)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import REPO_ROOT
from .engine import ARMS
from .evaluate import evaluate, write_results
from .invariants import check_case_log, check_consent
from .runner import load_context, make_patients, metrics_of, run_arm

DEFAULT_SEED = 20261017


def _cmd_run(args) -> int:
    ctx = load_context(args.params, args.pathway)
    arms = ARMS if args.arm == "both" else (args.arm,)
    patients = make_patients(ctx, args.cases, args.seed)
    out = {}
    for arm in arms:
        t0 = time.perf_counter()
        res = run_arm(ctx, arm, args.cases, args.days, args.seed, patients)
        elapsed = time.perf_counter() - t0
        info = {
            "arm": arm, "cases": args.cases, "days": args.days, "seed": args.seed,
            "events": res.n_events, "event_log_sha256": res.event_hash, "seconds": round(elapsed, 2),
            "counters": dict(res.counters),
        }
        if args.verify:
            bad = []
            for c in res.cases:
                bad += [f"case {c.p.key}: {m}" for m in check_case_log(c.log, ctx.pathway)]
                if arm == "orchestrated":
                    bad += [f"case {c.p.key}: {m}" for m in check_consent(c.log)]
            info["invariant_violations"] = len(bad)
            if bad:
                print("\n".join(bad[:20]), file=sys.stderr)
        m = metrics_of(ctx, res)
        info["metrics"] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()
                           if not k.startswith(("fairness.", "messy."))}
        out[arm] = info
        if args.events:
            path = Path(args.events.replace("{arm}", arm))
            with path.open("w") as f:
                for c in res.cases:
                    for ev in c.log:
                        f.write(json.dumps({"case": c.p.key, "seq": ev.seq, "type": ev.type, "at": ev.at.isoformat(),
                                            "actor": ev.actor, "payload": ev.payload}, sort_keys=True) + "\n")
    print(json.dumps(out, indent=2, sort_keys=True))
    return 1 if any(v.get("invariant_violations") for v in out.values()) else 0


def _cmd_evaluate(args) -> int:
    t0 = time.perf_counter()
    ctx = load_context(args.params, args.pathway)
    result = evaluate(args.reps, args.cases, args.days, args.seed, args.params, args.pathway, args.bootstrap, args.jobs)
    jpath, mpath = write_results(result, Path(args.out_dir), ctx)
    print(f"wrote {jpath} and {mpath} in {time.perf_counter() - t0:.1f}s")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m simulator.sim.cli", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--seed", type=int, default=DEFAULT_SEED)
    common.add_argument("--cases", type=int, default=2000)
    common.add_argument("--params", action="append", default=None, help="parameter YAML (repeat to layer; later files override)")
    common.add_argument("--pathway", default=None, help="pathway YAML (default config/pathways/colorectal_fit.yaml)")
    r = sub.add_parser("run", parents=[common], help="run one or both arms once and print metrics")
    r.add_argument("--arm", choices=(*ARMS, "both"), default="orchestrated")
    r.add_argument("--days", type=int, default=120, help="follow-up days per case")
    r.add_argument("--verify", action="store_true", help="check the closure rule and consent invariants on every log")
    r.add_argument("--events", default=None, help="write the event log as JSON lines (use {arm} in the name)")
    r.set_defaults(fn=_cmd_run)
    e = sub.add_parser("evaluate", parents=[common], help="replicated evaluation -> results.json + results.md")
    e.add_argument("--reps", type=int, default=20)
    e.add_argument("--days", type=int, default=360, help="follow-up days per case (360 covers the 12-month metrics)")
    e.add_argument("--bootstrap", type=int, default=1000)
    e.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1), help="worker processes (never changes results)")
    e.add_argument("--out-dir", default=str(REPO_ROOT / "docs" / "results"))
    e.set_defaults(fn=_cmd_evaluate)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
