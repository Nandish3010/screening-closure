#!/usr/bin/env python3
"""Soak test of the kind cluster `sc-m76` (M7-6): steady load for >= 2 h with the scheduler sweeping and the watchdog scanning.

The load is tools/k8s/api_load.py's open-loop generator (imported: same mix, same keep-alive client design, same bounded worklist),
run as ONE stage at a rate well inside the measured envelope (default 50 req/s; the sweep in latency.json passed at 175 req/s).
Next to it a sampler thread records, every --sample-period seconds (60):
  * per pod (api, scheduler, watchdog, postgres): cgroup memory.current, memory.stat anon / file, CPU (cpu.stat usage_usec and the
    throttling counters), read with `kubectl exec ... cat /sys/fs/cgroup/...` (there is no metrics-server);
  * per pod: container restart count, last termination reason / exit code / time (OOMKilled shows here), phase, readiness;
  * the database size, the size of the cases and events relations, the scheduler's leader lease (holder, fencing token) and the
    heartbeats table (the scheduler's last sweep info);
  * incremental logs: the API pods' access lines for /scheduler/* and /watchdog/* (sweep, drain and scan completion instants), the
    scheduler pods' loop lines (sweep outcome with error count, automation / evidence scan, leadership changes, warnings) and the
    watchdog pod's lines (scan outcome).
Latency percentiles are computed per 5-minute window of the generator's own records (intended start -> end of response, failures
counted at their latency) and overall. The result is written to docs/results/scale/soak.json (a checkpoint after every sample with
"status": "running", so an interrupted run leaves its series behind; a run that did not reach "finished" is not the reported soak).

    python tools/k8s/soak.py --duration 7500 [--rate 50] [--out docs/results/scale/soak.json]

macOS must not sleep during the run: start it under `caffeinate -i`.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import random
import re
import statistics
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_load as AL  # noqa: E402  (the generator, helpers and log analysis shared with the latency run)

ROOT = AL.ROOT
OUT = ROOT / "docs/results/scale/soak.json"
WINDOW_S = 300.0
SCHED_INTERVAL_S = 10.0
CG = ("cat /sys/fs/cgroup/cpu.stat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.max 2>/dev/null; echo '--stat'; "
      "grep -E '^(anon|file) ' /sys/fs/cgroup/memory.stat 2>/dev/null")
MEM_RE = re.compile(r"^\d+$")
ISO = re.compile(r"^(?P<ts>\d{4}-\d\d-\d\dT[\d:.]+Z) (?P<msg>.*)$")

METHOD = (
    "One uninterrupted open-loop stage of tools/k8s/api_load.py's generator at a constant arrival rate (request i is due at t0 + i/rate "
    "whatever the outstanding requests; latency = intended start to end of the response, failures counted at their latency), against the "
    "NodePort 127.0.0.1:18376 across the 3 API pods, on a database of ~1.04M cases / 7.2M events, while the 2 scheduler pods "
    "(one leads) sweep every 10 s with the automation and evidence scans, and the watchdog runs its scan (M7-7: the incremental scan plus a slice of the full reconciliation pass) at the chart's "
    "interval (watchdog.intervalSeconds). The mix is api_load.py's (bounded worklist only: GET /cases?state=lost_contact). Every "
    "60 s a sampler records per-pod cgroup memory (memory.current and anon from memory.stat) and CPU (kubectl exec; no metrics-server), "
    "container restart counts and last-termination reasons (kubectl get pods), the database size, the scheduler leader lease and the "
    "heartbeats table, and reads the new log lines of the API, scheduler and watchdog pods. Restarts during the soak = restartCount at "
    "the last sample minus at the first sample (pre-existing restarts excluded). Memory growth = least-squares slope of the pod's "
    "memory.current (and of anon) over the soak; reported for the whole series and for the series after the first 20 minutes (after "
    "caches and the allocator have warmed up); a pod that restarted has its series cut at the restart (the slope is taken over its "
    "longest restart-free stretch). Sweeps expected = measured seconds / 10 (the nominal interval; the real period is interval + the "
    "loop's own work: drain, sweep, automation scan, evidence scan, which run in series). Sweep duration = time between the "
    "outbox-drain and sweep access-log lines on the API (the sweep runs between them). Watchdog scan duration = from the instant the "
    "watchdog starts the call (its start, or its previous outcome + interval) to the API's access-log line for POST /watchdog/scan "
    "(logged when the request ends); the watchdog's own HTTP client times out after 120 s and logs 'scan failed', but the API goes on "
    "with the scan until it finishes. The generator, the kind cluster and Postgres share one host."
)


# ------------------------------------------------------------------------------------------------------------ helpers

def iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat()


def atomic_write(path: Path, doc: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1, default=str) + "\n")
    tmp.replace(path)


def slope_per_hour(points: list[tuple[float, float]]) -> float | None:
    """Least-squares slope of (t seconds, bytes), in MiB per hour."""
    if len(points) < 3:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den * 3600 / 2**20


def kube_exec_cgroup(pod: str) -> dict | None:
    try:
        out = subprocess.run(["kubectl", "--context", AL.CTX, "-n", AL.NS, "exec", pod, "--", "sh", "-c", CG], capture_output=True,
                             text=True, timeout=30).stdout
    except subprocess.SubprocessError:
        return None
    head, _, stat = out.partition("--stat")
    d: dict = {}
    nums: list[str] = []
    for ln in head.splitlines():
        p = ln.split()
        if len(p) == 2 and p[0] in ("usage_usec", "nr_throttled", "throttled_usec", "nr_periods"):
            d[p[0]] = int(p[1])
        elif len(p) == 1 and (MEM_RE.match(p[0]) or p[0] == "max"):
            nums.append(p[0])
    if nums and nums[0].isdigit():
        d["memory_current"] = int(nums[0])
    if len(nums) > 1 and nums[1].isdigit():
        d["memory_max"] = int(nums[1])
    for ln in stat.splitlines():
        p = ln.split()
        if len(p) == 2 and p[0] in ("anon", "file"):
            d["mem_" + p[0]] = int(p[1])
    return d if "memory_current" in d else None


def pod_states() -> dict[str, dict]:
    data = json.loads(AL.kubectl("get", "pods", "-o", "json", timeout=60))
    res = {}
    for p in data["items"]:
        name = p["metadata"]["name"]
        cs = (p["status"].get("containerStatuses") or [{}])[0]
        term = (cs.get("lastState") or {}).get("terminated") or {}
        res[name] = {
            "app": p["metadata"].get("labels", {}).get("app.kubernetes.io/name") or p["metadata"].get("labels", {}).get("app") or name,
            "phase": p["status"].get("phase"), "ready": cs.get("ready"), "restart_count": cs.get("restartCount", 0),
            "last_termination": {k: term.get(k) for k in ("reason", "exitCode", "finishedAt")} if term else None,
            "waiting_reason": (cs.get("state") or {}).get("waiting", {}).get("reason"),
            "started_at": cs.get("state", {}).get("running", {}).get("startedAt"),
            "pod_started": p["metadata"].get("creationTimestamp"),
        }
    return res


def parse_ts(s: str) -> float:
    s = s.rstrip("Z")
    if "." in s:
        a, b = s.split(".")
        s = f"{a}.{b[:6]}"
    return datetime.fromisoformat(s + "+00:00").timestamp()


class Collector(threading.Thread):
    def __init__(self, dsn: str, out: Path, doc: dict, gen: AL.Gen, period: float, t_wall0: float, warmup: float) -> None:
        super().__init__(daemon=True, name="soak-sampler")
        self.dsn, self.out, self.doc, self.gen, self.period = dsn, out, doc, gen, period
        self.t_wall0, self.warmup = t_wall0, warmup
        self.stop = threading.Event()
        self.samples: list[dict] = []
        self.api_events: dict[str, set] = collections.defaultdict(set)   # pod -> {(t, method, path, status)}
        self.sched_lines: set = set()                                    # (t, pod, message)
        self.wd_lines: set = set()
        self.last_fetch: dict[str, str] = {}
        self.windows_done: dict[int, dict] = {}
        self.sample_errors: list[str] = []
        self.conn: psycopg.Connection | None = None
        self.start_states: dict[str, dict] | None = None

    # -- one sample
    def sample(self) -> dict:
        t = time.time()
        s: dict = {"t": iso(t), "t_epoch": round(t, 3), "elapsed_s": round(t - self.t_wall0, 1)}
        try:
            st = pod_states()
            s["pods"] = st
        except (subprocess.SubprocessError, ValueError) as exc:
            st = {}
            self.sample_errors.append(f"{iso(t)} pod_states: {exc}")
        res: dict[str, dict] = {}
        threads = []
        for name in st:
            if st[name]["phase"] != "Running" or not name.startswith(("closure-", "postgres")):
                continue
            def one(name=name):
                r = kube_exec_cgroup(name)
                if r:
                    res[name] = r
            th = threading.Thread(target=one)
            th.start()
            threads.append(th)
        for th in threads:
            th.join()
        s["cgroup"] = res
        try:
            if self.conn is None or self.conn.closed:
                self.conn = psycopg.connect(self.dsn, autocommit=True, connect_timeout=10)
                self.conn.execute("SET max_parallel_workers_per_gather = 0")
            c = self.conn
            s["database_bytes"] = c.execute("SELECT pg_database_size(current_database())").fetchone()[0]
            s["relation_bytes"] = {r: c.execute("SELECT pg_total_relation_size(%s::regclass)", (r,)).fetchone()[0] for r in ("cases", "events")}
            s["leader_lease"] = [{"name": n, "holder": h, "token": tok, "expires_in_s": round(float(e), 1)} for n, h, tok, e in c.execute(
                "SELECT name, holder, token, extract(epoch FROM (expires_at - now())) FROM leader_lease ORDER BY name").fetchall()]
            s["heartbeats"] = {svc: {"age_s": round(float(a), 1), "info": info} for svc, a, info in c.execute(
                "SELECT service, extract(epoch FROM (now() - beat_at)), info FROM heartbeats").fetchall()}
            s["connections"] = c.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()").fetchone()[0]
        except Exception as exc:  # noqa: BLE001 - a failed probe must not end the soak
            self.sample_errors.append(f"{iso(t)} postgres: {type(exc).__name__}: {exc}")
            self.conn = None
        try:
            self.fetch_logs(st)
        except Exception as exc:  # noqa: BLE001
            self.sample_errors.append(f"{iso(t)} logs: {type(exc).__name__}: {exc}")
        s["generator"] = {"issued_so_far": len(self.gen.records), "max_lag_ms": round(self.gen.max_lag * 1000, 1)}
        s["took_s"] = round(time.time() - t, 2)
        return s

    def fetch_logs(self, st: dict[str, dict]) -> None:
        for name, info in st.items():
            app = info["app"]
            if app not in ("api", "scheduler", "watchdog") or info["phase"] != "Running":
                continue
            since = self.last_fetch.get(name)
            now = datetime.now(UTC)
            args = ["logs", name, "--timestamps"] + ([f"--since-time={since}"] if since else ([] if app == "watchdog" else [f"--since-time={iso(self.t_wall0 - 60)}"]))
            text = AL.kubectl(*args, timeout=120, check=False)
            self.last_fetch[name] = datetime.fromtimestamp(now.timestamp() - 5, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            for ln in text.splitlines():
                m = ISO.match(ln)
                if not m:
                    continue
                t, msg = parse_ts(m["ts"]), m["msg"]
                if app == "api":
                    if "uvicorn.access" not in msg:
                        continue
                    try:
                        d = json.loads(msg)
                    except ValueError:
                        continue
                    a = AL.ACCESS.search(d.get("message", ""))
                    if a and (a["p"].startswith("/scheduler/") or a["p"].startswith("/watchdog/")):
                        self.api_events[name].add((t, a["m"], a["p"], int(a["s"])))
                elif app == "scheduler":
                    self.sched_lines.add((t, name, msg))
                else:
                    self.wd_lines.add((t, name, msg))

    # -- latency windows (a window is computed once, when it is complete)
    def finish_windows(self, upto_rel: float, final: bool = False) -> None:
        recs = list(self.gen.records)
        by_w: dict[int, list] = collections.defaultdict(list)
        for r in recs:
            if r[4] >= self.warmup:
                by_w[int((r[4] - self.warmup) // WINDOW_S)].append(r)
        for w, rs in by_w.items():
            end = self.warmup + (w + 1) * WINDOW_S
            if w in self.windows_done or (end > upto_rel and not final):
                continue
            self.windows_done[w] = window_stats(w, rs, self.t_wall0 + self.warmup + w * WINDOW_S)

    def run(self) -> None:
        while not self.stop.is_set():
            t0 = time.time()
            try:
                s = self.sample()
                self.samples.append(s)
                self.finish_windows(time.time() - self.t_wall0 - 5)
                self.checkpoint()
            except Exception as exc:  # noqa: BLE001
                self.sample_errors.append(f"{iso(t0)} sample: {type(exc).__name__}: {exc}")
            self.stop.wait(max(0.0, self.period - (time.time() - t0)))

    def checkpoint(self) -> None:
        doc = dict(self.doc)
        doc.update(status="running", updated_at=datetime.now(UTC).isoformat(), samples=self.samples, sample_errors=self.sample_errors,
                   windows=[self.windows_done[k] for k in sorted(self.windows_done)])
        atomic_write(self.out, doc)


def window_stats(w: int, rs_all: list, t_start: float) -> dict:
    ok = lambda r: isinstance(r[1], int) and 200 <= r[1] < 300  # noqa: E731
    shed = sum(1 for r in rs_all if r[1] == AL.SHED)  # refused with 503 + Retry-After (ADR-048): counted apart, not an error
    rs = [r for r in rs_all if r[1] != AL.SHED]
    errs = collections.Counter(str(r[1]) for r in rs if not ok(r))
    point = [r for r in rs if r[0] != "worklist"]
    return {"window": w, "start": iso(t_start), "requests": len(rs_all), "answered": len(rs), "shed": shed, "errors": sum(errs.values()), "errors_by_status": dict(errs),
            "latency_all": AL.dist([r[2] for r in rs]), "latency_excluding_worklist": AL.dist([r[2] for r in point])}


# ------------------------------------------------------------------------------------------------------------ summary

def derive_cadence(col: Collector, t_from: float, t_to: float) -> dict:
    """M7-8 (ADR-047): the achieved start cadence of every scheduler task, from the `tick task=... start=...` lines. A start is counted when
    the task began work (the scheduler writes the line when the tick ends, with the start instant it recorded). Per task: starts in the
    window against the starts expected at the nominal interval, the gaps between consecutive starts, the work duration and the
    ticks the scheduler itself reports skipping."""
    nominal = {"sweep": SCHED_INTERVAL_S, "automation-scan": SCHED_INTERVAL_S, "evidence-scan": SCHED_INTERVAL_S, "agency": 60.0}
    rows: dict[str, list[tuple[float, float, int]]] = collections.defaultdict(list)
    for t, pod, msg in col.sched_lines:
        m = AL.TICK.search(msg)
        if m and t_from <= float(m["start"]) <= t_to:
            rows[m["task"]].append((float(m["start"]), float(m["dur"]), int(m["sk"])))
    out = {}
    for task, rs in sorted(rows.items()):
        rs.sort()
        starts = [r[0] for r in rs]
        gaps = sorted(b - a for a, b in zip(starts, starts[1:]))
        durs = sorted(r[1] for r in rs)
        interval = nominal.get(task, SCHED_INTERVAL_S)
        expected = (t_to - t_from) / interval
        # a gap of about two intervals or more means a tick was skipped (or a leader change): count the grid points inside the gaps
        missed = sum(max(0, round(g / interval) - 1) for g in gaps)
        out[task] = {
            "nominal_interval_s": interval, "starts": len(starts), "expected_starts": round(expected, 1),
            "achieved_fraction_of_nominal": round(len(starts) / expected, 4) if expected else None,
            "gap_s": ({"mean": round(statistics.fmean(gaps), 3), "p50": round(AL.pct(gaps, 0.5), 3), "p95": round(AL.pct(gaps, 0.95), 3),
                       "p99": round(AL.pct(gaps, 0.99), 3), "max": round(gaps[-1], 3)} if gaps else None),
            "gaps_over_1_5_intervals": sum(1 for g in gaps if g > 1.5 * interval), "grid_points_missed_in_gaps": missed,
            "duration_s": ({"mean": round(statistics.fmean(durs), 3), "p50": round(AL.pct(durs, 0.5), 3), "p95": round(AL.pct(durs, 0.95), 3),
                            "max": round(durs[-1], 3)} if durs else None),
            "skipped_ticks_reported_by_scheduler": max((r[2] for r in rs), default=0),
            "meets_95_percent_of_nominal": bool(expected and len(starts) / expected >= 0.95),
        }
    return out


def derive_sweeps(col: Collector, t_from: float, t_to: float) -> dict:
    api = {pod: sorted(ev) for pod, ev in col.api_events.items()}
    rows = {pod: [(t, m, p, s) for t, m, p, s in ev if m == "POST"] for pod, ev in api.items()}
    timings = AL.scheduler_timings(rows, t_from, t_to)
    ev = sorted((t, p) for evs in rows.values() for t, m, p, s in evs if p.startswith("/scheduler/") and s == 200)
    series = []
    for i, (t, p) in enumerate(ev):
        if p != "/scheduler/sweep" or not (t_from <= t <= t_to):
            continue
        d = next((tt for tt, pp in reversed(ev[:i]) if pp == "/scheduler/outbox/drain"), None)
        if d is not None and t - d < 600:
            series.append((round(t, 2), round(t - d, 3)))
    statuses = collections.Counter(s for evs in rows.values() for t, m, p, s in evs if p == "/scheduler/sweep" and t_from <= t <= t_to)
    # the scheduler's own loop lines
    lines = sorted(x for x in col.sched_lines if t_from <= x[0] <= t_to)
    sweep_lines = [x for x in lines if " sweep sim_time=" in x[2]]
    err_total = sum(int(m.group(1)) for _, _, msg in sweep_lines for m in [re.search(r"errors=(\d+)", msg)] if m)
    failures = [x for x in lines if re.search(r"sweep (failed|refused)|refused|failed", x[2]) and "WARNING" in x[2] or " ERROR " in x[2]]
    leaders = [x for x in lines if "is the leader" in x[2] or "no longer the leader" in x[2]]
    scans = {k: sum(1 for x in lines if f" {k} " in x[2]) for k in ("automation-scan", "evidence-scan")}
    measured = t_to - t_from
    gaps = sorted(b[0] - a[0] for a, b in zip(sweep_lines, sweep_lines[1:]))
    return {"sweeps_completed": len(sweep_lines), "sweeps_expected_at_10s": round(measured / SCHED_INTERVAL_S, 1),
            "completed_fraction_of_expected": round(len(sweep_lines) / (measured / SCHED_INTERVAL_S), 4) if measured else None,
            "measured_seconds": round(measured, 1), "sweep_period_s": ({"mean": round(statistics.fmean(gaps), 2), "p50": round(AL.pct(gaps, 0.5), 2),
                                                                         "p95": round(AL.pct(gaps, 0.95), 2), "max": round(gaps[-1], 2)} if gaps else None),
            "sweep_errors_reported_in_summaries": err_total, "sweep_http_status_counts": dict(statuses),
            "scheduler_warning_or_error_lines": [{"t": iso(x[0]), "pod": x[1], "line": x[2][:300]} for x in failures[:50]],
            "scheduler_warning_or_error_count": len(failures), "leadership_log_lines": [{"t": iso(x[0]), "pod": x[1], "line": x[2][-200:]} for x in leaders],
            "scan_log_lines": scans, "durations_from_api_access_log": timings, "cadence": derive_cadence(col, t_from, t_to),
            "max_sweep_duration_s": max((d for _, d in series), default=None), "sweep_duration_series": series}


def derive_watchdog(col: Collector, t_from: float, t_to: float, interval_s: float) -> dict:
    wl = sorted(col.wd_lines)
    events = [(t, msg) for t, _, msg in wl]
    api_ends = sorted(t for evs in col.api_events.values() for t, m, p, s in evs if p == "/watchdog/scan")
    api_status = {t: s for evs in col.api_events.values() for t, m, p, s in evs if p == "/watchdog/scan"}
    scans, nxt = [], None
    for t, msg in events:
        if "starting; interval=" in msg:
            nxt = t
        elif "scan sim_time=" in msg or "scan failed" in msg or "scan refused" in msg:
            start = nxt if nxt is not None else t - 120
            end = next((e for e in api_ends if e >= start - 1), None)
            if end is not None and "scan failed" in msg and end < t - 130:
                end = None
            ok = "scan sim_time=" in msg
            scans.append({"start": iso(start), "client_outcome_at": iso(t), "client_outcome": "ok" if ok else msg.split(" watchdog ", 1)[-1][:160],
                          "api_log_end": iso(end) if end else None, "api_status": api_status.get(end) if end else None,
                          "duration_s": round(end - start, 1) if end else None,
                          "client_wait_s": round(t - start, 1), "within_soak": t_from <= start <= t_to})
            nxt = t + interval_s
    durs = [s["duration_s"] for s in scans if s["duration_s"] is not None and s["within_soak"]]
    return {"interval_s": interval_s, "scans_observed": len([s for s in scans if s["within_soak"]]), "scans": scans,
            "api_scan_requests_logged": len([t for t in api_ends if t_from <= t <= t_to]),
            "duration_s": ({"min": min(durs), "mean": round(statistics.fmean(durs), 1), "max": max(durs)} if durs else None),
            "raw_watchdog_lines": [{"t": iso(t), "line": m[:250]} for t, m in events][-40:]}


def pod_summaries(samples: list[dict], t_wall0: float) -> dict:
    series: dict[str, list[dict]] = collections.defaultdict(list)
    for s in samples:
        for name, cg in (s.get("cgroup") or {}).items():
            st = (s.get("pods") or {}).get(name, {})
            series[name].append({"t": s["t_epoch"], "rc": st.get("restart_count"), **cg})
    out: dict = {}
    first_states = next((s["pods"] for s in samples if s.get("pods")), {})
    last_states = next((s["pods"] for s in reversed(samples) if s.get("pods")), {})
    for name, rows in series.items():
        # restart-free stretches: a restart resets the cgroup (counter drops / restart count changes)
        stretches, cur = [], [rows[0]]
        for r in rows[1:]:
            if r["rc"] != cur[-1]["rc"]:
                stretches.append(cur)
                cur = []
            cur.append(r)
        stretches.append(cur)
        best = max(stretches, key=len)
        t0 = best[0]["t"]
        def pts(key, rows_=None):
            return [(r["t"] - t0, r[key]) for r in (rows_ or best) if key in r]
        after = [r for r in best if r["t"] - t0 >= 1200]
        cpu = [(b["usage_usec"] - a["usage_usec"]) / 1e6 / (b["t"] - a["t"]) for a, b in zip(best, best[1:]) if "usage_usec" in a and "usage_usec" in b and b["t"] > a["t"]]
        mem = [r["memory_current"] for r in rows]
        anon = [r["mem_anon"] for r in rows if "mem_anon" in r]
        out[name] = {
            "samples": len(rows), "restart_free_stretches": len(stretches), "stretch_used_seconds": round(best[-1]["t"] - best[0]["t"]),
            "memory_current_start_mib": round(mem[0] / 2**20, 1), "memory_current_end_mib": round(mem[-1] / 2**20, 1),
            "memory_current_max_mib": round(max(mem) / 2**20, 1), "memory_anon_start_mib": round(anon[0] / 2**20, 1) if anon else None,
            "memory_anon_end_mib": round(anon[-1] / 2**20, 1) if anon else None, "memory_anon_max_mib": round(max(anon) / 2**20, 1) if anon else None,
            "memory_limit_mib": round(rows[-1]["memory_max"] / 2**20) if "memory_max" in rows[-1] else None,
            "memory_current_slope_mib_per_hour": _r(slope_per_hour(pts("memory_current"))),
            "memory_anon_slope_mib_per_hour": _r(slope_per_hour(pts("mem_anon"))),
            "memory_current_slope_after_20min_mib_per_hour": _r(slope_per_hour(pts("memory_current", after))),
            "memory_anon_slope_after_20min_mib_per_hour": _r(slope_per_hour(pts("mem_anon", after))),
            "cpu_cores_mean": _r(statistics.fmean(cpu), 3) if cpu else None, "cpu_cores_max": _r(max(cpu), 3) if cpu else None,
            "cpu_throttled_periods": (best[-1].get("nr_throttled", 0) - best[0].get("nr_throttled", 0)),
            "restart_count_start": (first_states.get(name) or {}).get("restart_count"), "restart_count_end": (last_states.get(name) or {}).get("restart_count"),
        }
    return out


def _r(x, n=2):
    return None if x is None else round(x, n)


def restart_summary(samples: list[dict], t_start: float) -> dict:
    """Restarts during the soak per container, from the first and last pod listing; reasons from lastState.terminated. A pod that
    appeared or vanished (a rollout, an eviction) is listed apart."""
    first = next((s["pods"] for s in samples if s.get("pods")), {})
    last = next((s["pods"] for s in reversed(samples) if s.get("pods")), {})
    per_pod = {}
    for name in sorted(set(first) | set(last)):
        a, b = first.get(name), last.get(name)
        if a is None or b is None:
            per_pod[name] = {"note": "pod absent at the start" if a is None else "pod absent at the end (replaced during the soak)",
                             "restart_count_start": (a or {}).get("restart_count"), "restart_count_end": (b or {}).get("restart_count")}
            continue
        d = b["restart_count"] - a["restart_count"]
        per_pod[name] = {"restart_count_start": a["restart_count"], "restart_count_end": b["restart_count"], "restarts_during_soak": d,
                         "last_termination_at_end": b["last_termination"] if d else None}
    terminations = {}
    for s in samples:
        for name, st in (s.get("pods") or {}).items():
            lt = st.get("last_termination")
            if lt and lt.get("finishedAt") and parse_ts(lt["finishedAt"]) >= t_start:
                terminations[(name, lt["finishedAt"])] = {"pod": name, **lt, "seen_at": s["t"]}
    return {"per_pod": per_pod, "terminations_during_soak": sorted(terminations.values(), key=lambda x: x["finishedAt"]),
            "total_restarts_during_soak": sum(v.get("restarts_during_soak", 0) for v in per_pod.values()),
            "oom_killed_during_soak": sum(1 for v in terminations.values() if v.get("reason") == "OOMKilled")}


def leader_summary(samples: list[dict]) -> dict:
    seq = []
    for s in samples:
        for l in s.get("leader_lease") or []:
            if l["name"] == "scheduler":
                key = (l["holder"], l["token"])
                if not seq or seq[-1]["key"] != key:
                    seq.append({"key": key, "first_seen": s["t"], "holder": l["holder"], "token": l["token"]})
    for x in seq:
        x.pop("key")
    return {"scheduler_leader_changes": max(0, len(seq) - 1), "scheduler_leader_history": seq}


def build_summary(col: Collector, gen: AL.Gen, args, t_wall0: float, t_wall_end: float, wd_interval: float) -> dict:
    samples = col.samples
    t_from, t_to = t_wall0 + args.warmup, t_wall0 + args.warmup + args.duration
    stage = AL.summarise(gen, args.rate, args.warmup, args.duration)
    wins = [col.windows_done[k] for k in sorted(col.windows_done)]
    full = [w for w in wins if w["requests"] >= 0.8 * args.rate * WINDOW_S]
    worst = max(full, key=lambda w: w["latency_all"]["p99_ms"] or 0) if full else None
    worst_p95 = max(full, key=lambda w: w["latency_all"]["p95_ms"] or 0) if full else None
    gaps = [b["t_epoch"] - a["t_epoch"] for a, b in zip(samples, samples[1:])]
    rec = [r for r in gen.records if r[4] >= args.warmup]
    ok = lambda r: isinstance(r[1], int) and 200 <= r[1] < 300  # noqa: E731
    status_counts = collections.Counter(str(r[1]) for r in rec)
    n = len(rec)
    dbs = [(s["t_epoch"], s["database_bytes"]) for s in samples if "database_bytes" in s]
    return {
        "measured_seconds": args.duration, "wall_seconds_total": round(t_wall_end - t_wall0, 1), "target_rate_per_s": args.rate,
        "requests_total": n, "requests_2xx": sum(1 for r in rec if ok(r)), "achieved_rate_per_s": round(n / args.duration, 2),
        "cases_created_by_run": gen.n, "issued_total_including_warmup": stage["issued_total"], "unfinished_after_45s": stage["unfinished_after_45s"],
        "errors": stage["errors"], "shed": stage["shed"], "error_rate": stage["error_rate"], "status_counts": dict(status_counts),
        "errors_by_status": stage["errors_by_status"],
        "latency_all_requests": stage["latency_all_requests"], "latency_2xx": stage["latency_2xx"],
        "latency_all_requests_excluding_worklist": stage["latency_all_requests_excluding_worklist"],
        "window_seconds": WINDOW_S, "windows": len(wins), "worst_window_by_p99": worst, "worst_window_by_p95": worst_p95,
        "windows_p99_ms_range": [min((w["latency_all"]["p99_ms"] for w in full), default=None), max((w["latency_all"]["p99_ms"] for w in full), default=None)],
        "windows_over_p99_500ms": sum(1 for w in full if (w["latency_all"]["p99_ms"] or 0) >= 500),
        "per_endpoint": stage["per_endpoint"], "generator_lag_ms": stage["generator_lag_ms"],
        "pass": stage["pass"], "criteria": stage["criteria"],
        "restarts": restart_summary(samples, t_wall0), "pods": pod_summaries(samples, t_wall0), "leader": leader_summary(samples),
        "scheduler": derive_sweeps(col, t_from, t_to), "watchdog": derive_watchdog(col, t_wall0, t_to, wd_interval),
        "database": {"bytes_start": dbs[0][1] if dbs else None, "bytes_end": dbs[-1][1] if dbs else None,
                     "growth_mib": round((dbs[-1][1] - dbs[0][1]) / 2**20, 1) if len(dbs) > 1 else None,
                     "growth_mib_per_hour": _r(slope_per_hour([(t - dbs[0][0], b) for t, b in dbs])) if len(dbs) > 2 else None},
        "sampling": {"period_s": args.sample_period, "samples": len(samples), "max_gap_s": round(max(gaps), 1) if gaps else None,
                     "gaps_over_3_periods": [round(g) for g in gaps if g > 3 * args.sample_period],
                     "uninterrupted": bool(gaps) and max(gaps) <= 3 * args.sample_period and stage["unfinished_after_45s"] == 0},
        "sample_errors": col.sample_errors[:50], "sample_error_count": len(col.sample_errors),
    }


# ------------------------------------------------------------------------------------------------------------ driver

def agency_summary(dsn: str, since_epoch: float) -> dict:
    """What the agency did during the soak (M7-7 (b)), from the ledger: runs started since `since_epoch` (root tasks), their final status
    (`done`, `capped` = the bounded read or the order cap left work for the next run, `budget_exhausted`), the cases each run read (the cap
    is AGENCY_MAX_CASES_READ), the tasks they created, and how long each took."""
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT status, output, extract(epoch FROM (finished_at - started_at)), created_at FROM agency_tasks"
            " WHERE parent_id IS NULL AND created_at >= to_timestamp(%s) ORDER BY created_at", (since_epoch,)).fetchall()
        tasks = conn.execute("SELECT count(*) FROM agency_tasks WHERE parent_id IS NOT NULL AND created_at >= to_timestamp(%s)", (since_epoch,)).fetchone()[0]
    outs = [r[1] or {} for r in rows]
    by_status = collections.Counter((o.get("status") or r[0]) for o, r in zip(outs, rows))
    read = [o.get("cases_read") for o in outs if isinstance(o.get("cases_read"), int)]
    secs = sorted(float(r[2]) for r in rows if r[2] is not None)
    return {"runs": len(rows), "runs_by_status": dict(by_status), "tasks_created_total": tasks,
            "cases_read_per_run": {"max": max(read) if read else None, "median": statistics.median(read) if read else None, "sum": sum(read)},
            "runs_with_truncated_read": sum(1 for o in outs if o.get("read_truncated")), "runs_with_order_cap": sum(1 for o in outs if o.get("orders_capped")),
            "run_seconds": {"median": round(statistics.median(secs), 2), "p95": round(AL.pct(secs, 0.95), 2), "max": round(secs[-1], 2)} if secs else None,
            "first_run": rows[0][3].isoformat() if rows else None, "last_run": rows[-1][3].isoformat() if rows else None}


async def amain(args) -> int:
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    dsn = await asyncio.to_thread(AL.owner_dsn)
    tokens = {"lab": AL.mint("lab", "soak-lab", ttl=args.ttl), "coord": AL.mint("coordinator", "soak", ttl=args.ttl),
              "pco": AL.mint("primary_care_owner", "Dr. Layla Haddad", sub="Dr. Layla Haddad", ttl=args.ttl)}
    seeded = await asyncio.to_thread(AL.sample_seeded_ids, dsn, args.sample_ids)
    gen = AL.Gen(tokens, seeded, random.Random(args.seed))
    chart = AL.chart_values()
    wd_interval = float(chart.get("watchdog", {}).get("intervalSeconds", 10))
    machine = json.loads(subprocess.run(["bash", str(ROOT / "tools/k8s/machine_spec.sh")], capture_output=True, text=True, check=True).stdout)
    doc: dict = {
        "schema_version": 1, "tool": "tools/k8s/soak.py", "method": METHOD, "git": AL.git_info(), "machine_spec": machine, "chart_values": chart,
        "chart_file_values_kind": (ROOT / "deploy/helm/screening-closure/values-kind.yaml").read_text(),
        "cluster_overrides": await asyncio.to_thread(AL.cluster_overrides), "mix_weights": dict(AL.MIX), "list_state": AL.LIST_STATE,
        "parameters": {"rate_per_s": args.rate, "warmup_s": args.warmup, "duration_s": args.duration, "sample_period_s": args.sample_period,
                       "window_s": WINDOW_S, "seed": args.seed, "label": args.label},
        "population_before": await asyncio.to_thread(AL.population, dsn), "caffeinated": bool(os.environ.get("SOAK_CAFFEINATED")),
        "pids": {"soak": os.getpid()},
    }
    t_wall0 = time.time() + 0.5
    doc["started_at"] = iso(t_wall0)
    col = Collector(dsn, out, doc, gen, args.sample_period, t_wall0, args.warmup)
    col.start()
    print(f"soak: {args.rate:g} req/s for {args.duration:g}s (+{args.warmup:g}s warm-up), started {iso(t_wall0)}; out {out}", flush=True)
    t0, _ = await gen.stage(args.rate, args.warmup, args.duration)
    t_wall_end = time.time()
    col.stop.set()
    col.join(timeout=180)
    col.samples.append(await asyncio.to_thread(col.sample))      # the closing sample (restart counts, memory at the end)
    col.finish_windows(float("inf"), final=True)
    doc["finished_at"] = iso(t_wall_end)
    doc["population_after"] = await asyncio.to_thread(AL.population, dsn)
    doc["agency"] = await asyncio.to_thread(agency_summary, dsn, t_wall0)
    doc["summary"] = build_summary(col, gen, args, t_wall0, t_wall_end, wd_interval)
    doc.update(status="finished", samples=col.samples, sample_errors=col.sample_errors, windows=[col.windows_done[k] for k in sorted(col.windows_done)])
    atomic_write(out, doc)
    s = doc["summary"]
    la = s["latency_all_requests"]
    print(f"soak done: {s['requests_total']} requests, error rate {s['error_rate']:.5f}, p50/p95/p99 {la['p50_ms']}/{la['p95_ms']}/{la['p99_ms']} ms, "
          f"worst window p99 {(s['worst_window_by_p99'] or {}).get('latency_all', {}).get('p99_ms')} ms, restarts {s['restarts']['total_restarts_during_soak']}, "
          f"sweeps {s['scheduler']['sweeps_completed']}/{s['scheduler']['sweeps_expected_at_10s']}, uninterrupted {s['sampling']['uninterrupted']}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rate", type=float, default=50.0, help="steady arrival rate, req/s (the measured envelope is 175)")
    ap.add_argument("--duration", type=float, default=7500.0, help="measured seconds after the warm-up (7500 = 2 h 5 min)")
    ap.add_argument("--warmup", type=float, default=15.0)
    ap.add_argument("--sample-period", type=float, default=60.0)
    ap.add_argument("--sample-ids", type=int, default=5000)
    ap.add_argument("--ttl", type=int, default=21600, help="lifetime of the minted JWTs, seconds")
    ap.add_argument("--seed", type=int, default=7602)
    ap.add_argument("--label", default="soak")
    ap.add_argument("--out", default=str(OUT))
    return asyncio.run(amain(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
