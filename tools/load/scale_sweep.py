#!/usr/bin/env python3
"""Sweep at scale (M7-6): 100k / 500k / 1M open cases on the IN-CLUSTER Postgres of the kind cluster `sc-m76`.

This is tools/load/time_sweep.py's `daily` scenario (same case mixture, same planner expectations, same assertions, same
single-process `run_sweep` and sharded `POST /scheduler/sweep` route) pointed at the kind cluster's Postgres
(127.0.0.1:15476, owner `closure_owner`, password read from the Secret `sc-postgres`; nothing is printed) instead of the
compose one, with three additions:

  * a parallel seeder (several processes, COPY) so 1M cases seed in minutes, followed by VACUUM (ANALYZE) so the table is in
    the state an aged production table is in (hint bits set, visibility map built);
  * the seeded database is kept as a TEMPLATE (`closure_load_tpl_<size>`) and every timed cell starts from a fresh
    `CREATE DATABASE closure_load TEMPLATE ... STRATEGY FILE_COPY`, so each cell sees the same data (a sweep mutates it);
  * evidence beyond the wall time: EXPLAIN (ANALYZE, BUFFERS) of the sweep's due-case selection and of the per-case read,
    pg_stat_user_indexes / pg_stat_user_tables counters before and after the sweep, table and index sizes, a cProfile run.

Only `closure_load*` databases are ever created, dropped or swept (tools/load/seed_cases.assert_throwaway); the cluster's
`closure` database is touched only by the explicit `seed-closure` subcommand, which inserts rows into it and nothing else.

    python tools/load/scale_sweep.py sweep --sizes 100000,500000,1000000 --workers 1,4 [--label before] [--profile]
    python tools/load/scale_sweep.py seed-closure --size 1000000          # the population for the API latency test
    python tools/load/scale_sweep.py drop                                 # drop closure_load and all templates

Results: docs/results/scale/sweep.json (merged by size/workers/label across invocations). Every number in it is measured by
this script in the run that produced it.
"""

from __future__ import annotations

import argparse
import base64
import cProfile
import io
import json
import multiprocessing
import os
import pstats
import random
import re
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import seed_cases as sc  # noqa: E402
import time_sweep as ts  # noqa: E402

import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

from app.db import create_pool, run_migrations  # noqa: E402
from app.events import Event  # noqa: E402
from app.state_machine import replay_model  # noqa: E402
from app.store_pg import PostgresEventStore  # noqa: E402
from app.sweep import NEVER, next_due_at, plan_case, run_sweep, timing_fingerprint  # noqa: E402

ROOT = sc.REPO_ROOT
CTX = "kind-sc-m76"
NS = "closure"
PG_HOSTPORT = "127.0.0.1:15476"
DEFAULT_OUT = ROOT / "docs/results/scale/sweep.json"
TARGET_LOOP_SECONDS = 10.0

METHOD = (
    "Synthetic open cases (the time_sweep.py `daily` mixture: eleven open states, ~40% overdue, previous sweep one working day "
    "before the timed one, cases.next_due_at as the last append left it) are seeded by several processes with COPY into a "
    "template database on the in-cluster Postgres (kind cluster sc-m76, postgres-0, tools/k8s/postgres.yaml), then VACUUM "
    "(ANALYZE)d. Each timed cell recreates `closure_load` from the template (file copy), runs a CHECKPOINT, then times ONE sweep "
    "at the fixed sweep time (2026-03-10 08:00 UTC): workers=1 is app.sweep.run_sweep in one process with a TimedStore that "
    "accumulates the time spent inside the store's read and append calls (planning is wall - read - append); workers=N is "
    "POST /scheduler/sweep on the FastAPI app (in process, TestClient) with SWEEP_WORKERS=N and a warm worker pool, as the "
    "scheduler service triggers it. A second identical sweep (idle re-sweep, must emit nothing) is timed, then an untimed "
    "full-scan sweep (every case read and replayed; timed separately as the no-index cost) must find nothing left to do. "
    "Correctness assertions are those of time_sweep.py (emitted == planner expectation, no errors/rejects, no close event, "
    "gap-free logs, row count grew by exactly the events emitted). The sweep runs on the macOS host (the Python process) and "
    "reaches Postgres through the kind port mapping and the Docker VM, so every statement pays that hop; the API pods in the "
    "cluster reach Postgres over the pod network. The host, the Docker VM (10 CPUs, ~7.7 GiB) and the three kind nodes are "
    "shared by the load generator, Postgres and the cluster's own pods."
)


def kubectl(*args: str, timeout: float = 120) -> str:
    return subprocess.run(["kubectl", "--context", CTX, "-n", NS, *args], capture_output=True, text=True, check=True,
                          timeout=timeout).stdout


def pg_password() -> str:
    return base64.b64decode(kubectl("get", "secret", "sc-postgres", "-o", "jsonpath={.data.password}")).decode()


def configure_admin_url() -> None:
    """Point seed_cases (and the spawned seeding processes, through the environment) at the in-cluster Postgres."""
    os.environ["LOAD_PG_ADMIN_URL"] = f"postgresql://closure_owner:{pg_password()}@{PG_HOSTPORT}/postgres?sslmode=disable"


def closure_url() -> str:
    return sc._swap_db(sc.admin_url(), "closure")


def tpl_name(size: int) -> str:
    return f"closure_load_tpl_{size}"


def connect(url: str, **kw) -> "psycopg.Connection":
    """A harness connection with parallel query off: the Postgres pod's /dev/shm is Docker's default 64 MB, and a parallel plan
    (or parallel VACUUM) over millions of rows asks for a larger shared-memory segment than that ("No space left on device")."""
    conn = psycopg.connect(url, **kw)
    conn.execute("SET max_parallel_workers_per_gather = 0")
    return conn


def admin(statement: str, *params, db: str = "postgres"):
    with psycopg.connect(sc._swap_db(sc.admin_url(), db), autocommit=True) as conn:
        cur = conn.execute(statement, params or None)
        return cur.fetchall() if cur.description else None


def one(url: str, statement: str, *params):
    with connect(url) as conn:
        row = conn.execute(statement, params or None).fetchone()
        return row[0] if row else None


# ------------------------------------------------------------------------------------------------------------ seeding


def _wall_safe_enter_time(rng, calendar, pathway, state: str, phase: str) -> datetime:
    """seed_cases._final_state_enter_time with one change: a `healthy` case entered its state at most its SLA ago. The original draws
    the due date 0.2-20 days AHEAD of the sweep time whatever the SLA, which puts the state-entry event up to 13 days AFTER the
    sweep time (harmless for time_sweep.py's fixed simulated sweep time). In a wall-clock deployment such an event makes the API
    refuse to start (CLOCK_MODE=wall: an event dated after now), so a population seeded into `closure` must not contain one."""
    sla = pathway.sla_for_state(state)
    if sla is None or phase != "healthy":
        return _ORIGINAL_ENTER_TIME(rng, calendar, pathway, state, phase)
    days = sla[1]
    target_due = sc.SWEEP_NOW + timedelta(days=rng.uniform(0.2, max(days * 7 / 5 - 0.6, 0.3)))
    return sc._enter_time_for_due(calendar, days, target_due)


_ORIGINAL_ENTER_TIME = sc._final_state_enter_time


def _seed_chunk(args: tuple) -> dict:
    """One seeding process: `n` cases with their own rng stream, COPYed in batches. The per-case logic is
    tools/load/seed_cases.seed() verbatim; only `now` (the timed sweep's instant) is a parameter."""
    url, n, seed_value, scenario, now_iso, batch, weights, wall_safe = args
    if wall_safe:
        sc._final_state_enter_time = _wall_safe_enter_time
    if weights:
        sc.STATE_WEIGHTS = dict(weights)  # build_case_events draws the state from this module global
    now = datetime.fromisoformat(now_iso)
    sc.SWEEP_NOW, sc.PREV_SWEEP = now, now - timedelta(days=1)  # the module's helpers read these globals
    pathway, calendar = sc.load_engine()
    basis = timing_fingerprint(pathway, calendar)
    rng = random.Random(seed_value)
    out = {"cases": 0, "events": 0, "pre_swept": 0, "mark_overdue": 0, "escalate": 0, "by_state": {}}
    cases_rows: list[tuple] = []
    event_rows: list[tuple] = []
    with psycopg.connect(url) as conn:
        def flush() -> None:
            if not cases_rows:
                return
            with conn.cursor() as cur:
                with cur.copy("COPY cases (id, pathway, current_state, created_at, updated_at, next_due_at, next_due_basis) FROM STDIN") as cp:
                    for row in cases_rows:
                        cp.write_row(row)
                with cur.copy("COPY events (event_id, case_id, seq, type, at, actor, payload) FROM STDIN") as cp:
                    for row in event_rows:
                        cp.write_row(row)
            conn.commit()
            cases_rows.clear()
            event_rows.clear()

        for _ in range(n):
            case_id, events = sc.build_case_events(rng, pathway, calendar)
            model = replay_model(events, pathway)
            for ev in plan_case(case_id, model, pathway, calendar, sc.PREV_SWEEP):
                events.append(Event(case_id, ev.type, sc.PREV_SWEEP, ev.actor, ev.payload, ev.event_id))
                out["pre_swept"] += 1
            model = replay_model(events, pathway)
            for ev in plan_case(case_id, model, pathway, calendar, now):
                out["mark_overdue" if ev.type == "mark_overdue" else "escalate"] += 1
            if wall_safe and events[-1].at > now:
                raise AssertionError(f"seeded event dated after now: {events[-1].at} > {now}")
            due = next_due_at(model, pathway, calendar)
            cases_rows.append((case_id, pathway.name, model.state, events[0].at, events[-1].at, NEVER if due is None else due, basis))
            for seq, ev in enumerate(events, start=1):
                event_rows.append((ev.event_id, case_id, seq, ev.type, ev.at, ev.actor, Jsonb(ev.payload)))
            out["by_state"][model.state] = out["by_state"].get(model.state, 0) + 1
            out["cases"] += 1
            out["events"] += len(events)
            if len(cases_rows) >= batch:
                flush()
        flush()
    return out


def seed_database(name: str, size: int, now: datetime, *, procs: int, recreate: bool, base_seed: int, scenario: str = "daily",
                  chunk_cases: int = 20_000, batch: int = 5_000, weights: dict | None = None, wall_safe: bool = False, log=print) -> dict:
    """Seed `size` open cases into database `name` (created and migrated first when `recreate`). Returns the stats."""
    pathway, calendar = sc.load_engine()
    url = sc._swap_db(sc.admin_url(), name)
    t0 = time.perf_counter()
    if recreate:
        sc.assert_throwaway(name)
        admin(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)).as_string())
        admin(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)).as_string())
        pool = create_pool(url, max_size=2)
        try:
            run_migrations(pool)
        finally:
            pool.close()
    t_migrated = time.perf_counter()
    chunks = []
    left, i = size, 0
    while left > 0:
        n = min(chunk_cases, left)
        chunks.append((url, n, base_seed + i, scenario, now.isoformat(), batch, weights, wall_safe))
        left -= n
        i += 1
    agg = {"cases": 0, "events": 0, "pre_swept": 0, "mark_overdue": 0, "escalate": 0, "by_state": {}}
    ctx = multiprocessing.get_context("spawn")
    done = 0
    with ctx.Pool(procs) as pool:
        for part in pool.imap_unordered(_seed_chunk, chunks):
            for k in ("cases", "events", "pre_swept", "mark_overdue", "escalate"):
                agg[k] += part[k]
            for s, c in part["by_state"].items():
                agg["by_state"][s] = agg["by_state"].get(s, 0) + c
            done += part["cases"]
            log(f"   seeded {done:,}/{size:,} ({time.perf_counter() - t0:.0f}s)", flush=True)
    t_inserted = time.perf_counter()
    with connect(url, autocommit=True) as conn:
        conn.execute("VACUUM (ANALYZE, PARALLEL 0) cases")
        conn.execute("VACUUM (ANALYZE, PARALLEL 0) events")
    t_done = time.perf_counter()
    agg.update(size=size, scenario=scenario, processes=procs, base_seed=base_seed, database=name,
               migrate_seconds=t_migrated - t0, insert_seconds=t_inserted - t_migrated, vacuum_analyze_seconds=t_done - t_inserted,
               total_seconds=t_done - t0, cases_per_second=size / (t_inserted - t_migrated),
               now=now.isoformat(), expected_emitted=agg["mark_overdue"] + agg["escalate"])
    return agg


# ------------------------------------------------------------------------------------------------------------ evidence

SIZE_SQL = """
SELECT c.relname, c.relkind, pg_relation_size(c.oid) AS rel_bytes, pg_total_relation_size(c.oid) AS total_bytes,
       c.reltuples::bigint AS est_rows, COALESCE(i.indrelid::regclass::text, '') AS on_table
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  LEFT JOIN pg_index i ON i.indexrelid = c.oid
 WHERE n.nspname = 'public' AND c.relkind IN ('r','i','t')
   AND (c.relname IN ('cases','events') OR i.indrelid IN ('cases'::regclass, 'events'::regclass)
        OR c.oid IN (SELECT reltoastrelid FROM pg_class WHERE relname IN ('cases','events')))
 ORDER BY c.relkind, c.relname
"""


def db_sizes(url: str, dbname: str) -> dict:
    with connect(url) as conn:
        total = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
        rows = conn.execute(SIZE_SQL).fetchall()
        counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("cases", "events")}
    rel = [{"name": r[0], "kind": {"r": "table", "i": "index", "t": "toast"}[r[1]], "relation_bytes": r[2],
            "total_bytes_incl_indexes_toast": r[3], "on_table": r[5]} for r in rows]
    tables = {}
    for t in ("cases", "events"):
        heap = next(r for r in rel if r["name"] == t)
        idx = [r for r in rel if r["kind"] == "index" and r["on_table"] == t]
        tables[t] = {"rows": counts[t], "heap_bytes": heap["relation_bytes"], "total_bytes": heap["total_bytes_incl_indexes_toast"],
                     "index_bytes": sum(r["relation_bytes"] for r in idx),
                     "heap_bytes_per_row": heap["relation_bytes"] / max(counts[t], 1),
                     "total_bytes_per_row": heap["total_bytes_incl_indexes_toast"] / max(counts[t], 1)}
    return {"database": dbname, "database_bytes": total, "tables": tables, "relations": rel}


def index_stats(url: str) -> dict:
    with psycopg.connect(url) as conn:
        idx = conn.execute("SELECT relname, indexrelname, idx_scan, idx_tup_read, idx_tup_fetch FROM pg_stat_user_indexes "
                           "WHERE relname IN ('cases','events') ORDER BY relname, indexrelname").fetchall()
        tab = conn.execute("SELECT relname, seq_scan, seq_tup_read, idx_scan, n_tup_ins, n_tup_upd, n_tup_hot_upd, n_dead_tup "
                           "FROM pg_stat_user_tables WHERE relname IN ('cases','events') ORDER BY relname").fetchall()
    return {"indexes": {f"{r[0]}.{r[1]}": {"idx_scan": r[2], "idx_tup_read": r[3], "idx_tup_fetch": r[4]} for r in idx},
            "tables": {r[0]: {"seq_scan": r[1], "seq_tup_read": r[2], "idx_scan": r[3], "n_tup_ins": r[4], "n_tup_upd": r[5],
                              "n_tup_hot_upd": r[6], "n_dead_tup": r[7]} for r in tab}}


def explain(url: str, statement: str, params: list) -> list[str]:
    with connect(url) as conn:
        rows = conn.execute("EXPLAIN (ANALYZE, BUFFERS, SETTINGS) " + statement, params).fetchall()
    # the 500-uuid array literal in the plan text is noise: keep its length, not its content
    return [re.sub(r"'\{[0-9a-f,-]{200,}\}'::uuid\[\]", "'{<uuid list>}'::uuid[]", r[0]) for r in rows]


def explain_queries(url: str, pathway, calendar, now: datetime) -> dict:
    """The sweep's due-case selection (api/app/store_pg.py `iter_due_case_models`) and its per-chunk read (`_read_chunk`), plus
    a single-case read, with the SQL text copied from the store and the parameters the sweep passes."""
    basis = timing_fingerprint(pathway, calendar)
    due_sql = ("SELECT id, (next_due_at IS NULL OR next_due_basis IS DISTINCT FROM %s) FROM cases"
               " WHERE pathway = %s AND current_state <> %s"
               " AND (next_due_at IS NULL OR next_due_basis IS DISTINCT FROM %s OR next_due_at <= %s)")
    params = [basis, pathway.name, pathway.terminal_state, basis, now]
    shard_sql = " AND mod(hashtext(id::text)::bigint & 2147483647, %s) = %s"
    with connect(url) as conn:
        ids = [r[0] for r in conn.execute(due_sql + " ORDER BY id LIMIT 500", params).fetchall()]
        n_due = conn.execute("SELECT count(*) FROM (" + due_sql + ") t", params).fetchone()[0]
    out = {
        "due_selection_rows": n_due,
        "due_selection_single_process": explain(url, due_sql + " ORDER BY id", params),
        "due_selection_shard_0_of_4": explain(url, due_sql + shard_sql + " ORDER BY id", params + [4, 0]),
    }
    cols = "event_id, seq, type, at, actor, payload"
    if ids:
        out["per_chunk_read_500_cases"] = explain(
            url, f"SELECT case_id, {cols} FROM events WHERE case_id = ANY(%s) ORDER BY case_id, seq", [ids])
        out["single_case_read"] = explain(url, f"SELECT {cols} FROM events WHERE case_id = %s ORDER BY seq", [ids[0]])
    # What the due-time index alone would do: the predicate without the basis clause (the clause that is not indexable).
    out["due_predicate_index_only_for_comparison"] = explain(
        url, "SELECT id FROM cases WHERE pathway = %s AND current_state <> %s AND next_due_at <= %s ORDER BY id",
        [pathway.name, pathway.terminal_state, now])
    return out


def settings(url: str) -> dict:
    names = ("shared_buffers", "work_mem", "effective_cache_size", "max_wal_size", "checkpoint_timeout", "synchronous_commit",
             "max_connections", "maintenance_work_mem", "wal_buffers", "random_page_cost", "effective_io_concurrency", "fsync")
    with psycopg.connect(url) as conn:
        return {n: conn.execute(f"SHOW {n}").fetchone()[0] for n in names} | {"version": conn.execute("SELECT version()").fetchone()[0]}


# ------------------------------------------------------------------------------------------------------------ one cell


def make_load_from_template(size: int) -> float:
    t = time.perf_counter()
    sc.assert_throwaway(sc.LOAD_DB)
    admin(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(sc.LOAD_DB)).as_string())
    admin(sql.SQL("CREATE DATABASE {} TEMPLATE {} STRATEGY FILE_COPY").format(
        sql.Identifier(sc.LOAD_DB), sql.Identifier(tpl_name(size))).as_string())
    return time.perf_counter() - t


def run_cell(size: int, workers: int, tpl_stats: dict, label: str, profile: bool = False, evidence: bool = False,
             full_check: bool = True, drop_dup_index: bool = False, steady: int = 0, log=print) -> dict:
    now = datetime.fromisoformat(tpl_stats["now"])
    assert now == sc.SWEEP_NOW, "templates are seeded at the fixed sweep time"
    pathway, calendar = sc.load_engine()
    clone_seconds = make_load_from_template(size)
    url = sc.load_url()
    if drop_dup_index:
        # EXPERIMENT (not a change to the product): events_case_id_seq_idx duplicates the UNIQUE (case_id, seq) constraint's index
        # (events_case_id_seq_key). Dropping it here shows what a migration that removes it would save; api/ is not touched.
        with connect(url, autocommit=True) as conn:
            conn.execute("DROP INDEX events_case_id_seq_idx")
    admin("CHECKPOINT")
    pool = create_pool(url, max_size=4)
    problems: list[str] = []
    cell: dict = {"size": size, "workers": workers, "label": label, "profiled": profile, "dropped_duplicate_index_events_case_id_seq_idx": drop_dup_index,
                  "postgres_settings": settings(sc.admin_url()), "clone_from_template_seconds": clone_seconds,
                  "started_at": datetime.now(UTC).isoformat()}
    try:
        events_before = one(url, "SELECT count(*) FROM events")
        if evidence:
            cell["sizes_before"] = db_sizes(url, sc.LOAD_DB)
            cell["explain_before_sweep"] = explain_queries(url, pathway, calendar, now)
        stats_before = index_stats(url)
        store = ts.TimedStore(PostgresEventStore(pool))
        route = ts.RouteSweep(url, pathway, pool, workers) if workers > 1 else None
        profiler = cProfile.Profile() if profile else None
        t0 = time.perf_counter()
        if profiler:
            profiler.enable()
        result = route.sweep() if route else run_sweep(store, pathway, calendar, now)
        if profiler:
            profiler.disable()
        wall = time.perf_counter() - t0
        time.sleep(2.0)  # backend statistics are flushed when the connection goes idle
        stats_after_sweep = index_stats(url)
        t1 = time.perf_counter()
        again = route.sweep() if route else run_sweep(PostgresEventStore(pool), pathway, calendar, now)
        resweep = time.perf_counter() - t1
        time.sleep(2.0)
        stats_after_resweep = index_stats(url)
        full_seconds = None
        full = None
        if full_check:
            t2 = time.perf_counter()
            full = run_sweep(PostgresEventStore(pool), pathway, calendar, now, full_scan=True)
            full_seconds = time.perf_counter() - t2
        emitted = result.mark_overdue + result.escalate + result.handoff_bounced
        events_after = one(url, "SELECT count(*) FROM events")
        if result.cases_evaluated + result.cases_skipped != tpl_stats["cases"]:
            problems.append(f"evaluated {result.cases_evaluated} + skipped {result.cases_skipped}, seeded {tpl_stats['cases']}")
        if full is not None:
            if full.cases_evaluated != tpl_stats["cases"]:
                problems.append(f"full scan read {full.cases_evaluated}, seeded {tpl_stats['cases']}")
            if full.mark_overdue + full.escalate + full.handoff_bounced or full.errors or full.rejected:
                problems.append("a full scan after the indexed sweep still found work (the due-time index missed a case)")
        if emitted != tpl_stats["expected_emitted"]:
            problems.append(f"emitted {emitted}, planner expected {tpl_stats['expected_emitted']}")
        if result.errors or result.rejected:
            problems.append(f"{len(result.errors)} errors, {result.rejected} rejected: {result.errors[:3]}")
        if again.mark_overdue + again.escalate + again.handoff_bounced:
            problems.append("second sweep emitted events (not idempotent)")
        if events_after != events_before + emitted:
            problems.append(f"event rows {events_before} -> {events_after}, expected +{emitted}")
        if one(url, "SELECT count(*) FROM events WHERE type = 'close'"):
            problems.append("a close event exists")
        gaps = one(url, "SELECT count(*) FROM (SELECT case_id FROM events GROUP BY case_id HAVING max(seq) <> count(*)) t")
        if gaps:
            problems.append(f"{gaps} case logs have a gap in seq")
        steady_runs = []
        if steady and not problems and not route:  # single process only
            # The steady state of the 10-second loop: sweeps 10 s, 20 s, ... after the daily sweep, i.e. only what became due in each
            # 10-second window. Run after every correctness check above (they emit events, so the checks must not see them).
            for k in range(1, steady + 1):
                at = now + timedelta(seconds=10 * k)
                ts0 = time.perf_counter()
                r = run_sweep(PostgresEventStore(pool), pathway, calendar, at)
                steady_runs.append({"sweep_time": at.isoformat(), "wall_seconds": time.perf_counter() - ts0, "cases_read": r.cases_evaluated,
                                    "emitted": r.mark_overdue + r.escalate + r.handoff_bounced})
        if route:
            route.close()
        cell.update(steady_state_sweeps=steady_runs,
            wall_seconds=wall, emitted=emitted, mark_overdue=result.mark_overdue, escalate=result.escalate,
            handoff_bounced=result.handoff_bounced, expected_emitted=tpl_stats["expected_emitted"],
            cases_read=result.cases_evaluated, cases_skipped=result.cases_skipped, events_before=events_before,
            events_after=events_after, resweep_seconds=resweep, resweep_cases_read=again.cases_evaluated,
            resweep_emitted=again.mark_overdue + again.escalate + again.handoff_bounced,
            full_scan_seconds=full_seconds, full_scan_cases_read=full.cases_evaluated if full else None,
            cases_per_second=result.cases_evaluated / wall if wall else 0.0,
            index_stats_before=stats_before, index_stats_after_sweep=stats_after_sweep,
            index_stats_after_resweep=stats_after_resweep, problems=problems, finished_at=datetime.now(UTC).isoformat(),
        )
        if workers == 1:
            cell["phases"] = {"read_replay_seconds": store.read_seconds, "append_seconds": store.append_seconds,
                              "planning_seconds": max(wall - store.read_seconds - store.append_seconds, 0.0),
                              "append_calls": store.appends,
                              "note": "read_replay = time inside the store's iter_due_case_models generator (selection query, event "
                                      "reads, replay, next_due write-back); planning = wall - read - append (plan_case, payload checks)"}
        if evidence:
            cell["sizes_after"] = db_sizes(url, sc.LOAD_DB)
        if profiler:
            buf = io.StringIO()
            pstats.Stats(profiler, stream=buf).sort_stats("cumulative").print_stats(30)
            buf2 = io.StringIO()
            pstats.Stats(profiler, stream=buf2).sort_stats("tottime").print_stats(20)
            cell["cprofile_cumulative_top30"] = buf.getvalue()
            cell["cprofile_tottime_top20"] = buf2.getvalue()
        return cell
    finally:
        pool.close()


# ------------------------------------------------------------------------------------------------------------ driver


def git_info() -> dict:
    def g(*a: str) -> str:
        return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True).stdout.strip()
    return {"commit": g("rev-parse", "HEAD"), "branch": g("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty_files": g("status", "--porcelain").splitlines()[:30]}


def machine_spec() -> dict:
    return json.loads(subprocess.run(["bash", str(ROOT / "tools/k8s/machine_spec.sh")], capture_output=True, text=True, check=True).stdout)


def chart_values() -> dict:
    vals = json.loads(subprocess.run(["helm", "--kube-context", CTX, "-n", NS, "get", "values", "closure", "--all", "-o", "json"],
                                     capture_output=True, text=True, check=True).stdout)
    keep = ("images", "api", "scheduler", "watchdog", "app")
    return {k: vals[k] for k in keep if k in vals}


def postgres_pod_spec() -> dict:
    sts = json.loads(kubectl("get", "statefulset", "postgres", "-o", "json"))
    c = sts["spec"]["template"]["spec"]["containers"][0]
    return {"image": c["image"], "args": c.get("args"), "resources": c.get("resources"),
            "pvc": sts["spec"]["volumeClaimTemplates"][0]["spec"]}


def load_doc(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"cells": [], "seeding": {}}


def save_doc(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, default=str) + "\n")


def cmd_sweep(args) -> int:
    out = Path(args.out)
    doc = load_doc(out)
    doc.update(schema_version=1, tool="tools/load/scale_sweep.py", method=METHOD, git=git_info(), machine_spec=machine_spec(),
               chart_values=chart_values(), postgres_pod=postgres_pod_spec(), generated_at=datetime.now(UTC).isoformat(),
               target_loop_interval_seconds=TARGET_LOOP_SECONDS)
    doc["postgres_settings"] = settings(sc.admin_url())
    doc.setdefault("started_at", datetime.now(UTC).isoformat())
    rc = 0
    sizes = [int(s) for s in args.sizes.split(",") if s]
    workers_list = [int(w) for w in args.workers.split(",") if w]
    for size in sizes:
        tpl = tpl_name(size)
        exists = admin("SELECT 1 FROM pg_database WHERE datname = %s", tpl)
        meta = None
        if exists and not args.reseed:
            try:
                meta = one(sc._swap_db(sc.admin_url(), tpl), "SELECT v FROM scale_meta WHERE k = 'seed'")
            except psycopg.Error:
                meta = None
        if meta is None:
            print(f"== seeding {size:,} cases into template {tpl} ({args.procs} processes) ==", flush=True)
            meta = seed_database(tpl, size, sc.SWEEP_NOW, procs=args.procs, recreate=True, base_seed=sc.DEFAULT_SEED)
            with psycopg.connect(sc._swap_db(sc.admin_url(), tpl), autocommit=True) as conn:
                conn.execute("CREATE TABLE scale_meta (k text PRIMARY KEY, v jsonb)")
                conn.execute("INSERT INTO scale_meta VALUES ('seed', %s)", (Jsonb(meta),))
            print(f"   seeded in {meta['total_seconds']:.0f}s ({meta['cases_per_second']:,.0f} cases/s COPY phase, "
                  f"{meta['vacuum_analyze_seconds']:.0f}s VACUUM ANALYZE); the sweep should emit {meta['expected_emitted']:,}", flush=True)
            doc.setdefault("seeding", {})[str(size)] = meta
            doc["seeding_sizes_template"] = {str(size): db_sizes(sc._swap_db(sc.admin_url(), tpl), tpl)}
            save_doc(out, doc)
        else:
            doc.setdefault("seeding", {}).setdefault(str(size), meta)
        for w in workers_list:
            print(f"== {size:,} open cases, workers={w}, label={args.label} ==", flush=True)
            cell = run_cell(size, w, meta, args.label, evidence=(w == 1 and not args.no_evidence), full_check=not args.no_full_scan,
                            drop_dup_index=args.drop_dup_index, steady=args.steady)
            key = lambda c: (c["size"], c["workers"], c["label"], c.get("profiled", False))  # noqa: E731
            doc["cells"] = [c for c in doc["cells"] if key(c) != key(cell)] + [cell]
            doc["cells"].sort(key=key)
            save_doc(out, doc)
            ph = cell.get("phases")
            print(f"   sweep {cell['wall_seconds']:.2f}s, emitted {cell['emitted']:,}, read {cell['cases_read']:,}, skipped {cell['cases_skipped']:,}; "
                  f"re-sweep {cell['resweep_seconds']:.2f}s; full scan {cell['full_scan_seconds']}"
                  + (f"; read {ph['read_replay_seconds']:.2f} plan {ph['planning_seconds']:.2f} append {ph['append_seconds']:.2f}" if ph else ""), flush=True)
            for p in cell["problems"]:
                print("   PROBLEM:", p)
                rc = 1
        if args.profile and size == sizes[-1]:
            print(f"== {size:,} open cases, workers=1, cProfile ==", flush=True)
            cell = run_cell(size, 1, meta, args.label, profile=True, full_check=False)
            doc["cells"] = [c for c in doc["cells"] if (c["size"], c["workers"], c["label"], c.get("profiled")) != (size, 1, args.label, True)] + [cell]
            save_doc(out, doc)
            print(cell["cprofile_cumulative_top30"][:3000])
        if not args.keep_templates:
            admin(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(tpl)).as_string())
    doc["finished_at"] = datetime.now(UTC).isoformat()
    save_doc(out, doc)
    if not args.keep_load_db:
        sc.drop_database()
    print(f"wrote {out}")
    return rc


def cmd_seed_closure(args) -> int:
    """Insert synthetic open cases into the cluster's `closure` database (the population for the API latency test and the soak)."""
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    before = {t: one(closure_url(), f"SELECT count(*) FROM {t}") for t in ("cases", "events")}
    print(f"closure before: {before}; seeding {args.size:,} cases with now={now.isoformat()}", flush=True)
    # A seed distinct from the template seeds: case ids come from the rng stream and must not collide with an earlier population.
    # Only states that NO scheduler scan touches (intake, re-route, outreach rules and the evidence scan act on result_received,
    # acknowledged, referral_rejected, owner_accepted, patient_contacted, confirmatory_*, clinician_reviewed): the in-cluster
    # scheduler must sweep this population (SLA breaches and ladder rungs) without the automation drafting outreach for a million
    # cases or looking up evidence in a FHIR server that does not exist. lost_contact is seeded separately and small (bounded
    # worklist for the latency test).
    weights = {"patient_acknowledged": 0.85, "consent_withdrawn": 0.15}
    stats = seed_database("closure", args.size, now, procs=args.procs, recreate=False, base_seed=args.seed, weights=weights, wall_safe=True)
    stats["state_weights"] = weights
    if args.worklist_cases:
        extra = seed_database("closure", args.worklist_cases, now, procs=1, recreate=False, base_seed=args.seed + 900_000,
                              weights={"lost_contact": 1.0}, wall_safe=True)
        stats["worklist_seed"] = {k: extra[k] for k in ("cases", "events", "by_state", "total_seconds")}
    after = {t: one(closure_url(), f"SELECT count(*) FROM {t}") for t in ("cases", "events")}
    stats.update(count_before=before, count_after=after)
    out = Path(args.out)
    doc = load_doc(out)
    doc.setdefault("closure_population", []).append(stats | {"sizes": db_sizes(closure_url(), "closure")})
    save_doc(out, doc)
    print(f"closure after: {after}; {stats['total_seconds']:.0f}s total; wrote {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sweep")
    p.add_argument("--sizes", default="100000,500000,1000000")
    p.add_argument("--workers", default="1,4")
    p.add_argument("--label", default="baseline", help="free text kept in the results (e.g. before / after a change)")
    p.add_argument("--procs", type=int, default=4, help="seeding processes")
    p.add_argument("--profile", action="store_true", help="also run one cProfile cell (workers=1) at the largest size")
    p.add_argument("--reseed", action="store_true")
    p.add_argument("--keep-templates", action="store_true")
    p.add_argument("--keep-load-db", action="store_true")
    p.add_argument("--no-evidence", action="store_true", help="skip EXPLAIN and size queries")
    p.add_argument("--no-full-scan", action="store_true")
    p.add_argument("--steady", type=int, default=0, help="after the checks, run this many 10-second-increment sweeps (the loop's steady state)")
    p.add_argument("--drop-dup-index", action="store_true", help="experiment: drop the duplicate events_case_id_seq_idx in the cloned load database")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    s = sub.add_parser("seed-closure")
    s.add_argument("--size", type=int, required=True)
    s.add_argument("--procs", type=int, default=4)
    s.add_argument("--seed", type=int, default=7_600_001)
    s.add_argument("--worklist-cases", type=int, default=300, help="extra lost_contact cases (the bounded worklist)")
    s.add_argument("--out", default=str(DEFAULT_OUT))
    sub.add_parser("drop")
    args = ap.parse_args(argv)
    configure_admin_url()
    if args.cmd == "drop":
        for (n,) in admin("SELECT datname FROM pg_database WHERE datname LIKE 'closure\\_load%'"):
            sc.assert_throwaway(n)
            admin(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(n)).as_string())
            print("dropped", n)
        return 0
    return cmd_sweep(args) if args.cmd == "sweep" else cmd_seed_closure(args)


if __name__ == "__main__":
    sys.exit(main())
