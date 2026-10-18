#!/usr/bin/env python3
"""Time the scheduler sweep over a seeded load database (F6, T-F8).

For each size: recreate the throwaway database `closure_load`, apply the engine migrations, seed
open cases (tools/load/seed_cases.py), then time `app.sweep.run_sweep` (planning + emission) at the
fixed sweep time, against the real Postgres store. A second sweep immediately afterwards must
emit nothing (idempotency) and is timed too: it is the pure read-and-plan cost.

Correctness is asserted on every run, not just speed:
  * events emitted == what the pure planner said would be due when seeding;
  * no errors, no rejected events, no `close` event anywhere (the scheduler never closes);
  * the second sweep emits 0 events;
  * every case log is still gap-free and the row count grew by exactly the events emitted.

Modes
  default      sizes 1k, 10k, 100k; writes docs/results/load.md and load.json
  --check      reduced size (1k) for `make check`: asserts the above plus a time budget, writes nothing
  --drop       drop the load database and exit

The load database is dropped at the end of a run unless KEEP_LOAD_DB=1.

    python tools/load/time_sweep.py [--sizes 1000,10000,100000] [--scenario daily|backlog]
                                    [--check] [--budget SECONDS] [--profile] [--out docs/results/load.md]
"""

from __future__ import annotations

import argparse
import cProfile
import json
import os
import platform
import pstats
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import seed_cases as sc  # noqa: E402

from app.db import create_pool  # noqa: E402
from app.store_pg import PostgresEventStore  # noqa: E402
from app.sweep import SweepResult, run_sweep  # noqa: E402

REPO_ROOT = sc.REPO_ROOT
DEFAULT_SIZES = (1_000, 10_000, 100_000)
TARGET_100K_SECONDS = 5.0
CHECK_SIZE = 1_000
ROUTE_DEFAULT_WORKERS = min(4, os.cpu_count() or 1)  # what the API uses without SWEEP_WORKERS (A17)
DEFAULT_WORKERS = sorted({ROUTE_DEFAULT_WORKERS, min(os.cpu_count() or 1, 10)})
CHECK_BUDGET_SECONDS = 5.0  # generous for 1k (the 100k target is also 5 s); catches only gross regressions
BASELINE_PATH = REPO_ROOT / "tools/load/baseline.json"


class TimedStore:
    """Delegates to the real store and accumulates time in the two store calls the sweep makes."""

    def __init__(self, store: PostgresEventStore) -> None:
        self._store = store
        self.read_seconds = 0.0
        self.append_seconds = 0.0
        self.appends = 0
        self.rows_read = 0

    def iter_open_case_models(self, pathway, only=None, **kw):
        return self._timed(self._store.iter_open_case_models(pathway, only=only, **kw))

    def iter_due_case_models(self, pathway, calendar, now, **kw):
        return self._timed(self._store.iter_due_case_models(pathway, calendar, now, **kw))

    def _timed(self, it):
        while True:
            t = time.perf_counter()
            try:
                item = next(it)
            except StopIteration:
                self.read_seconds += time.perf_counter() - t
                return
            self.read_seconds += time.perf_counter() - t
            yield item

    def append(self, event, pathway):
        t = time.perf_counter()
        try:
            return self._store.append(event, pathway)
        finally:
            self.append_seconds += time.perf_counter() - t
            self.appends += 1

    def append_batches(self, batches, pathway):
        t = time.perf_counter()
        try:
            return self._store.append_batches(batches, pathway)
        finally:
            self.append_seconds += time.perf_counter() - t
            self.appends += len(batches)

    def append_many(self, events, pathway):
        t = time.perf_counter()
        try:
            return self._store.append_many(events, pathway)
        finally:
            self.append_seconds += time.perf_counter() - t
            self.appends += 1

    def __getattr__(self, name):
        return getattr(self._store, name)


@dataclass
class SizeResult:
    size: int
    scenario: str
    workers: int
    cases_evaluated: int
    cases_skipped: int
    events_before: int
    event_rows_read: int
    emitted: int
    expected_emitted: int
    mark_overdue: int
    escalate: int
    wall_seconds: float
    read_replay_seconds: float
    append_seconds: float
    plan_seconds: float
    resweep_seconds: float
    resweep_emitted: int
    resweep_evaluated: int
    seed_seconds: float
    problems: list[str] = field(default_factory=list)

    @property
    def rows_per_second(self) -> float:
        return self.cases_evaluated / self.wall_seconds if self.wall_seconds else 0.0


class RouteSweep:
    """The sharded sweep exactly as the scheduler service triggers it: `POST /scheduler/sweep` on the
    FastAPI app (A17), with the API's store pointed at the load database and `SWEEP_WORKERS=workers`.
    The worker pool is started before the clock, as in a running API after its first sweep. The
    timing includes the route, the service-token check and the JSON response (in process, no socket)."""

    def __init__(self, url: str, pathway, pool, workers: int) -> None:
        from fastapi.testclient import TestClient

        from app.clock import clock
        from app.config import settings
        from app.main import app
        from app.routers import cases, scheduler

        self._clock, self._cases, self._scheduler = clock, cases, scheduler
        self._saved = (cases.store, cases.pathway)
        os.environ["SWEEP_WORKERS"] = str(workers)
        scheduler.reset_pool()
        scheduler.get_pool().warm()
        cases.store, cases.pathway = PostgresEventStore(pool), pathway
        clock.reset()
        clock.advance(days=(sc.SWEEP_NOW - clock.now()).total_seconds() / 86400)
        assert clock.now() == sc.SWEEP_NOW
        self.client = TestClient(app)
        self.headers = {"Authorization": f"Bearer {settings.scheduler_service_token}"}

    def sweep(self) -> SweepResult:
        resp = self.client.post("/scheduler/sweep", headers=self.headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["workers"] > 1, "the route did not use the sharded sweep"
        out = SweepResult(sim_time=body["sim_time"])
        for key in ("cases_evaluated", "cases_skipped", "mark_overdue", "escalate", "handoff_bounced", "rejected"):
            setattr(out, key, body[key])
        out.errors = body["errors"]
        return out

    def close(self) -> None:
        self._scheduler.reset_pool()
        self._cases.store, self._cases.pathway = self._saved
        self._clock.reset()


def scalar(url: str, query: str):
    import psycopg

    with psycopg.connect(url) as conn:
        return conn.execute(query).fetchone()[0]


def run_size(size: int, scenario: str, profile: bool = False, workers: int = 1, log=print) -> SizeResult:
    pathway, calendar = sc.load_engine()
    stats = sc.seed(size, scenario=scenario, log=log)
    _SEEDED_OID[0] = _load_db_oid()
    url = sc.load_url()
    pool = create_pool(url, max_size=4)
    problems: list[str] = []
    try:
        store = TimedStore(PostgresEventStore(pool))
        events_before = scalar(url, "SELECT count(*) FROM events")
        profiler = cProfile.Profile() if profile else None
        route = RouteSweep(url, pathway, pool, workers) if workers > 1 else None  # worker pool started before the clock
        t0 = time.perf_counter()
        if profiler:
            profiler.enable()
        if route:
            result = route.sweep()
        else:
            result = run_sweep(store, pathway, calendar, sc.SWEEP_NOW)
        if profiler:
            profiler.disable()
        wall = time.perf_counter() - t0
        if profiler:
            print(f"--- cProfile, size {size}, sorted by cumulative time ---")
            pstats.Stats(profiler).sort_stats("cumulative").print_stats(25)

        t1 = time.perf_counter()
        if route:
            again = route.sweep()
        else:
            again = run_sweep(PostgresEventStore(pool), pathway, calendar, sc.SWEEP_NOW)
        resweep = time.perf_counter() - t1
        # Untimed cross-check of the due-time index (A20): a sweep that reads and replays EVERY open case must find
        # nothing left to do, i.e. the indexed sweep missed no case.
        full = run_sweep(PostgresEventStore(pool), pathway, calendar, sc.SWEEP_NOW, full_scan=True)

        emitted = result.mark_overdue + result.escalate + result.handoff_bounced
        events_after = scalar(url, "SELECT count(*) FROM events")
        if result.cases_evaluated + result.cases_skipped != stats.cases:
            problems.append(f"evaluated {result.cases_evaluated} + skipped {result.cases_skipped} cases, seeded {stats.cases}")
        if full.cases_evaluated != stats.cases:
            problems.append(f"the full-scan check read {full.cases_evaluated} cases, seeded {stats.cases}")
        if full.mark_overdue + full.escalate + full.handoff_bounced or full.errors or full.rejected:
            problems.append(f"a full scan after the indexed sweep still found work: {full.mark_overdue + full.escalate + full.handoff_bounced} "
                            f"events, {len(full.errors)} errors, {full.rejected} rejected (the due-time index missed a case)")
        if emitted != stats.expected_emitted:
            problems.append(f"emitted {emitted} events, planner expected {stats.expected_emitted}")
        if result.errors or result.rejected:
            problems.append(f"{len(result.errors)} errors, {result.rejected} rejected: {result.errors[:3]}")
        if again.mark_overdue + again.escalate + again.handoff_bounced:
            problems.append(f"second sweep emitted {again.mark_overdue + again.escalate + again.handoff_bounced} events (not idempotent)")
        if events_after != events_before + emitted:
            problems.append(f"event rows {events_before} -> {events_after}, expected +{emitted}")
        if scalar(url, "SELECT count(*) FROM events WHERE type = 'close'"):
            problems.append("a close event exists: the scheduler must never close a case")
        gaps = scalar(url, "SELECT count(*) FROM (SELECT case_id FROM events GROUP BY case_id "
                           "HAVING max(seq) <> count(*)) t")
        if gaps:
            problems.append(f"{gaps} case logs have a gap in seq")
        if route:
            route.close()
        return SizeResult(
            size=size, scenario=scenario, workers=workers, cases_evaluated=result.cases_evaluated, cases_skipped=result.cases_skipped, events_before=events_before,
            event_rows_read=events_before, emitted=emitted, expected_emitted=stats.expected_emitted,
            mark_overdue=result.mark_overdue, escalate=result.escalate, wall_seconds=wall,
            read_replay_seconds=store.read_seconds, append_seconds=store.append_seconds,
            plan_seconds=max(wall - store.read_seconds - store.append_seconds, 0.0),
            resweep_seconds=resweep, resweep_emitted=again.mark_overdue + again.escalate + again.handoff_bounced,
            resweep_evaluated=again.cases_evaluated, seed_seconds=stats.seconds, problems=problems,
        )
    finally:
        pool.close()


# ---------------------------------------------------------------- machine spec and report


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def machine_spec() -> dict[str, str]:
    spec: dict[str, str] = {"os": f"{platform.system()} {platform.release()} ({platform.machine()})"}
    if platform.system() == "Darwin":
        spec["cpu"] = _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor()
        mem = _run(["sysctl", "-n", "hw.memsize"])
        spec["ram"] = f"{int(mem) / 2**30:.0f} GiB" if mem.isdigit() else "unknown"
    else:
        cpu = ""
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
        spec["cpu"] = cpu or platform.processor() or "unknown"
        try:
            kb = int(next(ln for ln in Path("/proc/meminfo").read_text().splitlines() if ln.startswith("MemTotal")).split()[1])
            spec["ram"] = f"{kb / 2**20:.0f} GiB"
        except (OSError, StopIteration, ValueError):
            spec["ram"] = "unknown"
    spec["cores"] = str(os.cpu_count())
    import psycopg

    spec["python"] = f"{platform.python_version()} (psycopg {psycopg.__version__})"
    try:
        spec["postgres"] = scalar(sc.admin_url(), "SELECT split_part(version(), ' on ', 1)")
    except Exception:  # noqa: BLE001 - the spec is informational
        spec["postgres"] = "unknown"
    if shutil.which("docker"):
        info = _run(["docker", "info", "--format", "{{.NCPU}} CPUs, {{.MemTotal}} bytes, {{.OperatingSystem}}"])
        if info:
            parts = info.split(", ")
            try:
                mem_gib = f"{int(parts[1].split()[0]) / 2**30:.1f} GiB"
            except (IndexError, ValueError):
                mem_gib = "?"
            spec["docker"] = f"{parts[0]}, {mem_gib}, {parts[-1]}; Postgres runs in the compose `postgres` container"
    return spec


def git_commit() -> str:
    return _run(["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"]) or "unknown"


def fmt(x: float, places: int = 2) -> str:
    return f"{x:,.{places}f}"


def render_markdown(results: list[SizeResult], spec: dict[str, str], extra: list[SizeResult], commit: str | None = None,
                    first: list[SizeResult] | None = None) -> str:
    daily = [r for r in results if r.scenario == "daily"]
    single = [r for r in daily if r.workers == 1]
    big1 = next((r for r in daily if r.size == 100_000 and r.workers == 1), None)
    bigs = [r for r in daily if r.size == 100_000 and r.workers > 1]
    verdict = ""
    if big1:
        met = lambda r: "met" if r.wall_seconds <= TARGET_100K_SECONDS else "not met"  # noqa: E731
        verdict = (f"**Target: the 100,000-case sweep under {TARGET_100K_SECONDS:g} s. Single process (`SWEEP_WORKERS=1`): "
                   f"{fmt(big1.wall_seconds)} s ({met(big1)}).")
        for bigp in bigs:
            default = " (the API default)" if bigp.workers == ROUTE_DEFAULT_WORKERS else ""
            verdict += f" Through `POST /scheduler/sweep` with {bigp.workers} workers{default}: {fmt(bigp.wall_seconds)} s ({met(bigp)})."
        verdict += "**"
    lines = [
        "# Scheduler sweep load test",
        "",
        "Generated by `make test-load` (`tools/load/time_sweep.py`). Do not edit by hand. All data are synthetic.",
        "",
        verdict,
        "",
        "## Results",
        "",
        "One sweep (`app.sweep.run_sweep`: select the open cases that can be due, read and replay them, plan, append the "
        "scheduler's events) over a freshly seeded `closure_load` database, against the real Postgres store. Scenario `daily`: the "
        "previous sweep ran one working day earlier, so the timed sweep emits only the breaches and escalation rungs that became "
        "due since then, and `cases.next_due_at` (migration 012, A20) is as the last append left it. Cases are spread over the "
        "eleven open states (main chain plus consent withdrawn, referral rejected and lost contact); about 40% are already "
        "overdue. Workers 1 is `run_sweep` in one process (`SWEEP_WORKERS=1`); workers N is `POST /scheduler/sweep` on the FastAPI "
        "app (A17) with `SWEEP_WORKERS=N`: the route's warm long-lived pool of N processes, each sweeping a disjoint slice of the "
        "cases. The route's default is min(4, cpus). Route timings include the service-token check and the JSON response, in "
        "process (no socket). \"Read\" is the number of cases the sweep selected, read and replayed; the rest were skipped "
        "because their stored due time is still in the future.",
        "",
        "| Open cases | Workers | Sweep wall time (s) | Events emitted | Cases read | Cases skipped | Event rows in the database | Re-sweep wall time (s) | Re-sweep: cases read |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in daily:
        lines.append(f"| {r.size:,} | {r.workers} | {fmt(r.wall_seconds)} | {r.emitted:,} | {r.cases_evaluated:,} | {r.cases_skipped:,} | "
                     f"{r.event_rows_read:,} | {fmt(r.resweep_seconds)} | {r.resweep_evaluated:,} |")
    lines += [
        "",
        "Re-sweep: the same sweep run again straight afterwards. It emits nothing (idempotency is asserted) so it is the cost of "
        "a sweep with nothing to do, which is what the 10-second loop mostly runs. After every sweep, untimed, a full scan "
        "(`full_scan=True`: read and replay every case) must also find nothing to do; that is the check that the due-time "
        "index skipped no case.",
        "",
        "## Where the time goes (single process)",
        "",
        "Appends is the number of events emitted; with batched writes (A20) many cases share one append call, so the time is "
        "per sweep, not per event.",
        "",
        "| Open cases | Read and replay (s) | Planning (s) | Emission, appends (s) | Events |",
        "|---|---|---|---|---|",
    ]
    for r in single:
        lines.append(f"| {r.size:,} | {fmt(r.read_replay_seconds)} | {fmt(r.plan_seconds)} | {fmt(r.append_seconds)} | {r.emitted:,} |")
    if extra:
        lines += [
            "",
            "## Cold start (scenario `backlog`)",
            "",
            "No sweep has run since the cases were created, for example after a long scheduler outage, so every breach and every "
            "rung is pending and the sweep must append all of them, so far more cases are due than in the daily scenario "
            "and the index skips only those with nothing due. Each case's append still locks the case, re-reads its log and re-verifies the replay (the engine's "
            "safety property); the sweep shares the transaction between up to 100 cases (A20).",
            "",
            "| Open cases | Workers | Sweep wall time (s) | Events emitted | Cases read | Cases skipped |",
            "|---|---|---|---|---|---|",
        ]
        for r in extra:
            lines.append(f"| {r.size:,} | {r.workers} | {fmt(r.wall_seconds)} | {r.emitted:,} | {r.cases_evaluated:,} | {r.cases_skipped:,} |")
    if first:
        lines += [
            "",
            "## First sweep after migration 012 (scenario `unindexed`)",
            "",
            "Migration 012 adds `cases.next_due_at`, NULL for every case that exists when it runs. NULL means \"not computed\", so the "
            "first sweep reads and replays every case once (as a sweep did before A20), writes each case's due time back, and the "
            "sweeps after it skip as in the first table. This is the one-off cost of switching the index on; it is paid once per "
            "database and again only if the calendar or the pathway's timings change (the stored fingerprint then differs).",
            "",
            "| Open cases | Workers | Sweep wall time (s) | Events emitted | Cases read | Re-sweep wall time (s) | Re-sweep: cases read |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in first:
            lines.append(f"| {r.size:,} | {r.workers} | {fmt(r.wall_seconds)} | {r.emitted:,} | {r.cases_evaluated:,} | "
                         f"{fmt(r.resweep_seconds)} | {r.resweep_evaluated:,} |")
    baseline = load_baseline()
    if baseline and baseline.get("a20"):
        a20 = baseline["a20"]
        lines += ["", "## What changed (A20)", "", a20["description"], "",
                  "| Configuration | " + " | ".join(f"{w} worker{'s' if w != 1 else ''}: sweep (s) / re-sweep (s)" for w in a20["workers"]) + " | Cases read, 100,000 |",
                  "|---|" + "---|" * (len(a20["workers"]) + 1)]
        for row in a20["rows"]:
            cells = [f"{fmt(row['sweep'][str(w)])} / {fmt(row['resweep'][str(w)])}" if str(w) in row["sweep"] else "n/a" for w in a20["workers"]]
            lines.append(f"| {row['label']} | " + " | ".join(cells) + f" | {row['read']:,} |")
        for note in a20.get("notes", []):
            lines += ["", note]
    if baseline:
        lines += ["", "## Optimisation history before A20", "", baseline["description"]]
        for note in baseline.get("notes", []):
            lines += ["", note]
    lines += [
        "",
        "## Method",
        "",
        "- `tools/load/seed_cases.py` creates the throwaway database `closure_load` on the compose Postgres (host port 5433), "
        "applies the engine's migrations through `app.db.run_migrations`, and bulk-inserts cases and their event logs with "
        "`COPY`. Every log is produced by the engine's own state machine and replayed once at seeding time. It refuses to "
        "touch any database not named `closure_load*`; the dev database is never used.",
        "- `tools/load/time_sweep.py` times the sweep with a fixed sweep time (2026-03-10 08:00 UTC, a Tuesday), the real "
        "`PostgresEventStore` and the UAE working-day calendar; with Workers 1 by calling `run_sweep`, with more workers by "
        "calling `POST /scheduler/sweep` on the FastAPI app (`RouteSweep`, `SWEEP_WORKERS=N`, pool started before the clock). Seed 20261017, so the data and the emitted-event counts are "
        "reproducible; wall times vary with the machine. Postgres runs in Docker on the same machine, so every statement pays "
        "a local network hop.",
        "- After each sweep it asserts: emitted events equal the pure planner's expectation; no errors or rejected events; a "
        "second sweep emits nothing; no `close` event exists; case logs are gap-free; the event row count grew by exactly the "
        "events emitted.",
        "- `make check` runs the 1,000-case size only (no file written); `make test-load` runs all sizes and rewrites this file. "
        "The database is dropped at the end unless `KEEP_LOAD_DB=1`.",
        "",
        "## Machine",
        "",
        "| | |",
        "|---|---|",
    ]
    for k, v in spec.items():
        lines.append(f"| {k} | {v} |")
    lines += ["| commit | " + (commit or git_commit()) + " |", ""]
    return "\n".join(lines)


def load_baseline() -> dict | None:
    try:
        return json.loads(BASELINE_PATH.read_text())
    except (OSError, ValueError):
        return None


_SEEDED_OID: list[int | None] = [None]  # pg_database.oid of closure_load as run_size seeded it


def _load_db_oid() -> int | None:
    try:
        return scalar(sc.admin_url(), f"SELECT oid::int FROM pg_database WHERE datname = '{sc.LOAD_DB}'")
    except Exception:  # noqa: BLE001 - the database is gone, or Postgres is
        return None


def run_cell(size: int, scenario: str, profile: bool = False, workers: int = 1, attempts: int = 6) -> SizeResult:
    """`run_size`, rerun from the seed if `closure_load` was dropped (and perhaps recreated) under it. The database has one
    fixed name, so a second run on the same Postgres (another checkout's `make test-tools`, a second `make test-load`)
    drops it with DROP DATABASE ... WITH (FORCE) and the sweep sees "terminating connection due to administrator command".
    That is the harness's neighbour, not a result: the cell is measured again on a fresh seed. Any other failure, or a
    database that is still the one that was seeded, is raised."""
    for attempt in range(1, attempts + 1):
        _SEEDED_OID[0] = None
        try:
            return run_size(size, scenario, profile=profile, workers=workers)
        except Exception as exc:  # noqa: BLE001
            seeded = _SEEDED_OID[0]
            if seeded is not None and _load_db_oid() == seeded:
                raise  # same database as seeded: a real failure
            print(f"   {sc.LOAD_DB} was dropped under the run ({type(exc).__name__}); another process uses it. "
                  f"Seeding again (attempt {attempt} of {attempts}).", flush=True)
            time.sleep(5)
    raise SystemExit(f"{sc.LOAD_DB} kept being dropped by another process; run again when it is quiet")


def report(r: SizeResult) -> None:
    print(f"   workers={r.workers}: sweep {fmt(r.wall_seconds)} s, emitted {r.emitted:,}, {r.rows_per_second:,.0f} cases/s"
          + (f" (read+replay {fmt(r.read_replay_seconds)}, plan {fmt(r.plan_seconds)}, append {fmt(r.append_seconds)})"
             if r.workers == 1 else "")
          + f"; re-sweep {fmt(r.resweep_seconds)} s", flush=True)
    for p in r.problems:
        print(f"   PROBLEM: {p}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", default=",".join(map(str, DEFAULT_SIZES)))
    ap.add_argument("--workers", default=",".join(map(str, DEFAULT_WORKERS)),
                    help="also run the sharded sweep through POST /scheduler/sweep with each of these SWEEP_WORKERS "
                         "values (comma-separated; 0 or 1: single process only)")
    ap.add_argument("--backlog-sizes", default="1000,10000", help="sizes also run in the cold-start scenario (full runs only)")
    ap.add_argument("--unindexed-sizes", default="10000,100000",
                    help="sizes also run as the first sweep after migration 012, `cases.next_due_at` still NULL (full runs only; "
                         "the sharded route only)")
    ap.add_argument("--check", action="store_true", help=f"reduced run for `make check`: {CHECK_SIZE} cases, no files written")
    ap.add_argument("--budget", type=float, default=None, help="--check: fail if the sweep takes longer than this many seconds")
    ap.add_argument("--skip-single", action="store_true", help="do not run the single-process mode (an ablation run: SWEEP_FULL_SCAN=1 / SWEEP_BATCH_CASES=1)")
    ap.add_argument("--profile", action="store_true", help="print a cProfile of each single-process sweep")
    ap.add_argument("--out", default=str(REPO_ROOT / "docs/results/load.md"))
    ap.add_argument("--json-out", default=None, help="default: next to --out as load.json")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--drop", action="store_true", help="drop the load database and exit")
    ap.add_argument("--render-json", default=None, metavar="LOAD_JSON",
                    help="re-render the Markdown from a previous run's load.json (no database, no timing) and exit; "
                         "used after editing tools/load/baseline.json notes")
    args = ap.parse_args(argv)

    if args.render_json:
        data = json.loads(Path(args.render_json).read_text())
        mk = lambda rows: [SizeResult(**{k: v for k, v in r.items()}) for r in rows]  # noqa: E731
        out = Path(args.out)
        out.write_text(render_markdown(mk(data["results"]), data["machine"], mk(data["cold_start"]), data.get("commit"),
                                       mk(data.get("first_sweep", []))), encoding="utf-8")
        print(f"rendered {out} from {args.render_json} (measured at commit {data.get('commit')})")
        return 0

    if args.drop:
        sc.drop_database()
        print(f"dropped {sc.LOAD_DB}")
        return 0
    if not sc.pg_reachable():
        print(f"Postgres is not reachable at {sc.admin_url().rsplit('@', 1)[-1]} (LOAD_PG_ADMIN_URL); "
              "start it with `docker compose up -d postgres`", file=sys.stderr)
        return 2

    sizes = [CHECK_SIZE] if args.check else [int(s) for s in args.sizes.split(",") if s]
    worker_modes = ([] if args.skip_single and not args.check else [1]) + (
        [2] if args.check else [w for w in (int(x) for x in args.workers.split(",") if x) if w > 1])
    results: list[SizeResult] = []
    extra: list[SizeResult] = []
    first: list[SizeResult] = []
    rc = 0
    try:
        for size in sizes:
            for w in worker_modes:
                print(f"== {size:,} open cases (daily) ==", flush=True)
                r = run_cell(size, "daily", profile=args.profile and w == 1, workers=w)
                results.append(r)
                report(r)
                rc |= 1 if r.problems else 0
        if not args.check:
            for size in [int(s) for s in args.backlog_sizes.split(",") if s]:
                for w in worker_modes:
                    print(f"== {size:,} open cases (backlog) ==", flush=True)
                    r = run_cell(size, "backlog", workers=w)
                    extra.append(r)
                    report(r)
                    rc |= 1 if r.problems else 0
            for size in [int(s) for s in args.unindexed_sizes.split(",") if s]:
                for w in (w for w in worker_modes if w > 1):
                    print(f"== {size:,} open cases (first sweep after migration 012) ==", flush=True)
                    r = run_cell(size, "unindexed", workers=w)
                    first.append(r)
                    report(r)
                    rc |= 1 if r.problems else 0
    finally:
        if os.environ.get("KEEP_LOAD_DB") == "1":
            print(f"KEEP_LOAD_DB=1: leaving {sc.LOAD_DB} in place")
        else:
            sc.drop_database()

    if args.check:
        budget = args.budget if args.budget is not None else CHECK_BUDGET_SECONDS
        r = results[0]
        if r.wall_seconds > budget:
            print(f"load-check: FAIL, {r.size:,}-case sweep took {fmt(r.wall_seconds)} s (budget {budget:g} s)")
            rc = 1
        print(f"load-check: {'FAIL' if rc else 'PASS'} ({r.size:,} cases, sweep {fmt(r.wall_seconds)} s of {budget:g} s budget, "
              f"{r.emitted} events emitted as planned, re-sweep emitted {r.resweep_emitted}; sharded sweep (2 workers) agrees)")
        return rc

    spec = machine_spec()
    # The results are written whenever the correctness assertions held, whether or not the 5 s target was met: the file
    # must report a miss as honestly as a hit. The exit code still fails on a miss (below).
    if not args.no_write and rc == 0:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_markdown(results, spec, extra, None, first), encoding="utf-8")
        json_out = Path(args.json_out) if args.json_out else out.with_suffix(".json")
        json_out.write_text(json.dumps({"machine": spec, "commit": git_commit(),
                                        "results": [asdict(r) for r in results],
                                        "cold_start": [asdict(r) for r in extra],
                                        "first_sweep": [asdict(r) for r in first]}, indent=2, sort_keys=True) + "\n")
        print(f"wrote {out} and {json_out}")
    big = [r for r in results if r.size == 100_000]
    for r in big:
        ok = r.wall_seconds <= TARGET_100K_SECONDS
        default = " (the route's default)" if r.workers == ROUTE_DEFAULT_WORKERS else ""
        print(f"target: 100k sweep under {TARGET_100K_SECONDS:g} s, workers={r.workers}{default}: {fmt(r.wall_seconds)} s: {'MET' if ok else 'NOT MET'}")
    # The target is for the real route with its default worker count (A20); the other rows are information.
    gated = [r for r in big if r.workers == ROUTE_DEFAULT_WORKERS] or big
    if gated and not all(r.wall_seconds <= TARGET_100K_SECONDS for r in gated):
        rc = 1
    print(f"test-load: {'FAIL' if rc else 'PASS'}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
