#!/usr/bin/env python3
"""`make check`: run every suite in order, stop at the first failure, print a summary (F1, T-F1).

Stages (each is a make target, so each can be run on its own):

    test          api unit + property + simulator tests (no database)
    test-db       Postgres integration tests; SKIPPED loudly if Postgres on :5433 is unreachable
    web-test      frontend unit tests (runs `make web-install` first if web/node_modules is missing)
    web-build     TypeScript check and production build
    evaluate-check  `make evaluate` twice, results.json must be identical
    docs-check    broken links, ragged tables, TODO markers in docs/ and project/
    tracker-lint  TRACKER / TEST-CASES / LOG cross-references, done rows have real commits
    test-security secret scan of tracked files and history (F9; F7/F8 extend this target)
    test-tools    the cross-cutting tests in tests/ (scanner, linters, load harness)
    load-check    sweep over 1,000 seeded cases (F6; full sizes are `make test-load`);
                  SKIPPED loudly if Postgres on :5433 is unreachable

A skipped stage is printed as a boxed warning and counted separately; it is not a pass.
`--keep-going` (or CHECK_KEEP_GOING=1) runs every stage even after a failure, for reporting;
the exit status is still non-zero. The api tests are run with PYTHONPATH=<this checkout>/api so
that a git worktree tests its own code rather than whichever checkout the venv was installed from.

Usage: python tools/check/run_check.py [--keep-going] [--only a,b] [--skip a,b]
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PG_HOST = os.environ.get("CHECK_PG_HOST", "localhost")
PG_PORT = int(os.environ.get("CHECK_PG_PORT", "5433"))


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
    before: tuple[str, ...] = ()  # make targets run first (not reported separately)
    web: bool = False


STAGES = [
    Stage("test"),
    Stage("test-db", needs_pg=True),
    Stage("web-test", web=True),
    Stage("web-build"),
    Stage("evaluate-check"),
    Stage("docs-check"),
    Stage("tracker-lint"),
    Stage("test-security"),
    Stage("test-tools"),
    Stage("load-check", needs_pg=True),
]

PYTEST_RE = re.compile(r"=+ (.*(?:passed|failed|error).*) in [\d.]+s.*=+|^(\d+ (?:passed|failed).*) in [\d.]+s")
VITEST_RE = re.compile(r"Tests\s+(.*)")
MARKER_RE = re.compile(r"^(?:[\w-]+): (?:PASS|FAIL)\b.*$")


def summarise(output: str) -> str:
    """One line of counts for the summary table, from whatever the stage printed."""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    pytest_lines = [m.group(1) or m.group(2) for ln in lines if (m := PYTEST_RE.search(ln))]
    markers = [ln for ln in lines if MARKER_RE.match(ln)]
    parts: list[str] = []
    if pytest_lines:
        parts.append(" + ".join(pytest_lines) if len(pytest_lines) > 1 else pytest_lines[-1])
    for ln in lines:
        m = VITEST_RE.search(ln)
        if m and "passed" in m.group(1):
            parts.append("vitest " + m.group(1).strip())
            break
    if markers:
        parts.append(markers[-1])
    if not parts:
        built = [ln for ln in lines if "built in" in ln]
        parts.append(built[-1] if built else (lines[-1] if lines else ""))
    return "; ".join(parts)[:200]


def run(cmd: list[str], env: dict[str, str]) -> tuple[int, str]:
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        out.append(line)
    proc.wait()
    return proc.returncode, "".join(out)


def box(message: str) -> None:
    bar = "!" * 78
    print(f"\n{bar}\n!! {message}\n{bar}\n", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep-going", action="store_true", default=os.environ.get("CHECK_KEEP_GOING") == "1")
    ap.add_argument("--only", default="")
    ap.add_argument("--skip", default="")
    args = ap.parse_args(argv)
    only = {s for s in args.only.split(",") if s}
    skip = {s for s in args.skip.split(",") if s}
    unknown = (only | skip) - {s.name for s in STAGES}
    if unknown:
        print(f"unknown stage(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "api"), env.get("PYTHONPATH", "")]))
    results: list[tuple[str, str, float, str]] = []  # name, status, seconds, detail
    failed = False
    for stage in STAGES:
        if (only and stage.name not in only) or stage.name in skip:
            continue
        if failed and not args.keep_going:
            results.append((stage.name, "NOT RUN", 0.0, "an earlier stage failed"))
            continue
        print(f"\n===== make {stage.name} =====", flush=True)
        if stage.needs_pg and not pg_reachable():
            box(f"SKIPPED {stage.name}: Postgres is not reachable on {PG_HOST}:{PG_PORT}. "
                "Start it with `docker compose up -d postgres` and rerun. This is NOT a pass.")
            results.append((stage.name, "SKIPPED", 0.0, f"Postgres not reachable on {PG_HOST}:{PG_PORT}"))
            continue
        t0 = time.perf_counter()
        if stage.web and not (ROOT / "web/node_modules").exists():
            print("web/node_modules is missing: running `make web-install` first", flush=True)
            rc, out = run(["make", "web-install"], env)
            if rc:
                results.append((stage.name, "FAIL", time.perf_counter() - t0, "make web-install failed"))
                failed = True
                continue
        rc, out = run(["make", "--no-print-directory", stage.name], env)
        status = "PASS" if rc == 0 else "FAIL"
        results.append((stage.name, status, time.perf_counter() - t0, summarise(out)))
        failed |= rc != 0

    print("\n===== make check: summary =====")
    width = max(len(r[0]) for r in results) if results else 10
    for name, status, secs, detail in results:
        print(f"{name:<{width}}  {status:<8} {secs:6.1f}s  {detail}")
    n_fail = sum(1 for r in results if r[1] == "FAIL")
    n_skip = sum(1 for r in results if r[1] == "SKIPPED")
    n_pass = sum(1 for r in results if r[1] == "PASS")
    n_notrun = sum(1 for r in results if r[1] == "NOT RUN")
    verdict = "FAIL" if n_fail else ("PASS WITH SKIPS" if n_skip else "PASS")
    print(f"check: {verdict} ({n_pass} passed, {n_fail} failed, {n_skip} skipped, {n_notrun} not run)")
    if n_skip:
        box(f"{n_skip} stage(s) were SKIPPED, so this run did not prove everything. See above.")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
