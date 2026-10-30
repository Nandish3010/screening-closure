#!/usr/bin/env python3
"""API latency at a sustained arrival rate across the 3 API replicas of the kind cluster `sc-m76` (M7-6).

An OPEN-LOOP, constant-arrival-rate load generator (asyncio + httpx): requests are issued at fixed instants
t0 + i/rate whether or not earlier ones have finished, so a slow server cannot slow the generator down and hide its own
latency (no coordinated omission). Latency is measured from the INTENDED start instant to the end of the response body;
the service time from the actual send is recorded as well, and so is the generator's own lag.

The mix (weights below) is the pathway's real traffic, through the NodePort 127.0.0.1:18376 (kube-proxy spreads it over the
API pods), authenticated with real OIDC-shaped tokens minted by tools/k8s/mint_token.py:
    create_case        lab          POST /cases                       (case_id client-chosen, result_ref DiagnosticReport/<id>)
    post_event         pco / coord  POST /cases/{id}/events           accept_ownership -> contact_patient -> patient_acknowledge
                                    on cases this run created (the automation acknowledges + assigns at creation)
    get_case_seeded    coordinator  GET  /cases/{id}                  a case from the large seeded population (cold-ish data)
    get_case_own       coordinator  GET  /cases/{id}                  a case this run created
    timeline           coordinator  GET  /cases/{id}/timeline         seeded case
    notifications      coordinator  GET  /cases/{id}/notifications    own case
    worklist           coordinator  GET  /cases?state=lost_contact    a bounded worklist (a few hundred rows; the unbounded worklist
                                    is measured separately by --probe-worklist, see the results)
Every response must be 2xx; anything else (4xx included) counts as an error.

While each stage runs, a sampler records per-pod CPU and memory from the pods' cgroups (`kubectl exec ... cat
/sys/fs/cgroup/...`; there is no metrics-server) and the kind nodes' `docker stats`. After the stage the API pods' logs give the
per-pod request distribution and the scheduler's own loop timings (sweep, automation scan, evidence scan: the gaps between
the access-log lines of the scheduler's POSTs).

The load generator runs on the SAME host (macOS, Docker Desktop VM) as the cluster: it competes with the cluster for CPU.

    python tools/k8s/api_load.py --rates 50,100 --duration 300 [--search-start 150 --search-step 50] [--probe-worklist]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import collections
import json
import random
import re
import statistics
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg

ROOT = Path(__file__).resolve().parents[2]
CTX = "kind-sc-m76"
NS = "closure"
API_BASE = "http://127.0.0.1:18376"
PG_HOSTPORT = "127.0.0.1:15476"
DEFAULT_OUT = ROOT / "docs/results/scale/latency.json"
P99_LIMIT_S = 0.5
ERROR_LIMIT = 0.001
LIST_STATE = "lost_contact"
MIX = [("create_case", 12), ("post_event", 28), ("get_case_seeded", 20), ("get_case_own", 15), ("timeline", 10),
       ("notifications", 14), ("worklist", 1)]
ENDPOINT = {"create_case": "POST /cases", "post_event": "POST /cases/{id}/events", "get_case_seeded": "GET /cases/{id}",
            "get_case_own": "GET /cases/{id}", "timeline": "GET /cases/{id}/timeline",
            "notifications": "GET /cases/{id}/notifications", "worklist": "GET /cases?state="}
EVENT_STEPS = [("accept_ownership", "pco"), ("contact_patient", "pco"), ("patient_acknowledge", "coord")]
SCHED_INTERVAL = 10.0

METHOD = (
    "Open-loop constant arrival rate: request i is due at t0 + i/rate and is issued then, regardless of outstanding requests "
    "(no coordinated omission). Latency = intended start to end of response; service latency = actual send to end of response. "
    "Each stage = a warm-up (excluded from the statistics) then the measured duration, at one rate. Mix (weights %): "
    + ", ".join(f"{k} {w}" for k, w in MIX) + ". Cases for post_event/get_case_own/notifications are created by the run itself; "
    "get_case_seeded/timeline read cases from the seeded population (random sample of the `closure` table). Every non-2xx response "
    "is an error. The generator, the kind cluster (3 nodes in the Docker Desktop VM) and Postgres share one host: the generator's "
    "CPU use competes with the cluster. Per-pod CPU/memory come from the pods' cgroup files read with `kubectl exec` (no "
    "metrics-server), node CPU/memory from `docker stats`; per-pod request counts from the API pods' access logs; scheduler "
    "timings from the gaps between the scheduler's POSTs in the API access logs (outbox drain -> sweep -> automation scan -> "
    "evidence scan are sequential in one loop)."
)


# ------------------------------------------------------------------------------------------------------ cluster helpers

def kubectl(*args: str, timeout: float = 60, check: bool = True) -> str:
    return subprocess.run(["kubectl", "--context", CTX, "-n", NS, *args], capture_output=True, text=True, check=check,
                          timeout=timeout).stdout


def owner_dsn() -> str:
    pw = base64.b64decode(kubectl("get", "secret", "sc-postgres", "-o", "jsonpath={.data.password}")).decode()
    return f"postgresql://closure_owner:{pw}@{PG_HOSTPORT}/closure?sslmode=disable"


def mint(role: str, name: str, sub: str | None = None, ttl: int = 21600) -> str:
    cmd = [str(ROOT / ".venv/bin/python"), str(ROOT / "tools/k8s/mint_token.py"), "--role", role, "--name", name, "--ttl", str(ttl)]
    return subprocess.run(cmd + (["--sub", sub] if sub else []), capture_output=True, text=True, check=True, timeout=60).stdout.strip()


def pods(label: str) -> list[str]:
    out = kubectl("get", "pods", "-l", label, "-o", "jsonpath={.items[*].metadata.name}")
    return sorted(out.split())


def git_info() -> dict:
    def g(*a: str) -> str:
        return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True).stdout.strip()
    return {"commit": g("rev-parse", "HEAD"), "branch": g("rev-parse", "--abbrev-ref", "HEAD"), "dirty_files": g("status", "--porcelain").splitlines()[:30]}


def chart_values() -> dict:
    vals = json.loads(subprocess.run(["helm", "--kube-context", CTX, "-n", NS, "get", "values", "closure", "--all", "-o", "json"],
                                     capture_output=True, text=True, check=True).stdout)
    return {k: vals[k] for k in ("images", "api", "scheduler", "watchdog", "app") if k in vals}


def cluster_overrides() -> dict:
    """What the running cluster differs in from the Helm values: env set on the API deployment with `kubectl set env` and the Postgres
    statefulset's arguments and resources (tools/k8s/postgres.yaml)."""
    dep = json.loads(kubectl("get", "deployment", "closure-api", "-o", "json"))
    env = {e["name"]: e.get("value", "<secret/ref>") for e in dep["spec"]["template"]["spec"]["containers"][0]["env"]}
    sts = json.loads(kubectl("get", "statefulset", "postgres", "-o", "json"))["spec"]["template"]["spec"]["containers"][0]
    return {"api_env_AGENCY_EVERY_SWEEPS": env.get("AGENCY_EVERY_SWEEPS", "<unset: code default 6>"),
            "api_env_SWEEP_WORKERS": env.get("SWEEP_WORKERS"), "api_resources": dep["spec"]["template"]["spec"]["containers"][0]["resources"],
            "postgres_args": sts.get("args"), "postgres_resources": sts.get("resources")}


def population(dsn: str) -> dict:
    with psycopg.connect(dsn) as conn:
        conn.execute("SET max_parallel_workers_per_gather = 0")
        by_state = dict(conn.execute("SELECT current_state, count(*) FROM cases GROUP BY 1 ORDER BY 2 DESC").fetchall())
        return {"cases": sum(by_state.values()), "by_state": by_state,
                "events": conn.execute("SELECT count(*) FROM events").fetchone()[0],
                "database_bytes": conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]}


# ------------------------------------------------------------------------------------------------------ resource sampler

CG = "cat /sys/fs/cgroup/cpu.stat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.max 2>/dev/null"


def _cgroup(pod: str) -> dict | None:
    try:
        out = subprocess.run(["kubectl", "--context", CTX, "-n", NS, "exec", pod, "--", "sh", "-c", CG], capture_output=True, text=True,
                             timeout=20).stdout
    except subprocess.SubprocessError:
        return None
    d: dict = {}
    lines = out.split("\n")
    for ln in lines:
        p = ln.split()
        if len(p) == 2 and p[0] in ("usage_usec", "nr_throttled", "throttled_usec", "nr_periods"):
            d[p[0]] = int(p[1])
    nums = [ln for ln in lines if re.fullmatch(r"\d+|max", ln.strip())]
    if len(nums) >= 1 and nums[0].isdigit():
        d["memory_current"] = int(nums[0])
    if len(nums) >= 2 and nums[1].isdigit():
        d["memory_max"] = int(nums[1])
    return d if "usage_usec" in d else None


def _docker_stats() -> dict:
    out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}"], capture_output=True,
                         text=True, timeout=60).stdout
    res = {}
    for ln in out.splitlines():
        name, cpu, mem = ln.split("|")
        if name.startswith("sc-m76-"):
            res[name] = {"cpu_percent": float(cpu.rstrip("%")), "mem_usage": mem}
    return res


class Sampler(threading.Thread):
    def __init__(self, pod_list: list[str], period: float = 10.0) -> None:
        super().__init__(daemon=True)
        self.pods, self.period, self.stop = pod_list, period, threading.Event()
        self.cg: dict[str, list[tuple[float, dict]]] = {p: [] for p in pod_list}
        self.docker: list[tuple[float, dict]] = []

    def run(self) -> None:
        while not self.stop.is_set():
            t = time.time()
            threads = []
            for p in self.pods:
                def one(p=p):
                    s = _cgroup(p)
                    if s:
                        self.cg[p].append((time.time(), s))
                th = threading.Thread(target=one)
                th.start()
                threads.append(th)
            try:
                self.docker.append((t, _docker_stats()))
            except (subprocess.SubprocessError, ValueError):
                pass
            for th in threads:
                th.join()
            self.stop.wait(max(0.0, self.period - (time.time() - t)))

    def summary(self) -> dict:
        out: dict = {"sample_period_s": self.period, "pods": {}, "nodes": {}}
        for p, series in self.cg.items():
            if len(series) < 2:
                continue
            cpu = []
            for (t0, a), (t1, b) in zip(series, series[1:]):
                cpu.append((b["usage_usec"] - a["usage_usec"]) / 1e6 / (t1 - t0))
            first, last = series[0][1], series[-1][1]
            mem = [s["memory_current"] for _, s in series if "memory_current" in s]
            out["pods"][p] = {
                "cpu_cores_mean": statistics.fmean(cpu), "cpu_cores_max": max(cpu), "cpu_cores_p95": sorted(cpu)[int(0.95 * (len(cpu) - 1))],
                "memory_bytes_mean": statistics.fmean(mem) if mem else None, "memory_bytes_max": max(mem) if mem else None,
                "memory_limit_bytes": last.get("memory_max"), "samples": len(series),
                "cpu_throttled_periods": last.get("nr_throttled", 0) - first.get("nr_throttled", 0),
                "cpu_periods": last.get("nr_periods", 0) - first.get("nr_periods", 0),
                "cpu_throttled_seconds": (last.get("throttled_usec", 0) - first.get("throttled_usec", 0)) / 1e6}
        names = {n for _, d in self.docker for n in d}
        for n in sorted(names):
            cpus = [d[n]["cpu_percent"] for _, d in self.docker if n in d]
            out["nodes"][n] = {"cpu_percent_mean": statistics.fmean(cpus), "cpu_percent_max": max(cpus),
                               "mem_usage_last": [d[n]["mem_usage"] for _, d in self.docker if n in d][-1], "samples": len(cpus)}
        return out


# ------------------------------------------------------------------------------------------------------ the generator

def pct(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def dist(vals: list[float]) -> dict:
    s = sorted(vals)
    ms = lambda x: None if x is None else round(x * 1000, 2)  # noqa: E731
    return {"n": len(s), "p50_ms": ms(pct(s, 0.50)), "p95_ms": ms(pct(s, 0.95)), "p99_ms": ms(pct(s, 0.99)),
            "p999_ms": ms(pct(s, 0.999)), "max_ms": ms(s[-1] if s else None), "mean_ms": ms(statistics.fmean(s) if s else None)}


class Gen:
    def __init__(self, tokens: dict[str, str], seeded_ids: list[str], rng: random.Random) -> None:
        self.tokens, self.seeded, self.rng = tokens, seeded_ids, rng
        self.run = uuid.uuid4().hex[:8]
        self.own: list[tuple[str, int]] = []          # (case_id, next step index) for cases that exist
        self.advanceable: collections.deque = collections.deque()
        self.n = 0
        self.records: list[tuple] = []                # (op, status|error, latency_from_intended, service_latency, t_rel)
        self.ops = [k for k, w in MIX for _ in range(w)]
        self.clients: list[httpx.AsyncClient] = []
        self.rr = 0
        self.max_lag = 0.0
        self.lags: list[float] = []

    def auth(self, role: str) -> dict:
        return {"Authorization": f"Bearer {self.tokens[role]}"}

    async def send(self, op: str, method: str, path: str, role: str, intended: float, t_origin: float, body: dict | None = None):
        sent = time.perf_counter()
        status: int | str
        try:
            self.rr += 1
            r = await self.clients[self.rr % len(self.clients)].request(method, path, json=body, headers=self.auth(role))
            status = r.status_code
        except Exception as exc:  # noqa: BLE001 - timeouts, resets: counted as errors by type
            status = type(exc).__name__
        done = time.perf_counter()
        self.records.append((op, status, done - intended, done - sent, intended - t_origin))
        return status

    async def fire(self, intended: float, t_origin: float) -> None:
        lag = time.perf_counter() - intended
        self.lags.append(lag)
        self.max_lag = max(self.max_lag, lag)
        op = self.rng.choice(self.ops)
        if op in ("post_event", "get_case_own", "notifications") and not self.own:
            op = "create_case"
        if op == "post_event" and not self.advanceable:
            op = "create_case"
        if op == "create_case":
            self.n += 1
            cid = str(uuid.uuid4())
            status = await self.send(op, "POST", "/cases", "lab", intended, t_origin,
                                     {"case_id": cid, "patient_ref": f"Patient/load-{self.run}-{self.n}",
                                      "result_ref": f"DiagnosticReport/load-{self.run}-{self.n}"})
            if status == 201:
                self.own.append((cid, 0))
                self.advanceable.append((cid, 0))
        elif op == "post_event":
            cid, step = self.advanceable.popleft()  # out of the queue while in flight: no two events race on one case
            etype, role = EVENT_STEPS[step]
            status = await self.send(op, "POST", f"/cases/{cid}/events", role, intended, t_origin,
                                     {"type": etype, "event_id": str(uuid.uuid4())})
            if status == 201 and step + 1 < len(EVENT_STEPS):
                self.advanceable.append((cid, step + 1))
        elif op == "get_case_seeded":
            await self.send(op, "GET", f"/cases/{self.rng.choice(self.seeded)}", "coord", intended, t_origin)
        elif op == "get_case_own":
            await self.send(op, "GET", f"/cases/{self.rng.choice(self.own)[0]}", "coord", intended, t_origin)
        elif op == "timeline":
            await self.send(op, "GET", f"/cases/{self.rng.choice(self.seeded)}/timeline", "coord", intended, t_origin)
        elif op == "notifications":
            await self.send(op, "GET", f"/cases/{self.rng.choice(self.own)[0]}/notifications", "coord", intended, t_origin)
        elif op == "worklist":
            await self.send(op, "GET", f"/cases?state={LIST_STATE}", "coord", intended, t_origin)

    async def stage(self, rate: float, warmup: float, duration: float) -> tuple[float, float]:
        n_clients = max(6, min(400, int(rate * 1.5)))
        """Issue requests at `rate`/s for warmup+duration seconds, then wait for stragglers. Returns (t_origin, t_end) perf_counter.
        The requests go round-robin over `n_clients` independent HTTP clients (each its own keep-alive connection pool): kube-proxy
        balances per TCP connection, so one shared pool would pin all traffic to the few pods its connections happened to reach."""
        self.clients = [httpx.AsyncClient(base_url=API_BASE, timeout=httpx.Timeout(30.0, connect=10.0),
                                          limits=httpx.Limits(max_connections=16, max_keepalive_connections=16, keepalive_expiry=4.0))
                        for _ in range(n_clients)]
        # keepalive_expiry 4 s: uvicorn closes an idle connection after 5 s and httpx's default expiry is also 5 s, so a client that
        # reuses a connection at ~5 s races the server's close ("server disconnected": a generator artefact, not an API error). With
        # n_clients = 1.5 x rate each client is used every ~1.5 s, so connections stay open for the whole stage: opening a TCP
        # connection per request through Docker Desktop's published port costs seconds of SYN retransmits at tens of connections/s
        self.records, self.lags, self.max_lag = [], [], 0.0
        tasks: set[asyncio.Task] = set()
        t0 = time.perf_counter() + 0.5
        total = warmup + duration
        interval = 1.0 / rate
        i = 0
        while True:
            now = time.perf_counter()
            if now - t0 >= total:
                break
            while t0 + i * interval <= now and i * interval < total:
                tk = asyncio.create_task(self.fire(t0 + i * interval, t0))
                tasks.add(tk)
                tk.add_done_callback(tasks.discard)
                i += 1
            await asyncio.sleep(max(0.0, min(t0 + i * interval - time.perf_counter(), 0.004)))
        issued = i
        if tasks:
            await asyncio.wait(tasks, timeout=45)
        unfinished = len([t for t in tasks if not t.done()])
        for t in tasks:
            t.cancel()
        for c in self.clients:
            await c.aclose()
        self.issued, self.unfinished = issued, unfinished
        return t0, time.perf_counter()


def summarise(gen: Gen, rate: float, warmup: float, duration: float) -> dict:
    rec = [r for r in gen.records if r[4] >= warmup]
    ok = lambda r: isinstance(r[1], int) and 200 <= r[1] < 300  # noqa: E731
    per_ep: dict[str, list] = collections.defaultdict(list)
    for r in rec:
        per_ep[ENDPOINT[r[0]]].append(r)
    errors = collections.Counter(str(r[1]) for r in rec if not ok(r))
    n_err = sum(errors.values())
    n = len(rec)
    per_ep_out = {}
    for ep, rs in sorted(per_ep.items()):
        per_ep_out[ep] = {"requests": len(rs), "errors": sum(1 for r in rs if not ok(r)),
                          "latency": dist([r[2] for r in rs if ok(r)]), "service_latency": dist([r[3] for r in rs if ok(r)])}
    per_op = {op: {"requests": len(rs), "latency": dist([r[2] for r in rs if ok(r)])}
              for op in MIX_OPS for rs in [[r for r in rec if r[0] == op]] if rs}
    overall = dist([r[2] for r in rec if ok(r)])
    # An erroring request is part of the experience: p99 over ALL requests, failures counted at their latency.
    overall_all = dist([r[2] for r in rec])
    lags = sorted(gen.lags)
    ratio = n_err / n if n else 1.0
    p99 = overall_all["p99_ms"]
    point = [r for r in rec if r[0] != "worklist"]
    point_all = dist([r[2] for r in point])
    p99_point = point_all["p99_ms"]
    return {
        "target_rate_per_s": rate, "warmup_s": warmup, "duration_s": duration, "issued_total": gen.issued, "unfinished_after_45s": gen.unfinished,
        "measured_requests": n, "achieved_rate_per_s": n / duration, "errors": n_err, "error_rate": ratio,
        "errors_by_status": dict(errors), "latency_2xx": overall, "latency_all_requests": overall_all,
        "per_endpoint": per_ep_out, "per_operation": per_op,
        "generator_lag_ms": {"p50": round(pct(lags, 0.5) * 1000, 2), "p99": round(pct(lags, 0.99) * 1000, 2), "max": round(gen.max_lag * 1000, 2)},
        "latency_all_requests_excluding_worklist": point_all,
        "pass": bool(p99 is not None and p99 < P99_LIMIT_S * 1000 and ratio < ERROR_LIMIT and gen.unfinished == 0),
        "pass_excluding_worklist": bool(p99_point is not None and p99_point < P99_LIMIT_S * 1000 and ratio < ERROR_LIMIT and gen.unfinished == 0),
        "criteria": f"p99 (all requests, from intended start) < {P99_LIMIT_S * 1000:.0f} ms and error rate < {ERROR_LIMIT * 100:.1f}%",
    }


MIX_OPS = [k for k, _ in MIX]


# ------------------------------------------------------------------------------------------------------ log analysis

ACCESS = re.compile(r'"(?P<m>[A-Z]+) (?P<p>/\S*) HTTP/1\.\d" (?P<s>\d{3})')


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def api_logs(api_pods: list[str], since: datetime) -> dict[str, list[tuple[float, str, str, int]]]:
    out = {}
    for p in api_pods:
        text = kubectl("logs", p, f"--since-time={since.strftime('%Y-%m-%dT%H:%M:%SZ')}", timeout=180)
        rows = []
        for ln in text.splitlines():
            if '"uvicorn.access"' not in ln:
                continue
            try:
                d = json.loads(ln)
            except ValueError:
                continue
            m = ACCESS.search(d.get("message", ""))
            if m:
                rows.append((_ts(d["ts"]), m["m"], m["p"], int(m["s"])))
        out[p] = rows
    return out


def request_distribution(logs: dict, t_from: float, t_to: float) -> dict:
    res = {}
    for pod, rows in logs.items():
        c: collections.Counter = collections.Counter()
        for t, m, path, s in rows:
            if t_from <= t <= t_to and path.startswith("/cases"):
                key = re.sub(r"/[0-9a-f-]{36}", "/{id}", path.split("?")[0])
                c[f"{m} {key}"] += 1
        res[pod] = {"total": sum(c.values()), "by_route": dict(c)}
    tot = sum(v["total"] for v in res.values()) or 1
    for v in res.values():
        v["share"] = v["total"] / tot
    return res


def scheduler_timings(logs: dict, t_from: float, t_to: float) -> dict:
    """Durations of the scheduler loop's sequential calls, from the merged API access log: the outbox drain returns just before
    the sweep starts, and the automation scan starts when the sweep returns, so the gap between consecutive access lines is the
    duration of the later call (plus ~ms of network)."""
    ev = sorted((t, path) for rows in logs.values() for t, m, path, s in rows if m == "POST" and path.startswith("/scheduler/") and s == 200)
    sweeps, autos, evids, periods = [], [], [], []
    last_sweep_end = None
    for i, (t, path) in enumerate(ev):
        if not (t_from <= t <= t_to) or i == 0:
            continue
        prev_t, prev_path = ev[i - 1]
        # heartbeats and lease renewals interleave: look back for the closest preceding call of the expected kind
        def prior(kind: str) -> float | None:
            for tt, pp in reversed(ev[:i]):
                if pp == kind:
                    return tt
            return None
        if path == "/scheduler/sweep":
            d = prior("/scheduler/outbox/drain")
            if d is not None and t - d < 600:
                sweeps.append(t - d)
            if last_sweep_end is not None:
                periods.append(t - last_sweep_end)
            last_sweep_end = t
        elif path == "/scheduler/automation-scan":
            d = prior("/scheduler/sweep")
            if d is not None and t - d < 600:
                autos.append(t - d)
        elif path == "/scheduler/evidence-scan":
            d = prior("/scheduler/automation-scan")
            if d is not None and t - d < 600:
                evids.append(t - d)
    f = lambda xs: {"n": len(xs), "mean_s": round(statistics.fmean(xs), 3), "p50_s": round(pct(sorted(xs), 0.5), 3),  # noqa: E731
                    "p95_s": round(pct(sorted(xs), 0.95), 3), "max_s": round(max(xs), 3)} if xs else {"n": 0}
    return {"sweep_duration": f(sweeps), "automation_scan_duration": f(autos), "evidence_scan_duration": f(evids),
            "sweep_to_sweep_period": f(periods), "interval_setting_s": SCHED_INTERVAL,
            "note": "sweep_duration = time between the outbox-drain and sweep access-log lines (the sweep runs between them); "
                    "period = gap between consecutive sweep completions (interval + all loop work)"}


# ------------------------------------------------------------------------------------------------------ driver

async def run_stage(rate: float, args, tokens: dict, seeded: list[str], api_pods: list[str], sched_pods: list[str],
                    pg_pod: list[str], rng: random.Random) -> dict:
    gen = Gen(tokens, seeded, rng)
    sampler = Sampler(api_pods + sched_pods + pg_pod, period=args.sample_period)
    since = datetime.now(UTC)
    sampler.start()
    t_wall0 = time.time() + 0.5
    t0, t_end = await gen.stage(rate, args.warmup, args.duration)
    sampler.stop.set()
    sampler.join(timeout=60)
    t_wall_end = time.time()
    measured_from = t_wall0 + args.warmup
    out = summarise(gen, rate, args.warmup, args.duration)
    if args.dump_records:
        with open(args.dump_records, "w") as fh:
            for op, status, lat, svc, t_rel in gen.records:
                fh.write(json.dumps([round(t_rel, 3), op, status, round(lat, 4), round(svc, 4)]) + "\n")
    out["started_at"] = datetime.fromtimestamp(t_wall0, UTC).isoformat()
    out["finished_at"] = datetime.fromtimestamp(t_wall_end, UTC).isoformat()
    out["resources"] = sampler.summary()
    logs = await asyncio.to_thread(api_logs, api_pods, since)
    out["request_distribution_per_pod"] = request_distribution(logs, measured_from, t_wall0 + args.warmup + args.duration)
    out["scheduler_during_stage"] = scheduler_timings(logs, measured_from, t_wall0 + args.warmup + args.duration)
    out["cases_created_by_run"] = gen.n
    return out


def sample_seeded_ids(dsn: str, n: int) -> list[str]:
    with psycopg.connect(dsn) as conn:
        conn.execute("SET max_parallel_workers_per_gather = 0")
        total = conn.execute("SELECT reltuples::bigint FROM pg_class WHERE relname = 'cases'").fetchone()[0]
        pctg = max(min(100.0 * n * 3 / max(total, 1), 100.0), 0.001)
        rows = conn.execute(f"SELECT id::text FROM cases TABLESAMPLE SYSTEM ({pctg}) LIMIT %s", (n,)).fetchall()
    return [r[0] for r in rows]


def probe_worklist(tokens: dict, api_pods: list[str]) -> dict:
    """ONE unbounded worklist read (`GET /cases?state=...` on a state with tens of thousands of cases), to see what the route does
    at this population. Reports latency, response size, the status, and API pod memory/restarts before and after."""
    out = {}
    with psycopg.connect(owner_dsn()) as conn:
        counts = dict(conn.execute("SELECT current_state, count(*) FROM cases GROUP BY 1").fetchall())
    state = max(counts, key=counts.get)
    before = {p: _cgroup(p) for p in api_pods}
    restarts_before = json.loads(kubectl("get", "pods", "-l", "app.kubernetes.io/name=api", "-o", "json"))
    rb = {p["metadata"]["name"]: p["status"]["containerStatuses"][0]["restartCount"] for p in restarts_before["items"]}
    t = time.perf_counter()
    try:
        r = httpx.get(f"{API_BASE}/cases?state={state}", headers={"Authorization": f"Bearer {tokens['coord']}"}, timeout=300)
        out.update(status=r.status_code, response_bytes=len(r.content), rows=len(r.json()) if r.status_code == 200 else None)
    except Exception as exc:  # noqa: BLE001
        out.update(status=type(exc).__name__)
    out["seconds"] = time.perf_counter() - t
    out.update(state=state, cases_in_state=counts[state], pod_cgroup_before={p: (v or {}).get("memory_current") for p, v in before.items()})
    time.sleep(2)
    after = json.loads(kubectl("get", "pods", "-l", "app.kubernetes.io/name=api", "-o", "json"))
    out["restarts_before"] = rb
    out["restarts_after"] = {p["metadata"]["name"]: p["status"]["containerStatuses"][0]["restartCount"] for p in after["items"]}
    out["last_termination"] = {p["metadata"]["name"]: p["status"]["containerStatuses"][0].get("lastState", {}).get("terminated", {}).get("reason")
                               for p in after["items"]}
    return out


async def amain(args) -> int:
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dsn = await asyncio.to_thread(owner_dsn)
    tokens = {"lab": mint("lab", "loadgen-lab"), "coord": mint("coordinator", "loadgen"),
              "pco": mint("primary_care_owner", "Dr. Layla Haddad", sub="Dr. Layla Haddad")}
    api_pods, sched_pods, pg_pod = pods("app.kubernetes.io/name=api"), pods("app.kubernetes.io/name=scheduler"), ["postgres-0"]
    seeded = await asyncio.to_thread(sample_seeded_ids, dsn, args.sample_ids)
    print(f"sampled {len(seeded)} seeded case ids; api pods {api_pods}", flush=True)
    rng = random.Random(args.seed)
    doc: dict = load(out_path)
    doc.update(schema_version=1, tool="tools/k8s/api_load.py", method=METHOD, git=git_info(), generated_at=datetime.now(UTC).isoformat(),
               machine_spec=json.loads(subprocess.run(["bash", str(ROOT / "tools/k8s/machine_spec.sh")], capture_output=True, text=True, check=True).stdout),
               chart_values=chart_values(), cluster_overrides=cluster_overrides(), mix_weights=dict(MIX), list_state=LIST_STATE, note_same_host="the load generator runs on the same host as the cluster",
               population_before=await asyncio.to_thread(population, dsn), pass_criteria=f"p99 < {P99_LIMIT_S * 1000:.0f} ms (all requests, from intended start) and error rate < {ERROR_LIMIT * 100:.1f}%")
    doc.setdefault("stages", [])
    doc["started_at"] = doc.get("started_at") or datetime.now(UTC).isoformat()
    rates = [float(r) for r in args.rates.split(",") if r]
    results: list[dict] = []

    async def go(rate: float) -> dict:
        print(f"== stage: {rate:g} req/s, warm-up {args.warmup:g}s + {args.duration:g}s ==", flush=True)
        st = await run_stage(rate, args, tokens, seeded, api_pods, sched_pods, pg_pod, rng)
        st["label"] = args.label
        doc["stages"] = [s for s in doc["stages"] if (s["target_rate_per_s"], s.get("label")) != (rate, args.label)] + [st]
        doc["stages"].sort(key=lambda s: (s.get("label") or "", s["target_rate_per_s"]))
        doc["population_after"] = await asyncio.to_thread(population, dsn)
        doc["finished_at"] = datetime.now(UTC).isoformat()
        save(out_path, doc)
        la = st["latency_all_requests"]
        print(f"   achieved {st['achieved_rate_per_s']:.1f}/s, p50 {la['p50_ms']} p95 {la['p95_ms']} p99 {la['p99_ms']} max {la['max_ms']} ms, "
              f"errors {st['errors']} {st['errors_by_status']}, lag max {st['generator_lag_ms']['max']} ms -> {'PASS' if st['pass'] else 'FAIL'} "
              f"(p99 excl. worklist {st['latency_all_requests_excluding_worklist']['p99_ms']} ms: {'PASS' if st['pass_excluding_worklist'] else 'FAIL'}); "
              f"sched sweep {st['scheduler_during_stage']['sweep_duration']}", flush=True)
        return st

    for r in rates:
        results.append(await go(r))
    if args.search_start:
        rate = args.search_start
        while rate <= args.search_max:
            st = await go(rate)
            results.append(st)
            if not st["pass_excluding_worklist"]:  # the point operations fail first only if the worklist is not the limit
                break
            rate += args.search_step
    stages = [s for s in doc["stages"] if s.get("label") == args.label]
    summ = {}
    for key in ("pass", "pass_excluding_worklist"):
        passing = [s["target_rate_per_s"] for s in stages if s[key]]
        failing = [s["target_rate_per_s"] for s in stages if not s[key]]
        summ[key] = {"max_passing_rate_per_s": max(passing) if passing else None, "lowest_failing_rate_per_s": min(failing) if failing else None}
    doc.setdefault("summary", {})[args.label] = summ
    if args.probe_worklist:
        print("== unbounded worklist probe ==", flush=True)
        doc["unbounded_worklist_probe"] = await asyncio.to_thread(probe_worklist, tokens, api_pods)
        print(json.dumps(doc["unbounded_worklist_probe"], default=str)[:600], flush=True)
    save(out_path, doc)
    print(f"wrote {out_path}")
    return 0


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, indent=2, default=str) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rates", default="50,100", help="comma-separated arrival rates (req/s), one stage each")
    ap.add_argument("--duration", type=float, default=300.0, help="measured seconds per stage (>= 300 for the results)")
    ap.add_argument("--warmup", type=float, default=15.0)
    ap.add_argument("--search-start", type=float, default=0.0, help="then step up from this rate until a stage fails")
    ap.add_argument("--search-step", type=float, default=50.0)
    ap.add_argument("--search-max", type=float, default=1000.0)
    ap.add_argument("--sample-ids", type=int, default=5000, help="seeded case ids to read from")
    ap.add_argument("--sample-period", type=float, default=10.0)
    ap.add_argument("--label", default="main")
    ap.add_argument("--seed", type=int, default=7601)
    ap.add_argument("--probe-worklist", action="store_true")
    ap.add_argument("--dump-records", default=None, help="write every request (t_rel, op, status, latency, service) as JSON lines (debugging)")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    return asyncio.run(amain(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
