"""Command line: `python -m simulator.sim.cli run ...` and `... evaluate ...` (run from the repo root)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import DEFAULT_CONSERVATIVE, REPO_ROOT
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
                if arm == "orchestrated" and c.orch:
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
    overlay = [] if args.no_conservative else (args.conservative_params or [DEFAULT_CONSERVATIVE])
    result = evaluate(args.reps, args.cases, args.days, args.seed, args.params, args.pathway, args.bootstrap, args.jobs, overlay)
    jpath, mpath = write_results(result, Path(args.out_dir), ctx)
    print(f"wrote {jpath} and {mpath} in {time.perf_counter() - t0:.1f}s")
    return 0


def _cmd_sensitivity(args) -> int:
    from .sensitivity import sweep, write_outputs

    t0 = time.perf_counter()
    result = sweep(args.reps, args.cases, args.days, args.seed, args.jobs, args.params, args.pathway)
    out_dir = Path(args.out_dir)
    if args.check:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            fresh = write_outputs(result, Path(tmp))
            bad = [f.name for f in fresh if not (out_dir / f.name).exists() or (out_dir / f.name).read_bytes() != f.read_bytes()]
        if bad:
            print(f"sensitivity-check: FAIL, {', '.join(bad)} differ from a fresh run ({time.perf_counter() - t0:.0f}s)")
            return 1
        print(f"sensitivity-check: PASS, sensitivity.json, .md and .svg are byte-identical to a fresh run ({time.perf_counter() - t0:.0f}s)")
        return 0
    paths = write_outputs(result, out_dir)
    print(f"wrote {', '.join(str(p) for p in paths)} in {time.perf_counter() - t0:.1f}s")
    return 0


def _cmd_evaluate_capacity(args) -> int:
    """Finite-capacity evaluation -> capacity.json, and the "Under finite capacity" section of results.md (spliced in place)."""
    from .capacity_eval import evaluate_capacity, write_capacity
    from .capacity_report import splice_into_results

    t0 = time.perf_counter()
    if args.render_only:  # rewrite the results.md section from the committed capacity.json (no simulation)
        spliced = splice_into_results(Path(args.out_dir))
        print("re-rendered the capacity section of results.md from capacity.json" if spliced else "no results.md to update")
        return 0
    conservative = [] if args.no_conservative else (args.conservative_params or [DEFAULT_CONSERVATIVE])
    result = evaluate_capacity(args.reps, args.cases, args.days, args.seed, args.params, conservative, args.capacity_params,
                               args.pathway, args.bootstrap, args.jobs)
    out_dir = Path(args.out_dir)
    jpath = write_capacity(result, out_dir)
    spliced = splice_into_results(out_dir)
    print(f"wrote {jpath}" + (" and the capacity section of results.md" if spliced else " (no results.md to update)")
          + f" in {time.perf_counter() - t0:.1f}s")
    return 0


def _cmd_effort(args) -> int:
    """Print the Human effort section from results.json, or with --check verify it is current (no simulation)."""
    from .effort import SOURCES, effort_hash
    from .report import effort_section

    res = json.loads(Path(args.results).read_text())
    if not args.check:
        print(effort_section(res))
        return 0
    problems = []
    eff = res.get("effort")
    if not eff:
        problems.append(f"{args.results} has no `effort` key: run make evaluate")
    elif eff["model"]["effort_hash"] != effort_hash():
        problems.append("results.json was made with a different effort model than simulator/sim/effort.py: run make evaluate")
    evidence = (REPO_ROOT / "docs" / "evidence.md").read_text()
    if "## 11. Human effort per action" not in evidence:
        problems.append("docs/evidence.md has no section 11 'Human effort per action'")
    else:
        sec = evidence[evidence.index("## 11. Human effort per action"):]
        problems += [f"docs/evidence.md section 11 does not cite {sid}" for sid in SOURCES if sid not in sec]
    md = Path(args.results).with_name("results.md")
    if not md.exists() or "## Human effort" not in md.read_text():
        problems.append("results.md has no 'Human effort' section: run make evaluate")
    if problems:
        print("effort-check: FAIL\n  " + "\n  ".join(problems))
        return 1
    print(f"effort-check: PASS, results.json carries the current effort model (sha256 {effort_hash()[:16]}), evidence.md section 11 cites every source")
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
    e.add_argument("--conservative-params", action="append", default=None,
                   help="overlay layered on the --params stack to define the literature-anchored scenario "
                        "(repeatable; default simulator/params.conservative.yaml)")
    e.add_argument("--no-conservative", action="store_true", help="evaluate the upper-range scenario only")
    e.add_argument("--bootstrap", type=int, default=1000)
    e.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1), help="worker processes (never changes results)")
    e.add_argument("--out-dir", default=str(REPO_ROOT / "docs" / "results"))
    e.set_defaults(fn=_cmd_evaluate)
    ec = sub.add_parser("evaluate-capacity", parents=[common], help="finite-capacity evaluation -> capacity.json + results.md section")
    ec.add_argument("--reps", type=int, default=20)
    ec.add_argument("--days", type=int, default=360)
    ec.add_argument("--capacity-params", default=str(REPO_ROOT / "simulator" / "params.capacity.yaml"))
    ec.add_argument("--conservative-params", action="append", default=None)
    ec.add_argument("--no-conservative", action="store_true")
    ec.add_argument("--render-only", action="store_true", help="only re-render the results.md section from capacity.json (no simulation)")
    ec.add_argument("--bootstrap", type=int, default=1000)
    ec.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1), help="worker processes (never changes results)")
    ec.add_argument("--out-dir", default=str(REPO_ROOT / "docs" / "results"))
    ec.set_defaults(fn=_cmd_evaluate_capacity)
    ef = sub.add_parser("effort", help="print the Human effort section of results.json, or --check that it is current")
    ef.add_argument("--results", default=str(REPO_ROOT / "docs" / "results" / "results.json"))
    ef.add_argument("--check", action="store_true", help="fail if results.json predates the effort model or evidence.md lacks a source")
    ef.set_defaults(fn=_cmd_effort)
    sn = sub.add_parser("sensitivity", parents=[common], help="one-at-a-time parameter sweep -> sensitivity.json, .md, .svg")
    sn.set_defaults(cases=1000)
    sn.add_argument("--reps", type=int, default=5)
    sn.add_argument("--days", type=int, default=360)
    sn.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1), help="worker processes (never changes results)")
    sn.add_argument("--out-dir", default=str(REPO_ROOT / "docs" / "results"))
    sn.add_argument("--check", action="store_true", help="rerun and fail unless the three committed files are byte-identical")
    sn.set_defaults(fn=_cmd_sensitivity)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
