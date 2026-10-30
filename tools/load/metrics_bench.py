#!/usr/bin/env python3
"""M7-8: what the closure and effort metrics routes cost, the replay they used to do against the table they read now (ADR-049).

Run against a Postgres database holding a seeded population (the cluster's `closure` through the kind port mapping, or a scratch
database): 

  * `legacy` (only up to --legacy-max cases, the replay of everything holds every event in memory): wall time and the process's peak
    RSS of `legacy_closure` + `legacy_effort`, which is what each route call did;
  * `refresh`: the table is brought up to date with calls of --budget seconds each, as the routes do, until it has caught up. Per call:
    wall time, cases replayed, the lag still ahead of the cursor; the total, and the process's peak RSS;
  * `steady`: the two aggregates against the up-to-date table, N calls each: wall time per call;
  * `incremental`: --touch cases get one more event, then one refresh: cases replayed and the time;
  * `equal`: at the size the legacy replay was run, the two answers are compared (exact);

and writes the figures, the machine and the commit to --out (JSON). Each phase runs in a fresh process so its peak RSS is its own.

    python tools/load/metrics_bench.py --dsn postgresql://... --out docs/results/scale/m78/metrics_bench.json [--legacy-max 100000]
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import platform
import resource
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))


def peak_rss_mib() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(r / (2**20 if sys.platform == "darwin" else 2**10), 1)


def _env():
    from app.calendar import default_calendar
    from app.config import settings
    from app.db import create_pool
    from app.pathway import load_pathway
    from app.store_pg import PostgresEventStore

    return create_pool, PostgresEventStore, load_pathway(settings.pathway_path), default_calendar()


def phase_legacy(dsn: str, now_iso: str, limit: int, q) -> None:
    from app import metrics_index

    create_pool, Store, pathway, cal = _env()
    store = Store(create_pool(dsn, max_size=2))
    now = datetime.fromisoformat(now_iso)
    n = store.state_counts(pathway)
    total = sum(n.values())
    if total > limit:
        q.put({"skipped": f"{total:,} cases is over --legacy-max {limit:,}"})
        return
    t = time.perf_counter()
    body, failed = metrics_index.legacy_closure(store, pathway, now, cal)
    t_closure = time.perf_counter() - t
    rss_closure = peak_rss_mib()
    t = time.perf_counter()
    eff = metrics_index.legacy_effort(store, pathway, 500)
    t_effort = time.perf_counter() - t
    q.put({"cases": total, "closure_seconds": round(t_closure, 2), "closure_peak_rss_mib": rss_closure,
           "effort_seconds": round(t_effort, 2), "effort_peak_rss_mib": peak_rss_mib(), "replay_failures": len(failed),
           "closure_cases_total": body["cases_total"], "effort_cases": eff["cases"]})


def phase_refresh(dsn: str, budget: float, q) -> None:
    from app import metrics_index

    create_pool, Store, pathway, cal = _env()
    store = Store(create_pool(dsn, max_size=2))
    calls, t_all = [], time.perf_counter()
    while True:
        t = time.perf_counter()
        r = metrics_index.refresh(store, pathway, cal, budget_seconds=budget)
        calls.append({"seconds": round(time.perf_counter() - t, 2), "replayed": r["replayed"], "batches": r["batches"], "lag": r["lag"],
                      "caught_up": r["caught_up"], "busy": r["busy"]})
        if r["caught_up"] or r["busy"] or len(calls) > 100000:
            break
    total = time.perf_counter() - t_all
    with store._pool.connection() as conn:
        rows = conn.execute("SELECT (SELECT count(*) FROM case_metrics), (SELECT count(*) FROM case_metric_dwells),"
                            " pg_total_relation_size('case_metrics'), pg_total_relation_size('case_metric_dwells')").fetchone()
    secs = sorted(c["seconds"] for c in calls)
    q.put({"budget_seconds": budget, "calls": len(calls), "total_seconds": round(total, 1), "replayed_total": sum(c["replayed"] for c in calls),
           "call_seconds": {"median": statistics.median(secs), "p95": secs[int(0.95 * (len(secs) - 1))], "max": secs[-1]},
           "first_call": calls[0], "last_call": calls[-1], "case_metrics_rows": rows[0], "case_metric_dwells_rows": rows[1],
           "case_metrics_bytes": rows[2], "case_metric_dwells_bytes": rows[3], "peak_rss_mib": peak_rss_mib(),
           "replay_rate_cases_per_s": round(sum(c["replayed"] for c in calls) / total, 0) if total else None})


def phase_steady(dsn: str, now_iso: str, n: int, q) -> None:
    from app import metrics_index

    create_pool, Store, pathway, cal = _env()
    store = Store(create_pool(dsn, max_size=2))
    now = datetime.fromisoformat(now_iso)
    out = {}
    for name, fn in (("closure_aggregate", lambda: metrics_index.closure_metrics(store, now, cal, pathway.terminal_state, timeout_ms=600000)),
                     ("effort_aggregate", lambda: metrics_index.effort_report(store, 500, timeout_ms=600000)),
                     ("refresh_when_current", lambda: metrics_index.refresh(store, pathway, cal, budget_seconds=60))):
        ts = []
        for _ in range(n):
            t = time.perf_counter()
            fn()
            ts.append(time.perf_counter() - t)
        ts.sort()
        out[name] = {"calls": n, "median_s": round(statistics.median(ts), 3), "max_s": round(ts[-1], 3), "min_s": round(ts[0], 3)}
    body = metrics_index.closure_metrics(store, now, cal, pathway.terminal_state, timeout_ms=600000)
    out["cases_total"] = body["cases_total"]
    out["peak_rss_mib"] = peak_rss_mib()
    q.put(out)


def phase_incremental(dsn: str, touch: int, q) -> None:
    from datetime import timedelta
    from uuid import uuid4

    from app import metrics_index
    from app.events import Event

    create_pool, Store, pathway, cal = _env()
    store = Store(create_pool(dsn, max_size=2))
    with store._pool.connection() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM cases WHERE current_state = 'patient_acknowledged' ORDER BY random() LIMIT %s", (touch,)).fetchall()]
    t = time.perf_counter()
    for cid in ids:
        events = store.events_for(cid)
        store.append(Event(cid, "book_confirmatory", events[-1].at + timedelta(minutes=1), "specialist_owner:bench", {}, uuid4()), pathway)
    t_append = time.perf_counter() - t
    t = time.perf_counter()
    r = metrics_index.refresh(store, pathway, cal, budget_seconds=120)
    q.put({"touched": len(ids), "append_seconds": round(t_append, 2), "refresh_seconds": round(time.perf_counter() - t, 2),
           "replayed": r["replayed"], "caught_up": r["caught_up"], "peak_rss_mib": peak_rss_mib()})


def phase_equal(dsn: str, now_iso: str, limit: int, q) -> None:
    from app import metrics_index

    create_pool, Store, pathway, cal = _env()
    store = Store(create_pool(dsn, max_size=2))
    total = sum(store.state_counts(pathway).values())
    if total > limit:
        q.put({"skipped": f"{total:,} cases is over --legacy-max {limit:,}"})
        return
    now = datetime.fromisoformat(now_iso)
    old, failed = metrics_index.legacy_closure(store, pathway, now, cal)
    new = metrics_index.closure_metrics(store, now, cal, pathway.terminal_state, timeout_ms=600000)
    eo, en = metrics_index.legacy_effort(store, pathway, 500), metrics_index.effort_report(store, 500, timeout_ms=600000)
    q.put({"cases": total, "closure_equal": old == new, "effort_equal": eo == en, "replay_failures": len(failed)})


def run(fn, *args) -> dict:
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=fn, args=(*args, q))
    p.start()
    out = q.get()
    p.join()
    return out


def git() -> dict:
    def g(*a):
        return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True).stdout.strip()
    return {"commit": g("rev-parse", "HEAD"), "branch": g("rev-parse", "--abbrev-ref", "HEAD"), "dirty_files": g("status", "--porcelain").splitlines()[:30]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--now", default=None, help="the instant the reports are computed at (default: the latest event time + 1 day)")
    ap.add_argument("--legacy-max", type=int, default=100_000)
    ap.add_argument("--budget", type=float, default=3.0)
    ap.add_argument("--steady-calls", type=int, default=5)
    ap.add_argument("--touch", type=int, default=1000)
    ap.add_argument("--label", default="metrics")
    ap.add_argument("--skip-refresh", action="store_true")
    args = ap.parse_args()
    import psycopg

    with psycopg.connect(args.dsn) as conn:
        latest = conn.execute("SELECT max(at) FROM events").fetchone()[0]
        total = conn.execute("SELECT count(*) FROM cases").fetchone()[0]
    now = args.now or (latest + __import__("datetime").timedelta(days=1)).isoformat()
    doc = {"tool": "tools/load/metrics_bench.py", "label": args.label, "git": git(), "started_at": datetime.now(UTC).isoformat(),
           "host": {"platform": platform.platform(), "python": platform.python_version(), "machine": platform.machine()},
           "cases_in_database": total, "now": now, "legacy_max": args.legacy_max}
    print(f"cases {total:,}; now {now}", flush=True)
    doc["legacy_replay"] = run(phase_legacy, args.dsn, now, args.legacy_max)
    print("legacy:", doc["legacy_replay"], flush=True)
    if not args.skip_refresh:
        doc["refresh"] = run(phase_refresh, args.dsn, args.budget)
        print("refresh:", {k: v for k, v in doc["refresh"].items() if k not in ("first_call", "last_call")}, flush=True)
    doc["equality_at_legacy_size"] = run(phase_equal, args.dsn, now, args.legacy_max)
    print("equal:", doc["equality_at_legacy_size"], flush=True)
    doc["steady"] = run(phase_steady, args.dsn, now, args.steady_calls)
    print("steady:", doc["steady"], flush=True)
    doc["incremental"] = run(phase_incremental, args.dsn, args.touch)
    print("incremental:", doc["incremental"], flush=True)
    doc["finished_at"] = datetime.now(UTC).isoformat()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(doc, indent=2, default=str) + "\n")
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
