#!/usr/bin/env python3
"""`make check` / `make check-full`: run the suites, print a summary (F1, T-F1; speed-up F15, T-F26).

Two tiers (F15). `make check` is the FAST tier, the gate before every merge; `make check-full` is everything:

  fast   test, verify, test-db, web-test, web-build, web-e2e, docs-check, tracker-lint, test-security, test-tools,
         test-redteam, load-check, review-packs-check, test-review-packs, effort-check, and `evaluate-smoke` (the
         evaluate determinism check at 1 replication instead of 20)
  heavy  the simulator-heavy checks: sensitivity-check, evaluate-check (full size; replaces evaluate-smoke), risk-check,
         calibrate-check, validate-check, evaluate-capacity-check,
         evaluate-outcomes-check
  full   fast + heavy

PATH RULE: `make check` adds the heavy stages by itself when the working tree differs from main in a path that can move
their results: simulator/, config/pathways/, tools/calibration/, docs/results/, api/app/risk/ and the engine modules the
simulator imports (HEAVY_PATH_PREFIXES below). The diff is `git diff <merge-base with origin/main (or main)>` plus
untracked files, so uncommitted edits count. `CHECK_HEAVY=1` forces the heavy stages on, `CHECK_HEAVY=0` forces them off.
The nightly CI run and the `simulator-heavy` CI job cover the same stages regardless of the diff.

Stages are make targets, each runnable on its own. They run CONCURRENTLY in a worker pool (default min(cores, 6),
`CHECK_JOBS=n` or `--jobs n` to change it), longest first, with ordering only where a real dependency exists (DEPENDS:
web-e2e waits for web-build, whose output it serves). Each stage's output is buffered and printed as one block when the
stage ends, so blocks never interleave. `CHECK_SERIAL=1` (or `--serial`) restores the old behaviour: one stage at a time,
in list order, output streamed live, stop at the first failure.

A skipped stage is printed as a boxed warning and counted separately; it is not a pass. `--keep-going` (or
CHECK_KEEP_GOING=1) runs every stage even after a failure; without it no NEW stage starts once one has failed (stages
already running finish), and the unstarted ones are reported NOT RUN. The exit status is non-zero on a failure. The api
tests run with PYTHONPATH=<this checkout>/api so that a git worktree tests its own code, not the checkout the venv was
installed from.

Usage: python tools/check/run_check.py [--tier fast|full] [--keep-going] [--serial] [--jobs N] [--only a,b] [--skip a,b] [--plan]
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PG_HOST = os.environ.get("CHECK_PG_HOST", "localhost")
PG_PORT = int(os.environ.get("CHECK_PG_PORT", "5433"))
DEFAULT_KEEP_GOING = os.environ.get("CHECK_KEEP_GOING") == "1"
DEFAULT_SERIAL = os.environ.get("CHECK_SERIAL") == "1"


def pg_reachable() -> bool:
    try:
        with socket.create_connection((PG_HOST, PG_PORT), timeout=2):
            return True
    except OSError:
        return False


@dataclass
class Stage:
    name: str
    needs_pg: bool = False
    web: bool = False
    needs_path: str = ""  # a gitignored input the stage reads; SKIPPED loudly (not a pass) when it is absent
    make_args: tuple[str, ...] = ()  # the make target is `name` unless this is set
    weight: int = 10  # rough seconds on the reference Mac, only to start the long stages first
    tier: str = "fast"  # "fast" or "heavy"
    replaces: str = ""  # a fast stage this heavy stage supersedes when both would run


STAGES = [
    Stage("test", weight=100),
    Stage("verify", weight=30),
    Stage("test-db", needs_pg=True, weight=30),
    Stage("web-test", web=True, weight=5),
    Stage("web-build", weight=3),
    Stage("evaluate-smoke", weight=15),  # `make evaluate-smoke`: evaluate-check at REPS=1, the determinism smoke of the fast tier
    Stage("docs-check", weight=1),
    Stage("tracker-lint", weight=1),
    Stage("test-security", weight=16),
    Stage("test-tools", weight=17),
    Stage("test-redteam", weight=18),
    Stage("load-check", needs_pg=True, weight=3),
    Stage("review-packs-check", weight=1),
    Stage("test-review-packs", weight=2),
    Stage("effort-check", weight=6),
    Stage("web-e2e", web=True, weight=25),
    # --- the simulator-heavy tier: regeneration checks that move only when the simulator, the pathways or their parameters do
    Stage("sensitivity-check", tier="heavy", weight=330),
    Stage("evaluate-check", tier="heavy", weight=130, replaces="evaluate-smoke"),
    Stage("validate-check", tier="heavy", weight=150),  # B20: held-out validation regenerated twice and compared byte for byte
    Stage("evaluate-capacity-check", tier="heavy", weight=120),
    Stage("evaluate-outcomes-check", tier="heavy", weight=240),  # M7-3: health outcomes regenerated and compared byte for byte
    Stage("risk-check", tier="heavy", weight=15),
    Stage("calibrate-check", tier="heavy", needs_path="data/external/MANIFEST.json", weight=60),
]
STAGE_BY_NAME = {s.name: s for s in STAGES}

# stage -> stages that must have PASSED before it starts. web-e2e serves the production build web-build writes to web/dist
# (two builds into one directory would also race). Nothing else shares an output: the integration tests have a database
# per worker built from a template (api/tests/xdist_db.py), the regeneration checks write to temp directories.
DEPENDS: dict[str, tuple[str, ...]] = {"web-e2e": ("web-build",)}

# The path rule: a diff touching one of these adds the heavy tier to `make check`.
HEAVY_PATH_PREFIXES = (
    "simulator/",  # the engine, its parameter files (simulator/params*.yaml) and the tests of both
    "config/pathways/",  # the pathway YAML the simulator reads
    "tools/calibration/",  # the fitted values and held-out targets behind the `empirical` parameters
    "docs/results/",  # the committed artefacts the heavy checks compare against
    "api/app/risk/",  # the committed risk model
    "api/app/state_machine.py", "api/app/pathway.py", "api/app/events.py",  # the engine modules the simulator imports
    "api/app/effort.py", "api/app/calendar.py",
)

PLAYWRIGHT_RE = re.compile(r"^(\d+ passed)(?: \([\d.]+m?s\))?$")
PYTEST_RE = re.compile(r"=+ (.*(?:passed|failed|error).*) in [\d.]+s.*=+|^(\d+ (?:passed|failed).*) in [\d.]+s")
VITEST_RE = re.compile(r"Tests\s+(.*)")
MARKER_RE = re.compile(r"^(?:[\w-]+): (?:PASS|FAIL)\b.*$")


def summarise(output: str) -> str:
    """One line of counts for the summary table, from whatever the stage printed."""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    pytest_lines = [m.group(1) or m.group(2) for ln in lines if (m := PYTEST_RE.search(ln))]
    markers = [ln for ln in lines if MARKER_RE.match(ln)]
    playwright = [m.group(1) for ln in lines if (m := PLAYWRIGHT_RE.match(ln))]
    parts: list[str] = []
    if pytest_lines:
        parts.append(" + ".join(pytest_lines) if len(pytest_lines) > 1 else pytest_lines[-1])
    for ln in lines:
        m = VITEST_RE.search(ln)
        if m and "passed" in m.group(1):
            parts.append("vitest " + m.group(1).strip())
            break
    if playwright and not pytest_lines:
        parts.append("playwright " + playwright[-1])
    if markers:
        parts.append(markers[-1])
    if not parts:
        built = [ln for ln in lines if "built in" in ln]
        parts.append(built[-1] if built else (lines[-1] if lines else ""))
    return "; ".join(parts)[:200]


# ---- which stages, and the path rule -------------------------------------------------------------------------------

def changed_paths(root: Path = ROOT) -> list[str] | None:
    """Paths that differ from main: committed since the merge base, modified, and untracked. None if git cannot say."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}

    def git(*args: str) -> str | None:
        p = subprocess.run(["git", *args], cwd=root, env=env, capture_output=True, text=True)
        return p.stdout if p.returncode == 0 else None

    base = None
    for ref in ("origin/main", "main"):
        out = git("merge-base", "HEAD", ref)
        if out and out.strip():
            base = out.strip()
            break
    if base is None:
        return None
    tracked = git("diff", "--name-only", base)
    untracked = git("ls-files", "--others", "--exclude-standard")
    if tracked is None or untracked is None:
        return None
    return sorted({p for p in (tracked + untracked).splitlines() if p})


def touches_heavy_paths(paths: list[str]) -> list[str]:
    return [p for p in paths if p.startswith(HEAVY_PATH_PREFIXES)]


def heavy_decision(tier: str, env_override: str | None, paths: list[str] | None) -> tuple[bool, str]:
    """(run the heavy stages?, the reason printed in the header)."""
    if tier == "full":
        return True, "tier full"
    if env_override == "1":
        return True, "CHECK_HEAVY=1"
    if env_override == "0":
        return False, "CHECK_HEAVY=0"
    if paths is None:
        return False, "git could not give a diff against main; heavy stages off, `make check-full` runs them"
    hit = touches_heavy_paths(paths)
    if hit:
        more = f" (+{len(hit) - 1} more)" if len(hit) > 1 else ""
        return True, f"path rule: the diff against main touches {hit[0]}{more}"
    return False, "path rule: no simulator, pathway, parameter or result path differs from main"


def select_stages(heavy: bool, only: set[str], skip: set[str]) -> list[Stage]:
    if only:  # an explicit --only runs what it names, whatever the tier
        chosen = [s for s in STAGES if s.name in only]
    else:
        chosen = [s for s in STAGES if s.tier == "fast" or heavy]
        superseded = {s.replaces for s in chosen if s.replaces}
        chosen = [s for s in chosen if s.name not in superseded]
    return [s for s in chosen if s.name not in skip]


# ---- running one stage ---------------------------------------------------------------------------------------------

def run(cmd: list[str], env: dict[str, str], stream: bool = True) -> tuple[int, str]:
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        if stream:
            sys.stdout.write(line)
        out.append(line)
    proc.wait()
    return proc.returncode, "".join(out)


def box(message: str) -> None:
    bar = "!" * 78
    print(f"\n{bar}\n!! {message}\n{bar}\n", flush=True)


@dataclass
class Result:
    name: str
    status: str  # PASS FAIL SKIPPED NOT RUN
    seconds: float = 0.0
    detail: str = ""
    output: str = field(default="", repr=False)
    skip_box: str = ""


_print_lock = threading.Lock()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def stage_env(stage: Stage, base_env: dict[str, str], prebuilt_web: bool) -> dict[str, str]:
    env = dict(base_env)
    if stage.name == "web-e2e":
        # Its own preview port (a preview left on 4173 by another checkout would be reused and test the wrong build), and
        # the build web-build just made when that ran in this invocation (playwright.config.ts: E2E_PREBUILT).
        env.setdefault("E2E_PORT", str(free_port()))
        if prebuilt_web:
            env["E2E_PREBUILT"] = "1"
    return env


def execute(stage: Stage, base_env: dict[str, str], *, stream: bool, prebuilt_web: bool) -> Result:
    """Run one stage to completion. Output is returned (and streamed live only in serial mode)."""
    if stage.needs_pg and not pg_reachable():
        return Result(stage.name, "SKIPPED", 0.0, f"Postgres not reachable on {PG_HOST}:{PG_PORT}",
                      skip_box=f"SKIPPED {stage.name}: Postgres is not reachable on {PG_HOST}:{PG_PORT}. "
                               "Start it with `docker compose up -d postgres` and rerun. This is NOT a pass.")
    if stage.needs_path and not (ROOT / stage.needs_path).exists():
        return Result(stage.name, "SKIPPED", 0.0, f"{stage.needs_path} missing; run make calibrate-fetch",
                      skip_box=f"SKIPPED {stage.name}: {stage.needs_path} is missing (gitignored downloads). Run "
                               "`make calibrate-fetch` once and rerun. This is NOT a pass.")
    t0 = time.perf_counter()
    cmd = ["make", "--no-print-directory", *(stage.make_args or (stage.name,))]
    rc, out = run(cmd, stage_env(stage, base_env, prebuilt_web), stream=stream)
    return Result(stage.name, "PASS" if rc == 0 else "FAIL", time.perf_counter() - t0, summarise(out), out)


def ensure_web_install(stages: list[Stage], env: dict[str, str]) -> Result | None:
    if any(s.web for s in stages) and not (ROOT / "web/node_modules").exists():
        print("web/node_modules is missing: running `make web-install` first", flush=True)
        t0 = time.perf_counter()
        rc, _ = run(["make", "web-install"], env)
        if rc:
            return Result("web-install", "FAIL", time.perf_counter() - t0, "make web-install failed")
    return None


def emit_block(res: Result) -> None:
    """One finished stage's output as a single uninterrupted block."""
    with _print_lock:
        if res.skip_box:
            print(f"\n===== make {res.name}: SKIPPED =====", flush=True)
            box(res.skip_box)
            return
        print(f"\n===== make {res.name}: {res.status} in {res.seconds:.1f}s =====", flush=True)
        sys.stdout.write(res.output if res.output.endswith("\n") or not res.output else res.output + "\n")
        sys.stdout.flush()


def run_serial(stages: list[Stage], env: dict[str, str], keep_going: bool) -> list[Result]:
    results: list[Result] = []
    failed = False
    names = {s.name for s in stages}
    for stage in stages:
        dep_bad = [d for d in DEPENDS.get(stage.name, ())
                   if d in names and not any(r.name == d and r.status == "PASS" for r in results)]
        if (failed and not keep_going) or dep_bad:
            why = "an earlier stage failed" if failed and not keep_going else f"dependency {dep_bad[0]} did not pass"
            results.append(Result(stage.name, "NOT RUN", 0.0, why))
            continue
        print(f"\n===== make {stage.name} =====", flush=True)
        res = execute(stage, env, stream=True, prebuilt_web="web-build" in names)
        if res.skip_box:
            box(res.skip_box)
        results.append(res)
        failed |= res.status == "FAIL"
    return results


def run_parallel(stages: list[Stage], env: dict[str, str], keep_going: bool, jobs: int, executor_fn=None) -> list[Result]:
    executor_fn = executor_fn or execute
    names = {s.name for s in stages}
    pending = sorted(stages, key=lambda s: -s.weight)  # longest first
    results: dict[str, Result] = {}
    running: dict[Future, Stage] = {}
    failed = False
    prebuilt = "web-build" in names

    def ready(s: Stage) -> bool:
        return all(d not in names or d in results for d in DEPENDS.get(s.name, ()))

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        while pending or running:
            for s in list(pending):  # a stage whose dependency did not pass will never run
                bad = [d for d in DEPENDS.get(s.name, ()) if d in results and results[d].status != "PASS"]
                if bad:
                    pending.remove(s)
                    results[s.name] = Result(s.name, "NOT RUN", 0.0, f"dependency {bad[0]} did not pass")
            if failed and not keep_going:
                for s in pending:
                    results[s.name] = Result(s.name, "NOT RUN", 0.0, "an earlier stage failed")
                pending = []
            while pending and len(running) < jobs:
                nxt = next((s for s in pending if ready(s)), None)
                if nxt is None:
                    break
                pending.remove(nxt)
                with _print_lock:
                    print(f"[start] {nxt.name}", flush=True)
                running[pool.submit(executor_fn, nxt, env, stream=False, prebuilt_web=prebuilt)] = nxt
            if not running:
                break
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for fut in done:
                stage = running.pop(fut)
                try:
                    res = fut.result()
                except Exception as exc:  # a harness error is a failure of that stage, not of the whole run
                    res = Result(stage.name, "FAIL", 0.0, f"harness error: {exc!r}")
                results[stage.name] = res
                failed |= res.status == "FAIL"
                emit_block(res)
    return [results[s.name] for s in stages]


def summary(results: list[Result], title: str, wall: float | None = None) -> int:
    print(f"\n===== {title}: summary =====")
    width = max(len(r.name) for r in results) if results else 10
    for r in results:
        print(f"{r.name:<{width}}  {r.status:<8} {r.seconds:6.1f}s  {r.detail}")
    n = {k: sum(1 for r in results if r.status == k) for k in ("PASS", "FAIL", "SKIPPED", "NOT RUN")}
    verdict = "FAIL" if n["FAIL"] else ("PASS WITH SKIPS" if n["SKIPPED"] else "PASS")
    print(f"check: {verdict} ({n['PASS']} passed, {n['FAIL']} failed, {n['SKIPPED']} skipped, {n['NOT RUN']} not run)")
    if wall:
        total = sum(r.seconds for r in results)
        print(f"wall time {wall:.1f}s for {total:.1f}s of stage time (x{total / wall:.1f} overlap)")
    if n["SKIPPED"]:
        box(f"{n['SKIPPED']} stage(s) were SKIPPED, so this run did not prove everything. See above.")
    return 1 if n["FAIL"] else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tier", choices=("fast", "full"), default=os.environ.get("CHECK_TIER", "fast"))
    ap.add_argument("--keep-going", action="store_true", default=DEFAULT_KEEP_GOING)
    ap.add_argument("--serial", action="store_true", default=DEFAULT_SERIAL)
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("CHECK_JOBS", "0")))
    ap.add_argument("--only", default="")
    ap.add_argument("--skip", default="")
    ap.add_argument("--plan", action="store_true", help="print the stages that would run, and why, then exit")
    args = ap.parse_args(argv)
    only = {s for s in args.only.split(",") if s}
    skip = {s for s in args.skip.split(",") if s}
    unknown = (only | skip) - set(STAGE_BY_NAME)
    if unknown:
        print(f"unknown stage(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2

    if only:
        heavy, why = False, "--only"
    else:
        paths = None if args.tier == "full" else changed_paths()
        heavy, why = heavy_decision(args.tier, os.environ.get("CHECK_HEAVY"), paths)
    stages = select_stages(heavy, only, skip)
    jobs = 1 if args.serial else (args.jobs or min(os.cpu_count() or 2, 6))
    label = "make check-full" if args.tier == "full" else "make check"
    mode = "serial" if args.serial else f"{jobs} at a time"
    print(f"{label}: {len(stages)} stages, {mode}; heavy tier {'ON' if heavy else 'off'} ({why})", flush=True)
    if args.plan:
        for s in stages:
            print(f"  {s.name:<24} {s.tier:<6} weight {s.weight:>4}  after {','.join(DEPENDS.get(s.name, ())) or '-'}")
        return 0

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "api"), env.get("PYTHONPATH", "")]))
    env["GIT_OPTIONAL_LOCKS"] = "0"  # concurrent read-only git commands must not fight over .git/index.lock
    env.setdefault("PYTEST_ADDOPTS", "-p no:cacheprovider")  # concurrent pytest runs share no .pytest_cache

    t0 = time.perf_counter()
    bad = ensure_web_install(stages, env)
    if bad:
        return summary([bad], label)
    if args.serial:
        results = run_serial(stages, env, args.keep_going)
    else:
        results = run_parallel(stages, env, args.keep_going, jobs)
    return summary(results, label, time.perf_counter() - t0)


if __name__ == "__main__":
    sys.exit(main())
