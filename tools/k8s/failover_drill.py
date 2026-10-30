#!/usr/bin/env python3
"""Failover drill for the kind cluster `sc-m76` (M7-6 scalability evidence).

Kills the scheduler LEADER pod and the API pod that holds the `api-background` lease at the same instant, while a seeded
workload keeps the scheduler sweeping real work and an API client keeps writing and reading, then measures how long the
cluster takes to recover and proves from the database that nothing was lost or doubled. Repeatable:

    .venv/bin/python tools/k8s/failover_drill.py --runs 5 --graceful-runs 3 --out docs/results/scale/failover.json

Per iteration (see METHOD below for the exact definitions that are written into the output):
  1. wait until the cluster is healthy (3 api Ready, 2 schedulers, leases settled);
  2. seed N open cases straight into the `closure` database whose breaches / escalation rungs fall due spread over a window
     (they are fully valid event logs, built with the engine's own planner, so the sweep treats them like any other case);
  3. start the client (steady writes with client-chosen ids and reads, retrying the SAME request on connection error / 5xx),
     the lease and heartbeat poller (every ~150 ms, database clock) and `kubectl logs -f` streams of every scheduler and api pod;
  4. at the kill time delete the scheduler leader pod and the api-background leader pod in one `kubectl delete` (force,
     `--grace-period=0` by default; `--graceful-runs` repeats with an ordinary delete);
  5. wait out the window, then prove: no duplicate / lost events, every acknowledged request exactly once, every log replays,
     nothing outstanding for the planner, fencing tokens strictly increasing, no stale-token write.

Writes to the cluster only: the pod deletions, the seeded cases and client cases in the `closure` database (append-only event
log, so they stay), and - only when the working-day calendar would make "due in the next minutes" impossible (a weekend day) -
a temporary override of the calendar ConfigMap that is restored at the end. Secrets are read with kubectl into memory and
never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import collections
import json
import math
import os
import random
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import psycopg
import yaml
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))

from app.calendar import calendar_from_dict, load_calendar  # noqa: E402
from app.events import CASE_CREATED, Event, replay  # noqa: E402
from app.pathway import load_pathway  # noqa: E402
from app.state_machine import replay_model  # noqa: E402
from app.sweep import NEVER, due_at_for, next_due_at, plan_case, timing_fingerprint  # noqa: E402

CTX = "kind-sc-m76"
NS = "closure"
REL = "closure"
API_BASE = "http://127.0.0.1:18376"
PG_HOSTPORT = "127.0.0.1:15476"
SCHEDULER_ACTOR = "system:scheduler"
DRILL_NS = uuid.UUID("0d1f5a3e-6c0b-4f6a-9b1e-3c7d2e8a4b10")
SEED_STATE = "patient_acknowledged"       # SLA book_confirmatory; not an intake / outreach / evidence state, so no scan touches it
ARM_RUNGS = {"A": 0, "B": 3, "C": 7}      # extra working days after the breach at which the drilled trigger falls due
ARM_WEIGHTS = {"A": 0.5, "B": 0.3, "C": 0.2}
ARM_TRIGGER = {"A": ("mark_overdue", None), "B": ("escalate", "supervisor"), "C": ("escalate", "coordinator")}

METHOD = (
    "Each run: (1) cluster healthy (3 api Ready, 2 scheduler pods, no lease change for 30 s). (2) N open cases are inserted "
    "directly into the `closure` database as valid event logs in state patient_acknowledged (SLA book_confirmatory = 5 working "
    "days); their creation times are solved with the engine's own calendar so that a trigger falls due at a time drawn "
    "uniformly over the drill window, in three arms: A the state breach (mark_overdue + reminder rung), B the supervisor rung "
    "(breach and reminder already emitted earlier), C the coordinator rung (the first two rungs already emitted). So the sweep "
    "has new work to do continuously during the whole window. (3) A client issues, at a constant ~1 new case flow per second, "
    "create_case (client-chosen case_id), accept_ownership, contact_patient, patient_acknowledge (client-chosen event_id) and "
    "GET /cases/{id}, through the NodePort 127.0.0.1:18376 (kube-proxy spreads over the api pods); on a connection error, "
    "timeout or HTTP 5xx it retries the same request (same id) after 0.25 s, up to 90 s. (4) t0 = the instant the "
    "`kubectl delete pod <scheduler leader> <api-background leader>` command is issued (force: --grace-period=0 --force "
    "--wait=false, i.e. no graceful lease release; graceful: ordinary delete with the 30 s grace period, the processes release "
    "their leases on SIGTERM; hard: both containers are SIGKILLed through the node's runtime (crictl stop --timeout 0) and then the pods are force-deleted, t0 = the instant the SIGKILL commands were issued, so no process can release a lease; note that a plain --grace-period=0 delete still gives a container ~2 s between SIGTERM and SIGKILL, in which a process may release its lease: every run records lease_released_by_killed_holder). Leader identity comes from the leader_lease table (scheduler holder = pod name; the "
    "api-background holder is a random id mapped to its pod through the 'is now the leader' log line). (5) A poller reads "
    "leader_lease and the scheduler row of heartbeats every ~150 ms with the DATABASE clock (clock_timestamp()); t0 is "
    "converted to the database clock with the median host-minus-database offset of the run. time_to_new_leader_s = first poll "
    "at which the lease row names a holder other than the killed one with a higher fencing token, minus t0 (a takeover writes "
    "a fresh expiry, so that row is unexpired by construction); also reported: seconds since the killed holder's last renewal "
    "(its last observed expires_at minus the TTL). Resolution is the poll interval plus one query round trip. (6) Sweep "
    "evidence: the scheduler posts a heartbeat with sweeping=false at the end of every loop (after the sweep and the scans); "
    "the poller records every change of that row. sweep_gap_s = completion of the first sweep by a different holder than the "
    "killed leader minus completion of the killed leader's last sweep (the killed process can still finish a loop for a moment "
    "after t0: kubelet kill latency; sweep_gap_around_t0_s uses the last completion at or before t0 and the first after it "
    "instead, and killed_leader_last_sweep_after_t0_s says how long the killed leader kept sweeping). missed_sweeps = max(0, floor(sweep_gap_s / scheduler_interval_s) - 1): the number of whole scheduler "
    "intervals inside the gap in which a sweep should have started but did not, beyond the one interval that elapses between "
    "two healthy sweeps anyway. missed_sweeps_vs_baseline uses the median healthy gap of the run (interval + sweep + scans) "
    "instead of the nominal interval. The same gap is recomputed from the scheduler pods' log lines ('sweep sim_time=...') as a "
    "cross-check. (7) Late, not lost: after the window the expected scheduler events of every seeded case are computed by "
    "running the planner (app.sweep.plan_case) over the seeded log up to the verification time, until nothing more is planned; "
    "every expected event id must exist in the database (losses = expected and missing; unexpected = present and not expected), "
    "and re-planning every case from its STORED log must plan nothing (outstanding = 0). lateness = events.created_at "
    "(database insert time) minus the instant the trigger fell due. (8) Duplicates: duplicate event_id, duplicate (case, "
    "type, state-or-milestone, rung) scheduler events, duplicate (case, type) client events and duplicate genesis, over every "
    "seeded and client case; every acknowledged client request exists exactly once; every request that needed a retry produced "
    "exactly one event; every case replays (contiguous seq, replay_model succeeds, replay() state equals cases.current_state). "
    "(9) Fencing: every (holder, token) the poller saw for each lease; tokens must increase strictly; 409s are counted from the "
    "scheduler logs and the api access logs (organic = before the drill's own probes; 'sweep stopped: stale leader' log lines are "
    "sweeps the in-transaction fence stopped mid-way); a stale-token probe (killed holder, killed token) is sent to POST "
    "/scheduler/sweep and /scheduler/heartbeat after recovery and must get 409. The write-time fence (M7-1/F10: the store "
    "re-checks the lease with SELECT ... FOR SHARE inside the write transaction) is exercised directly: a due scheduler event "
    "is appended to a probe case through the real PostgresEventStore with three stale fences (killed holder+token, current holder "
    "+ old token, killed holder + current token), each must raise StaleLeader and write zero rows, and a control append with the "
    "current fence must succeed. The event log does NOT record the holder or token of the "
    "scheduler that wrote an event, so 'no write from a deposed leader' is also shown by timing: every scheduler event's database "
    "created_at is placed inside the validity window of one observed lease token (acquired-at to last observed expiry) and the "
    "events outside every window are counted (strict, and with a tolerance of the longest observed loop, because the fence is "
    "checked when a sweep request ENTERS the API, not when it commits). (10) Client impact: attempts, error attempts "
    "(connection error, timeout, 5xx), retries, ops that never succeeded, maximum latency inside the disruption window "
    "[t0, time all 3 api pods are Ready again, polled with kubectl every ~0.5 s]. Pod log timestamps are taken to be in the same "
    "clock domain as the database (same Docker VM)."
)


# ---------------------------------------------------------------------------------------------------------------------
# kubectl / secrets
# ---------------------------------------------------------------------------------------------------------------------

def kubectl(*args: str, check: bool = True, timeout: float = 90, namespace: bool = True) -> subprocess.CompletedProcess:
    cmd = ["kubectl", "--context", CTX] + (["-n", NS] if namespace else []) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=timeout)


def secret_value(name: str, key: str) -> str:
    out = kubectl("get", "secret", name, "-o", f"jsonpath={{.data.{key}}}").stdout
    return base64.b64decode(out).decode()


def owner_dsn() -> str:
    return f"postgresql://closure_owner:{secret_value('sc-postgres', 'password')}@{PG_HOSTPORT}/closure?sslmode=disable"


def mint(role: str, name: str, ttl: int = 21600, sub: str | None = None) -> str:
    py = str(ROOT / ".venv/bin/python")
    out = subprocess.run([py, str(ROOT / "tools/k8s/mint_token.py"), "--role", role, "--name", name, "--ttl", str(ttl)]
                         + (["--sub", sub] if sub else []),
                         capture_output=True, text=True, check=True, timeout=60).stdout
    return out.strip()


PROXY: dict = {"base": None, "proc": None}


def start_proxy() -> None:
    """`kubectl proxy` on a free port: pod lists, logs and deletes go through plain HTTP to the API server, so the drill does not
    spawn a kubectl process per question (on a loaded host that start-up costs seconds and would blur t0)."""
    proc = subprocess.Popen(["kubectl", "--context", CTX, "proxy", "--port=0"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    line = proc.stdout.readline()
    m = re.search(r"127\.0\.0\.1:(\d+)", line)
    if not m:
        proc.kill()
        raise SystemExit(f"kubectl proxy did not start: {line!r}")
    PROXY.update(base=f"http://127.0.0.1:{m.group(1)}", proc=proc)


def stop_proxy() -> None:
    if PROXY["proc"] is not None:
        PROXY["proc"].terminate()
        PROXY.update(base=None, proc=None)


def k8s_get(path: str, **params) -> httpx.Response:
    return httpx.get(PROXY["base"] + path, params=params, timeout=30)


def list_pods(label: str) -> list[dict]:
    items = k8s_get(f"/api/v1/namespaces/{NS}/pods", labelSelector=f"app.kubernetes.io/name={label}").json()["items"]
    out = []
    for it in items:
        conds = {c["type"]: c["status"] for c in it["status"].get("conditions", [])}
        out.append({
            "name": it["metadata"]["name"],
            "ready": conds.get("Ready") == "True",
            "terminating": it["metadata"].get("deletionTimestamp") is not None,
            "phase": it["status"].get("phase"),
            "restarts": sum(c.get("restartCount", 0) for c in it["status"].get("containerStatuses", [])),
            "created": it["metadata"]["creationTimestamp"],
            "node": it["spec"].get("nodeName"),
        })
    return out


def api_holder_pods(pods: list[dict]) -> dict[str, str]:
    """holder id -> pod name for every api pod that logged 'lease api-background: <holder> is now the leader'."""
    mapping: dict[str, str] = {}
    for p in pods:
        if p["terminating"]:
            continue
        try:
            logs = k8s_get(f"/api/v1/namespaces/{NS}/pods/{p['name']}/log").text
        except httpx.HTTPError:
            continue
        for m in re.finditer(r"lease api-background: (api-[0-9a-f]+) is now the leader", logs):
            mapping[m.group(1)] = p["name"]
    return mapping


def vm_pressure() -> dict:
    """Memory, swap and load of the Docker VM (visible in /proc/meminfo inside any kind node) and of the host: the drill runs on
    a laptop shared with other stacks, and a starved VM makes probes and queries time out, so every run records it."""
    out = {"host_load_1m": os.getloadavg()[0]}
    try:
        txt = subprocess.run(["docker", "exec", "sc-m76-worker", "cat", "/proc/meminfo"], capture_output=True, text=True, timeout=30).stdout
        mi = {m.group(1): int(m.group(2)) for m in re.finditer(r"^(\w+):\s+(\d+) kB", txt, re.M)}
        out.update(vm_mem_total_mb=mi["MemTotal"] // 1024, vm_mem_available_mb=mi["MemAvailable"] // 1024,
                   vm_swap_total_mb=mi["SwapTotal"] // 1024, vm_swap_used_mb=(mi["SwapTotal"] - mi["SwapFree"]) // 1024)
        load = subprocess.run(["docker", "exec", "sc-m76-worker", "cat", "/proc/loadavg"], capture_output=True, text=True, timeout=30).stdout.split()
        out["vm_load_1m"] = float(load[0])
        stats = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.CPUPerc}}"], capture_output=True, text=True, timeout=60).stdout
        out["docker_containers_total"] = len(stats.splitlines())
        out["docker_cpu_percent_sum_all_containers"] = round(sum(float(l.split()[1].rstrip("%")) for l in stats.splitlines() if len(l.split()) == 2), 1)
        out["docker_cpu_percent_sum_sc_m76_nodes"] = round(sum(float(l.split()[1].rstrip("%")) for l in stats.splitlines() if l.startswith("sc-m76-")), 1)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def parse_ts(s: str) -> float:
    s = s.strip().replace("Z", "+00:00")
    m = re.match(r"(.*?\.\d{1,6})\d*(\+00:00)$", s)
    if m:
        s = m.group(1) + m.group(2)
    return datetime.fromisoformat(s).timestamp()


# ---------------------------------------------------------------------------------------------------------------------
# calendar (a due time can only fall on a working day)
# ---------------------------------------------------------------------------------------------------------------------

def configmap_calendar_text() -> str:
    return kubectl("get", "configmap", "screening-closure-config", "-o", r"jsonpath={.data.calendar\.yaml}").stdout


def patch_calendar(text: str) -> None:
    patch = json.dumps({"data": {"calendar.yaml": text}})
    kubectl("patch", "configmap", "screening-closure-config", "--type", "merge", "-p", patch)
    kubectl("rollout", "restart", f"deployment/{REL}-api")
    kubectl("rollout", "status", f"deployment/{REL}-api", "--timeout=300s", timeout=330)


def ensure_calendar(allow_override: bool) -> dict:
    """Due instants are always on a working day (api/app/calendar.py), so on a weekend day (Dubai) no breach can fall due in
    the next minutes. If that is the case, switch the cluster's calendar to one with another weekend day for the drill."""
    text = configmap_calendar_text()
    raw = yaml.safe_load(text)
    cal = calendar_from_dict(raw)
    now = datetime.now(UTC)
    info = {"original_weekend": raw.get("weekend"), "timezone": raw.get("timezone", "Asia/Dubai"), "overridden": False, "reason": None}
    if cal.is_working_day(now) and cal.is_working_day(now + timedelta(hours=4)):
        info["effective_weekend"] = raw.get("weekend")
        return info
    if not allow_override:
        raise SystemExit("today is not a working day in the cluster calendar, so no SLA can fall due in the next minutes; "
                         "run on a working day or allow the calendar override")
    local = now.astimezone(cal.tz)
    names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    day = names[(local.weekday() + 3) % 7]
    new = re.sub(r"(?m)^weekend:.*$", f"weekend: [{day}]", text)
    patch_calendar(new)
    info.update(overridden=True, effective_weekend=[day],
                reason=f"{local:%A %Y-%m-%d %H:%M} {info['timezone']} is a non-working day in the cluster calendar; SLA due "
                       f"instants always land on working days, so the ConfigMap weekend was set to [{day}] for the drill "
                       f"and restored afterwards (api pods rolled both times)")
    return info


def restore_calendar() -> None:
    patch_calendar((ROOT / "config/calendar.yaml").read_text())


# ---------------------------------------------------------------------------------------------------------------------
# poller: leases + scheduler heartbeat, database clock
# ---------------------------------------------------------------------------------------------------------------------

POLL_SQL = """
SELECT (SELECT json_agg(json_build_object('name', name, 'holder', holder, 'token', token,
                                           'exp', extract(epoch from expires_at))) FROM leader_lease),
       (SELECT json_build_object('beat', extract(epoch from beat_at), 'info', info) FROM heartbeats WHERE service = 'scheduler'),
       extract(epoch from clock_timestamp())::float8
"""


class Poller(threading.Thread):
    def __init__(self, dsn: str, interval: float = 0.15) -> None:
        super().__init__(name="lease-poller", daemon=True)
        self.dsn, self.interval = dsn, interval
        self.stop_event = threading.Event()
        self.latest: dict | None = None
        self.lease_events: list[dict] = []
        self.lease_last_exp: dict[tuple[str, int], float] = {}   # greatest expires_at seen for a (lease, token)
        self.lease_final_exp: dict[tuple[str, int], float] = {}  # latest expires_at seen (lower than the greatest = released early)
        self.beats: list[dict] = []
        self.offsets: list[float] = []
        self.samples = 0
        self.errors = 0
        self.max_rtt = 0.0
        self._last_lease: dict[str, tuple] = {}
        self._last_beat: float | None = None

    def run(self) -> None:
        conn = None
        while not self.stop_event.is_set():
            try:
                if conn is None:
                    conn = psycopg.connect(self.dsn, autocommit=True, connect_timeout=5)
                h0 = time.time()
                leases, beat, db_now = conn.execute(POLL_SQL).fetchone()
                h1 = time.time()
            except Exception:  # noqa: BLE001 - keep polling; the gap is recorded
                self.errors += 1
                try:
                    conn and conn.close()
                except Exception:  # noqa: BLE001
                    pass
                conn = None
                time.sleep(0.5)
                continue
            self.samples += 1
            self.max_rtt = max(self.max_rtt, h1 - h0)
            self.offsets.append(db_now - (h0 + h1) / 2)
            cur = {}
            for row in leases or []:
                cur[row["name"]] = (row["holder"], int(row["token"]), float(row["exp"]))
                key = (row["name"], int(row["token"]))
                self.lease_last_exp[key] = max(self.lease_last_exp.get(key, 0.0), float(row["exp"]))
                self.lease_final_exp[key] = float(row["exp"])
                prev = self._last_lease.get(row["name"])
                if prev is None or (prev[0], prev[1]) != (row["holder"], int(row["token"])):
                    self.lease_events.append({"db_t": db_now, "name": row["name"], "holder": row["holder"],
                                              "token": int(row["token"]), "first_expires_at": float(row["exp"]),
                                              "first_sample": prev is None})
                self._last_lease[row["name"]] = (row["holder"], int(row["token"]), float(row["exp"]))
            if beat and beat["beat"] != self._last_beat:
                self._last_beat = beat["beat"]
                info = beat["info"] or {}
                self.beats.append({"beat_at": beat["beat"], "seen_db": db_now, "sweeping": info.get("sweeping"),
                                   "sweep_ok": info.get("sweep_ok"), "role": info.get("role"), "holder": info.get("holder"),
                                   "token": info.get("fencing_token"), "sim_time": info.get("sim_time")})
            self.latest = {"db_now": db_now, "leases": cur, "host": (h0 + h1) / 2}
            self.stop_event.wait(self.interval)
        try:
            conn and conn.close()
        except Exception:  # noqa: BLE001
            pass

    def lease_events_for(self, name: str) -> list[dict]:
        return [e for e in self.lease_events if e["name"] == name]

    def offset(self) -> float:
        """database clock minus host clock (median over the run)"""
        return statistics.median(self.offsets) if self.offsets else 0.0

    def stop(self) -> None:
        self.stop_event.set()
        self.join(timeout=10)


# ---------------------------------------------------------------------------------------------------------------------
# log streams
# ---------------------------------------------------------------------------------------------------------------------

class LogStreams(threading.Thread):
    """Follows the logs (with timestamps) of every scheduler and api pod that exists during the run, replacement pods included
    (a force-deleted pod's logs are gone afterwards, so they must be captured while it lives)."""

    def __init__(self, outdir: Path) -> None:
        super().__init__(name="log-streams", daemon=True)
        self.outdir = outdir
        self.files: dict[str, Path] = {}
        self.threads: dict[str, threading.Thread] = {}
        self.stop_event = threading.Event()

    def _follow(self, pod: str, path: Path) -> None:
        try:
            with open(path, "w") as fh, httpx.stream(
                    "GET", PROXY["base"] + f"/api/v1/namespaces/{NS}/pods/{pod}/log",
                    params={"follow": "true", "timestamps": "true", "sinceSeconds": "180"}, timeout=httpx.Timeout(10, read=None)) as r:
                for line in r.iter_lines():
                    fh.write(line + "\n")
                    fh.flush()
                    if self.stop_event.is_set():
                        break
        except Exception:  # noqa: BLE001 - the pod went away: the file holds what it logged until then
            pass

    def run(self) -> None:
        while not self.stop_event.is_set():
            for label in ("scheduler", "api"):
                try:
                    pods = list_pods(label)
                except Exception:  # noqa: BLE001
                    continue
                for p in pods:
                    if p["name"] in self.threads or p["phase"] != "Running":
                        continue
                    path = self.outdir / f"{label}__{p['name']}.log"
                    self.files[p["name"]] = path
                    th = threading.Thread(target=self._follow, args=(p["name"], path), daemon=True)
                    th.start()
                    self.threads[p["name"]] = th
            self.stop_event.wait(1.0)

    def stop(self) -> None:
        self.stop_event.set()
        self.join(timeout=10)
        for th in self.threads.values():
            th.join(timeout=3)  # a stream still open is a daemon thread; the file is complete up to its last flushed line

    def lines(self, label: str) -> list[tuple[str, float, str]]:
        out = []
        for pod, path in self.files.items():
            if not path.name.startswith(label + "__"):
                continue
            for line in path.read_text(errors="replace").splitlines():
                head, _, rest = line.partition(" ")
                try:
                    out.append((pod, parse_ts(head), rest))
                except ValueError:
                    continue
        return sorted(out, key=lambda x: x[1])


# ---------------------------------------------------------------------------------------------------------------------
# seeding
# ---------------------------------------------------------------------------------------------------------------------

def enter_time_for_due(calendar, working_days: int, target: datetime) -> datetime:
    """A state-entry instant whose `working_days` working days later is `target` (same solver as tools/load/seed_cases.py)."""
    enter = target - timedelta(days=working_days * 7 / 5)
    for _ in range(6):
        due = calendar.add_working_days(enter, working_days)
        enter += target - due
    return enter


def chain_to_seed_state(pathway) -> list[tuple[str, str]]:
    actors = {"acknowledge": "primary_care_owner:pco-1", "accept_ownership": "primary_care_owner:pco-1",
              "contact_patient": "primary_care_owner:pco-1", "patient_acknowledge": "patient:p"}
    chain = []
    for t in pathway.main_transitions:
        chain.append((t.event, actors[t.event]))
        if t.to_state == SEED_STATE:
            return chain
    raise SystemExit(f"{SEED_STATE} is not on the main chain")


def build_seed(pathway, calendar, run_ns: uuid.UUID, n_cases: int, base: datetime, window: float, rng: random.Random):
    sla_days = pathway.sla_for_state(SEED_STATE)[1]
    chain = chain_to_seed_state(pathway)
    basis = timing_fingerprint(pathway, calendar)
    arms = list(ARM_WEIGHTS)
    cases, rows_cases, rows_events = [], [], []
    for i in range(n_cases):
        arm = rng.choices(arms, [ARM_WEIGHTS[a] for a in arms])[0]
        k = ARM_RUNGS[arm]
        target = base + timedelta(seconds=rng.uniform(5, window))
        enter = enter_time_for_due(calendar, sla_days + k, target)
        case_id = uuid.uuid5(run_ns, f"seed|{i}")
        times = [enter - timedelta(minutes=45 * (len(chain) - 1 - j)) for j in range(len(chain))]
        events = [Event(case_id, CASE_CREATED, times[0] - timedelta(minutes=45), "system:intake", {},
                        uuid.uuid5(run_ns, f"seed|{i}|created"))]
        for (etype, actor), at in zip(chain, times):
            events.append(Event(case_id, etype, at, actor, {}, uuid.uuid5(run_ns, f"seed|{i}|{etype}")))
        model = replay_model(events, pathway)
        due = due_at_for(model, pathway, calendar)
        trigger_at = calendar.add_working_days(due, k).astimezone(UTC)
        assert abs((trigger_at - target).total_seconds()) < 0.01, (arm, trigger_at, target)
        if arm in ("B", "C"):
            pre = (due + timedelta(minutes=1)) if arm == "B" else (calendar.add_working_days(due, ARM_RUNGS["B"]) + timedelta(minutes=1))
            events.extend(plan_case(case_id, model, pathway, calendar, pre.astimezone(UTC)))
            model = replay_model(events, pathway)
        if i < 10:  # the solver is exact: nothing is planned a second before the trigger, something a second after
            assert plan_case(case_id, model, pathway, calendar, trigger_at - timedelta(seconds=1)) == [], (arm, i)
            assert plan_case(case_id, model, pathway, calendar, trigger_at + timedelta(seconds=1)) != [], (arm, i)
        nd = next_due_at(model, pathway, calendar)
        rows_cases.append((case_id, pathway.name, model.state, events[0].at, events[-1].at, NEVER if nd is None else nd, basis))
        for seq, ev in enumerate(events, start=1):
            rows_events.append((ev.event_id, case_id, seq, ev.type, ev.at, ev.actor, Jsonb(ev.payload)))
        cases.append({"case_id": case_id, "arm": arm, "trigger_at": trigger_at, "seed_events": events})
    return cases, rows_cases, rows_events


def insert_seed(dsn: str, rows_cases, rows_events) -> None:
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            with cur.copy("COPY cases (id, pathway, current_state, created_at, updated_at, next_due_at, next_due_basis) FROM STDIN") as cp:
                for row in rows_cases:
                    cp.write_row(row)
            with cur.copy("COPY events (event_id, case_id, seq, type, at, actor, payload) FROM STDIN") as cp:
                for row in rows_events:
                    cp.write_row(row)
        conn.commit()


# ---------------------------------------------------------------------------------------------------------------------
# client workload
# ---------------------------------------------------------------------------------------------------------------------

class Workload:
    RETRY_DEADLINE = 90.0
    RETRY_SLEEP = 0.25

    def __init__(self, run_ns: uuid.UUID, tokens: dict[str, str], rate_hz: float) -> None:
        self.run_ns, self.tokens, self.rate = run_ns, tokens, rate_hz
        self.attempts: list[dict] = []
        self.ops: dict[str, dict] = {}
        self.stopping = False
        self.flows_started = 0
        self._tasks: list[asyncio.Task] = []
        self.client: httpx.AsyncClient | None = None

    async def request(self, op: str, kind: str, method: str, path: str, role: str, body: dict | None = None,
                      create: bool = False, **extra) -> bool:
        rec = self.ops.setdefault(op, {"op": op, "kind": kind, "path": path, "attempts": 0, "acked": False,
                                       "acked_via_409": False, "first_t": time.time(), **extra})
        deadline = time.monotonic() + self.RETRY_DEADLINE
        while True:
            rec["attempts"] += 1
            t = time.time()
            m0 = time.perf_counter()
            status, err = None, None
            try:
                r = await self.client.request(method, path, json=body, headers={"Authorization": f"Bearer {self.tokens[role]}"})
                status = r.status_code
            except Exception as exc:  # noqa: BLE001
                err = type(exc).__name__
            lat = time.perf_counter() - m0
            self.attempts.append({"t": t, "op": op, "kind": kind, "attempt": rec["attempts"], "status": status, "error": err,
                                  "latency_s": lat})
            if status in (200, 201):
                rec["acked"] = True
                return True
            if create and status == 409 and rec["attempts"] > 1:
                rec["acked"] = rec["acked_via_409"] = True  # an earlier attempt of OURS created it (the id is client-chosen)
                return True
            if status is None or status >= 500 or status == 429:
                if time.monotonic() > deadline:
                    return False
                await asyncio.sleep(self.RETRY_SLEEP)
                continue
            rec["gave_up_status"] = status
            return False  # a 4xx the retry cannot fix

    async def flow(self, n: int) -> None:
        run = self.run_ns.hex[:8]
        case_id = str(uuid.uuid5(self.run_ns, f"case|{n}"))
        eid = lambda t: str(uuid.uuid5(self.run_ns, f"event|{n}|{t}"))  # noqa: E731
        ok = await self.request(f"create:{case_id}", "create_case", "POST", "/cases", "lab",
                                {"case_id": case_id, "patient_ref": f"Patient/drill-{run}-{n}",
                                 "result_ref": f"DiagnosticReport/drill-{run}-{n}"}, create=True, case_id=case_id)
        if not ok:
            return
        for etype, role in (("accept_ownership", "pco"), ("contact_patient", "pco"), ("patient_acknowledge", "coord")):
            await asyncio.sleep(0.6)
            await self.request(f"read:{case_id}:{etype}", "read_case", "GET", f"/cases/{case_id}", "coord")
            await self.request(f"event:{eid(etype)}", etype, "POST", f"/cases/{case_id}/events", role,
                               {"type": etype, "event_id": eid(etype)}, case_id=case_id, event_id=eid(etype), event_type=etype)
        await self.request(f"read:{case_id}:final", "read_case", "GET", f"/cases/{case_id}", "coord")

    async def run(self) -> None:
        self.client = httpx.AsyncClient(base_url=API_BASE, timeout=httpx.Timeout(5.0, connect=3.0),
                                        limits=httpx.Limits(max_connections=60, max_keepalive_connections=30))
        n = 0
        t_next = time.monotonic()
        while not self.stopping:
            self._tasks.append(asyncio.create_task(self.flow(n)))
            n += 1
            self.flows_started = n
            t_next += 1.0 / self.rate
            await asyncio.sleep(max(0.0, t_next - time.monotonic()))
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.client.aclose()


# ---------------------------------------------------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------------------------------------------------

def logical_key(e) -> tuple | None:
    p = e["payload"]
    if e["type"] == "mark_overdue":
        return (e["case_id"], "mark_overdue", p.get("milestone") or p.get("state"))
    if e["type"] == "escalate":
        return (e["case_id"], "escalate", p.get("milestone") or p.get("state"), p.get("rung"))
    if e["type"] == "handoff_rejected" and p.get("bounced"):
        return (e["case_id"], "bounce", p.get("requested_at"))
    return None


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, math.ceil(q * len(s)) - 1)]


def dist(values: list[float]) -> dict:
    return {"n": len(values), "min": min(values) if values else None, "median": statistics.median(values) if values else None,
            "p95": pct(values, 0.95), "max": max(values) if values else None}


def verify_database(dsn: str, pathway, calendar, seeds: list[dict], client_cases: list[str], workload: Workload,
                    t_seed_commit: float, poller: Poller, ttl: float, max_loop_s: float) -> dict:
    out: dict = {}
    seed_ids = [s["case_id"] for s in seeds]
    client_uuids = [uuid.UUID(c) for c in client_cases]
    client_set = set(client_uuids)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = on")
        db_now = conn.execute("SELECT extract(epoch from clock_timestamp())::float8").fetchone()[0]
        now_v = datetime.fromtimestamp(db_now, UTC)
        rows = conn.execute(
            "SELECT case_id, event_id, seq, type, at, actor, payload, extract(epoch from created_at)::float8 FROM events "
            "WHERE case_id = ANY(%s) ORDER BY case_id, seq", (seed_ids + client_uuids,)).fetchall()
        states = dict(conn.execute("SELECT id, current_state FROM cases WHERE id = ANY(%s)", (seed_ids + client_uuids,)).fetchall())
        total_events_db = conn.execute("SELECT count(*), count(DISTINCT event_id) FROM events").fetchone()
        total_cases_db = conn.execute("SELECT count(*) FROM cases").fetchone()[0]
    by_case: dict[uuid.UUID, list] = collections.defaultdict(list)
    for r in rows:
        by_case[r[0]].append(r)
    all_events = [{"case_id": r[0], "event_id": r[1], "seq": r[2], "type": r[3], "at": r[4], "actor": r[5], "payload": r[6],
                   "created": r[7]} for r in rows]
    out["database"] = {"events_in_database_total": total_events_db[0], "distinct_event_ids_in_database_total": total_events_db[1],
                       "cases_in_database_total": total_cases_db, "drill_cases_checked": len(by_case), "drill_events_checked": len(all_events)}

    # (a) duplicates -------------------------------------------------------------------------------------------------
    ids = collections.Counter(e["event_id"] for e in all_events)
    dup_ids = [str(k) for k, v in ids.items() if v > 1]
    sched = [e for e in all_events if e["actor"] == SCHEDULER_ACTOR]
    lk = collections.Counter(k for e in sched if (k := logical_key(e)) is not None)
    dup_logical = [" | ".join(map(str, k)) for k, v in lk.items() if v > 1]
    client_chain = collections.Counter((e["case_id"], e["type"]) for e in all_events if e["case_id"] in client_set
                                       and e["type"] in ("accept_ownership", "contact_patient", "patient_acknowledge"))
    dup_client = [f"{k[0]} {k[1]}" for k, v in client_chain.items() if v > 1]
    genesis = collections.Counter(e["case_id"] for e in all_events if e["type"] == CASE_CREATED)
    dup_genesis = [str(k) for k, v in genesis.items() if v > 1]
    out["duplicates"] = {"duplicate_event_ids": len(dup_ids), "duplicate_scheduler_events_logical": len(dup_logical),
                         "duplicate_client_events": len(dup_client), "duplicate_genesis": len(dup_genesis),
                         "total": len(dup_ids) + len(dup_logical) + len(dup_client) + len(dup_genesis),
                         "examples": (dup_ids + dup_logical + dup_client + dup_genesis)[:5],
                         "scheduler_events_checked": len(sched)}

    # (b)(c) client requests ---------------------------------------------------------------------------------------
    present_event = collections.Counter(e["event_id"] for e in all_events)
    ev_case = {e["event_id"]: (e["case_id"], e["type"]) for e in all_events}
    acked_missing, acked_multi = [], []
    retried_total = retried_ok = acked_total = in_doubt = in_doubt_present = 0
    known_event_ids = set()
    for op in workload.ops.values():
        if op["kind"] == "create_case":
            n = genesis.get(uuid.UUID(op["case_id"]), 0)
        elif op["kind"] in ("accept_ownership", "contact_patient", "patient_acknowledge"):
            eid = uuid.UUID(op["event_id"])
            known_event_ids.add(eid)
            n = present_event.get(eid, 0)
            if n == 1 and ev_case[eid] != (uuid.UUID(op["case_id"]), op["event_type"]):
                n = -1  # wrong case or type
        else:
            continue
        if op["acked"]:
            acked_total += 1
            if n != 1:
                (acked_multi if n > 1 else acked_missing).append(op["op"])
            if op["attempts"] > 1:
                retried_total += 1
                retried_ok += n == 1
        else:
            in_doubt += 1
            in_doubt_present += n >= 1
    unexpected_client = [f"{e['case_id']}:{e['type']}" for e in all_events if e["case_id"] in client_set
                         and not e["actor"].startswith("system:") and e["type"] != CASE_CREATED and e["event_id"] not in known_event_ids]
    out["client_requests"] = {
        "writes_acknowledged": acked_total, "acknowledged_missing_from_db": len(acked_missing),
        "acknowledged_present_more_than_once": len(acked_multi), "examples_missing": acked_missing[:5],
        "writes_that_needed_a_retry": retried_total, "retried_writes_with_exactly_one_event": retried_ok,
        "writes_never_acknowledged": in_doubt, "never_acknowledged_but_present_in_db": in_doubt_present,
        "unexpected_events_in_client_cases": len(unexpected_client),
    }

    # (d) replay ---------------------------------------------------------------------------------------------------
    bad_seq, bad_replay, bad_state = [], [], []
    for cid, rs in by_case.items():
        if [r[2] for r in rs] != list(range(1, len(rs) + 1)):
            bad_seq.append(str(cid))
        evs = [Event(r[0], r[3], r[4], r[5], r[6], r[1], seq=r[2]) for r in rs]
        try:
            replay_model(evs, pathway)
            if replay(evs, pathway) != states.get(cid):
                bad_state.append(str(cid))
        except Exception as exc:  # noqa: BLE001
            bad_replay.append(f"{cid}: {type(exc).__name__}")
    out["replay"] = {"cases_replayed": len(by_case), "non_contiguous_seq": len(bad_seq), "replay_failures": len(bad_replay),
                     "state_mismatch": len(bad_state), "examples": (bad_seq + bad_replay + bad_state)[:5]}

    # (e) late, not lost: expected scheduler events -----------------------------------------------------------------
    actual = {e["event_id"]: e for e in sched}
    expected, trigger_ids, outstanding = {}, {}, 0
    for s in seeds:
        evs = list(s["seed_events"])
        planned_all = []
        while True:
            planned = plan_case(s["case_id"], replay_model(evs, pathway), pathway, calendar, now_v)
            if not planned:
                break
            evs += planned
            planned_all += planned
        for ev in planned_all:
            expected[ev.event_id] = (s["case_id"], ev.type, ev.payload.get("rung"))
        want_type, want_rung = ARM_TRIGGER[s["arm"]]
        trig = next((ev for ev in planned_all if ev.type == want_type and (want_rung is None or ev.payload.get("rung") == want_rung)), None)
        trigger_ids[s["case_id"]] = trig.event_id if trig else None
    for cid, rs in by_case.items():  # re-plan from the STORED logs: nothing may be outstanding
        evs = [Event(r[0], r[3], r[4], r[5], r[6], r[1], seq=r[2]) for r in rs]
        if plan_case(cid, replay_model(evs, pathway), pathway, calendar, now_v):
            outstanding += 1
    pre_swept = {ev.event_id for s in seeds for ev in s["seed_events"] if ev.actor == SCHEDULER_ACTOR}
    missing = [eid for eid in expected if eid not in actual]
    unexpected = [eid for eid in actual if eid not in expected and eid not in pre_swept]
    lateness_rows = []
    for s in seeds:
        tid = trigger_ids[s["case_id"]]
        if tid is not None and tid in actual:
            lateness_rows.append((s["trigger_at"].timestamp(), actual[tid]["created"] - s["trigger_at"].timestamp()))
    out["late_not_lost"] = {
        "seeded_cases": len(seeds), "expected_new_scheduler_events": len(expected), "present": len(expected) - len(missing),
        "pre_swept_scheduler_events_seeded": len(pre_swept),
        "losses_expected_but_missing": len(missing), "unexpected_scheduler_events": len(unexpected),
        "cases_with_outstanding_work_on_replan": outstanding, "verification_time_db_epoch": db_now,
        "examples": [str(x) for x in (missing + unexpected)[:5]],
    }
    out["_lateness_rows"] = lateness_rows

    # (f) fencing: where did scheduler events land relative to lease windows ---------------------------------------
    windows = []
    for e in poller.lease_events_for("scheduler"):
        windows.append({"token": e["token"], "holder": e["holder"], "start": None if e["first_sample"] else e["first_expires_at"] - ttl,
                        "end": poller.lease_last_exp.get(("scheduler", e["token"]))})
    live = [e for e in sched if e["created"] > t_seed_commit]

    def inside(c: float, slack_end: float):
        return next((w for w in windows if (w["start"] is None or c >= w["start"] - 0.25) and w["end"] is not None
                     and c <= w["end"] + slack_end), None)

    strict_out = tol_out = 0
    per_token = collections.Counter()
    outliers = []
    for e in live:
        hit, near = inside(e["created"], 0.0), inside(e["created"], max_loop_s)
        strict_out += hit is None
        if near is None:
            tol_out += 1
            outliers.append({"event_id": str(e["event_id"]), "created_db": e["created"]})
        w = near or hit
        per_token[str(w["token"]) if w else "none"] += 1
    out["fencing_windows"] = {
        "scheduler_events_written_during_run": len(live), "tolerance_s": max_loop_s,
        "outside_every_lease_window_strict": strict_out, "outside_every_lease_window_with_tolerance": tol_out,
        "events_by_lease_token": dict(per_token), "outliers": outliers[:5],
    }
    return out


# ---------------------------------------------------------------------------------------------------------------------
# the write-time fence, exercised directly (F10: api/app/leader.py check_fence_pg, called inside the event store's transaction)
# ---------------------------------------------------------------------------------------------------------------------

def stale_fence_probe(dsn: str, pathway, calendar, run_ns: uuid.UUID, old_holder: str, old_token: int) -> dict:
    """Appends a due scheduler event to a fresh probe case through the REAL event store (`PostgresEventStore.append_many`) with
    (1) the killed leader's fence, (2) the current holder with the killed token, (3) the killed holder with the current token:
    each must raise `StaleLeader` and leave the event log unchanged. Then (4) with the current fence, as a control: it must
    succeed, so the three refusals are not an artefact of the probe (a missing case, a bad event). The probe case is not part
    of the checked seeded / client cases."""
    from psycopg_pool import ConnectionPool

    from app.leader import Fence, StaleLeader
    from app.store_pg import PostgresEventStore

    now = datetime.now(UTC)
    rng = random.Random(7)
    seeds, rows_cases, rows_events = build_seed(pathway, calendar, uuid.uuid5(run_ns, "probe"), 1, now - timedelta(seconds=600), 6.0, rng)
    case = seeds[0]
    rows_cases = [rc[:5] + (NEVER,) + rc[6:] for rc in rows_cases]  # the live sweep skips it (next_due_at says nothing is due)
    insert_seed(dsn, rows_cases, rows_events)
    model = replay_model(case["seed_events"], pathway)
    planned = plan_case(case["case_id"], model, pathway, calendar, now)
    out: dict = {"probe_case": str(case["case_id"]), "events_planned": len(planned), "attempts": []}
    with psycopg.connect(dsn, autocommit=True) as conn:
        cur_lease = conn.execute("SELECT holder, token FROM leader_lease WHERE name = 'scheduler'").fetchone()
        count = lambda: conn.execute("SELECT count(*) FROM events WHERE case_id = %s", (case["case_id"],)).fetchone()[0]  # noqa: E731
        cur_holder, current_fence_no = cur_lease[0], int(cur_lease[1])
        pool = ConnectionPool(dsn, min_size=1, max_size=2, open=True)
        try:
            pool.wait(timeout=30)
            store = PostgresEventStore(pool, calendar=calendar)
            variants = [("killed holder, killed token", Fence("scheduler", old_holder, old_token), True),
                        ("current holder, killed (old) token", Fence("scheduler", cur_holder, old_token), True),
                        ("killed holder, current token", Fence("scheduler", old_holder, current_fence_no), True),
                        ("control: current holder, current token", Fence("scheduler", cur_holder, current_fence_no), False)]
            for label, fence, must_refuse in variants:
                before = count()
                try:
                    store.append_many(planned, pathway, fence=fence)
                    outcome = "accepted"
                except StaleLeader:
                    outcome = "StaleLeader"
                except Exception as exc:  # noqa: BLE001
                    outcome = f"{type(exc).__name__}: {exc}"[:120]
                out["attempts"].append({"fence": label, "holder": fence.holder, "token": fence.token, "outcome": outcome,
                                        "events_before": before, "events_after": count(), "expected_refusal": must_refuse})
        finally:
            pool.close()
    stale = [a for a in out["attempts"] if a["expected_refusal"]]
    ctrl = [a for a in out["attempts"] if not a["expected_refusal"]]
    out["stale_fence_attempts"] = len(stale)
    out["stale_fence_refused"] = sum(a["outcome"] == "StaleLeader" for a in stale)
    out["stale_fence_rows_written"] = sum(a["events_after"] - a["events_before"] for a in stale)
    out["control_accepted"] = all(a["outcome"] == "accepted" and a["events_after"] - a["events_before"] == len(planned) for a in ctrl)
    out["ok"] = out["stale_fence_refused"] == len(stale) and out["stale_fence_rows_written"] == 0 and out["control_accepted"] and len(planned) > 0
    out["summary"] = (f"{out['stale_fence_refused']}/{len(stale)} stale fences refused with StaleLeader, {out['stale_fence_rows_written']} rows "
                      f"written by them, control write accepted={out['control_accepted']}")
    return out


# ---------------------------------------------------------------------------------------------------------------------
# one iteration
# ---------------------------------------------------------------------------------------------------------------------

def wait_healthy(dsn: str, settle: float = 30.0, timeout: float = 420.0) -> dict:
    t_end = time.time() + timeout
    last_tokens, stable_since = None, time.time()
    while time.time() < t_end:
        try:
            api, sch = list_pods("api"), list_pods("scheduler")
            ok_pods = (len(api) == 3 and all(p["ready"] and not p["terminating"] for p in api)
                       and len(sch) == 2 and all(p["ready"] and not p["terminating"] for p in sch))
            with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
                leases = conn.execute("SELECT name, holder, token, extract(epoch from expires_at - now())::float8 "
                                      "FROM leader_lease").fetchall()
                followers = conn.execute("SELECT service, extract(epoch from now() - beat_at)::float8 FROM heartbeats "
                                         "WHERE service LIKE 'scheduler-follower-%'").fetchall()
            L = {r[0]: r for r in leases}
            names = {p["name"] for p in sch}
            fresh = {r[0][len("scheduler-follower-"):] for r in followers if r[1] < 20}
            ok_leases = ("scheduler" in L and "api-background" in L and L["scheduler"][3] > 5 and L["api-background"][3] > 5
                         and L["scheduler"][1] in names and (names - {L["scheduler"][1]}) <= fresh)
            tokens = tuple(sorted((r[0], r[1], r[2]) for r in leases))
            if tokens != last_tokens:
                last_tokens, stable_since = tokens, time.time()
            if ok_pods and ok_leases and time.time() - stable_since >= settle:
                api_map = api_holder_pods(api)
                if L["api-background"][1] in api_map:
                    return {"scheduler_holder": L["scheduler"][1], "scheduler_token": L["scheduler"][2],
                            "background_lease_pod": L["api-background"][1], "api_background_token": L["api-background"][2],
                            "api_background_pod": api_map[L["api-background"][1]],
                            "scheduler_restarts": sum(p["restarts"] for p in sch), "api_restarts": sum(p["restarts"] for p in api)}
        except Exception as exc:  # noqa: BLE001
            print(f"  (health check: {type(exc).__name__}: {exc})", flush=True)
        time.sleep(2)
    raise SystemExit("cluster did not become healthy in time")


def parse_scheduler_logs(streams: LogStreams, t_from: float) -> dict:
    sweeps, refusals, leader_lines, failures = [], [], [], []
    for pod, ts, msg in streams.lines("scheduler"):
        if ts < t_from:  # the streams ask for 180 s of history so that a pod's first lines are not missed
            continue
        m = re.search(r"scheduler (\w+) (.*)", msg)
        if not m:
            continue
        text = m.group(2)
        if text.startswith("sweep sim_time="):
            kv = dict(re.findall(r"(\w+)=(\S+)", text))
            sweeps.append({"t": ts, "pod": pod, "evaluated": int(kv.get("evaluated", 0)), "mark_overdue": int(kv.get("mark_overdue", 0)),
                           "escalate": int(kv.get("escalate", 0)), "errors": int(kv.get("errors", 0)), "rejected": int(kv.get("rejected", 0))})
        elif re.search(r"HTTP(?: Error)? 409", text) or "stale leader" in text:
            refusals.append({"t": ts, "pod": pod, "line": text[:160]})
        elif "is the leader, fencing token" in text or "no longer the leader" in text:
            leader_lines.append({"t": ts, "pod": pod, "line": text[:160]})
        elif "not delivered" in text or "not acquired" in text or "failed" in text:
            failures.append({"t": ts, "pod": pod, "line": text[:160]})
    return {"sweeps": sweeps, "refusals_409": refusals, "leader_changes": leader_lines, "failures": failures}


def parse_api_logs(streams: LogStreams, probe_t: float, t_from: float) -> dict:
    """409s on /scheduler/* routes and mid-sweep fence refusals (`sweep stopped: stale leader`, F10) in the api pods' logs. Lines
    from `probe_t` on include the drill's own stale-token probes and are counted apart ("organic" = before the probes)."""
    refused, organic = collections.Counter(), collections.Counter()
    samples, leaders = [], {}
    mid_sweep = []
    for pod, ts, msg in streams.lines("api"):
        if ts < t_from:
            continue
        m = re.search(r"lease api-background: (api-[0-9a-f]+) is now the leader \(token (\d+)\)", msg)
        if m:
            leaders[m.group(1)] = {"pod": pod, "token": int(m.group(2)), "t": ts}
        if "sweep stopped: stale leader" in msg:
            mid_sweep.append({"t": ts, "pod": pod, "line": msg[:200]})
        if "POST /scheduler/" in msg and re.search(r'HTTP/1\.[01]\\?" 409\b', msg):
            route = re.search(r"POST (/scheduler/[a-z/-]+)", msg)
            name = route.group(1) if route else "?"
            refused[name] += 1
            if ts < probe_t:
                organic[name] += 1
            if len(samples) < 20:
                samples.append({"t": ts, "pod": pod, "route": name, "organic": ts < probe_t})
    return {"scheduler_calls_refused_409": dict(refused), "scheduler_calls_refused_409_organic": dict(organic), "samples": samples,
            "api_background_leaders": leaders, "sweeps_stopped_mid_sweep_by_fence": mid_sweep}


async def run_iteration(idx: int, mode: str, args, tokens: dict, dsn: str, pathway, calendar, ttl: float, interval: float) -> dict:
    run_label = f"{mode}-{idx}"
    run_ns = uuid.uuid5(DRILL_NS, f"{run_label}|{uuid.uuid4()}")
    print(f"\n=== iteration {run_label} ({mode} kill) ===", flush=True)
    env_start = await asyncio.to_thread(vm_pressure)
    health = await asyncio.to_thread(wait_healthy, dsn)
    print(f"  healthy: scheduler lease {health['scheduler_holder']} token {health['scheduler_token']}, api-background "
          f"{health['background_lease_pod']} ({health['api_background_pod']}) token {health['api_background_token']}", flush=True)
    outdir = Path(tempfile.mkdtemp(prefix="failover-logs-"))
    poller = Poller(dsn)
    poller.start()
    streams = LogStreams(outdir)
    streams.start()
    await asyncio.sleep(4)
    started_at = datetime.now(UTC)

    wl = Workload(run_ns, tokens, args.client_rate)
    wl_task = asyncio.create_task(wl.run())
    await asyncio.sleep(2)

    # seed -----------------------------------------------------------------------------------------------------------
    rng = random.Random(int(run_ns.int % (2 ** 31)))
    base_lead = max(25.0, args.cases * 0.02 + 15.0)
    base = datetime.fromtimestamp(poller.latest["db_now"] + base_lead, UTC)
    t_build = time.time()
    seeds, rows_cases, rows_events = await asyncio.to_thread(build_seed, pathway, calendar, run_ns, args.cases, base, args.window, rng)
    build_s = time.time() - t_build
    t_ins = time.time()
    await asyncio.to_thread(insert_seed, dsn, rows_cases, rows_events)
    insert_s = time.time() - t_ins
    t_seed_commit = poller.latest["db_now"]
    if t_seed_commit >= base.timestamp():
        raise SystemExit(f"seeding finished after the first trigger (build {build_s:.1f}s insert {insert_s:.1f}s): raise the lead")
    print(f"  seeded {len(seeds)} cases / {len(rows_events)} events (build {build_s:.1f}s, insert {insert_s:.1f}s); "
          f"triggers from +5s to +{args.window:.0f}s after {base:%H:%M:%S}Z", flush=True)

    # wait for the kill time -----------------------------------------------------------------------------------------
    kill_db = base.timestamp() + args.kill_at
    while poller.latest["db_now"] < kill_db - 4:
        await asyncio.sleep(0.2)
    pods_api = await asyncio.to_thread(list_pods, "api")
    pods_sch = await asyncio.to_thread(list_pods, "scheduler")
    amap = await asyncio.to_thread(api_holder_pods, pods_api)
    while poller.latest["db_now"] < kill_db:
        await asyncio.sleep(0.05)
    lease = poller.latest["leases"]
    s_holder, s_token, _ = lease["scheduler"]
    a_holder, a_token, _ = lease["api-background"]
    notes = []
    if s_holder not in {p["name"] for p in pods_sch}:
        raise SystemExit(f"scheduler holder {s_holder} is not a running scheduler pod")
    s_pod = s_holder
    a_pod = amap.get(a_holder)
    if a_pod is None:
        a_pod = next(p["name"] for p in pods_api if p["ready"])
        notes.append(f"api-background holder {a_holder} could not be mapped to a pod; deleted {a_pod} instead")
    opts = {"kind": "DeleteOptions", "apiVersion": "v1"} | ({"gracePeriodSeconds": 0} if mode in ("force", "hard") else {})
    hard_info: dict = {}
    if mode == "hard":
        # SIGKILL both containers through the node's container runtime (crictl stop --timeout 0: no SIGTERM, so nothing can
        # release a lease), then force-delete the pods. Container ids are resolved BEFORE t0.
        node_of = {p["name"]: p["node"] for p in pods_api + pods_sch}

        def container_id(pod: str) -> str:
            out = subprocess.run(["docker", "exec", node_of[pod], "crictl", "ps", "-q", "--label", f"io.kubernetes.pod.name={pod}"],
                                 capture_output=True, text=True, timeout=30).stdout.split()
            if not out:
                raise SystemExit(f"no running container found for pod {pod}")
            return out[0]

        cids = {s_pod: await asyncio.to_thread(container_id, s_pod), a_pod: await asyncio.to_thread(container_id, a_pod)}

        def kill9(pod: str) -> float:
            m = time.time()
            subprocess.run(["docker", "exec", node_of[pod], "crictl", "stop", "--timeout", "0", cids[pod]],
                           capture_output=True, text=True, timeout=30, check=True)
            return time.time() - m

        t0 = time.time()
        durs = await asyncio.gather(asyncio.to_thread(kill9, s_pod), asyncio.to_thread(kill9, a_pod))
        hard_info = {"sigkill_command_s": {"scheduler": durs[0], "api": durs[1]}}
    async with httpx.AsyncClient(base_url=PROXY["base"], timeout=30) as kc:
        if mode != "hard":
            t0 = time.time()
        resp = await asyncio.gather(*(kc.request("DELETE", f"/api/v1/namespaces/{NS}/pods/{n}", json=opts) for n in (s_pod, a_pod)))
        t_cmd_done = time.time()
    cmd = [f"DELETE the scheduler leader pod and the api-background holder pod (names in the run record), gracePeriodSeconds="
           + {"force": "0 (= kubectl delete --grace-period=0 --force)", "graceful": "default (pod's terminationGracePeriodSeconds)",
              "hard": "0 after SIGKILL of both containers with crictl stop --timeout 0 (t0 = instant the SIGKILL commands were issued)"}[mode]]
    print(f"  t0: deleted scheduler leader {s_pod} (token {s_token}) and api pod {a_pod} (api-background {a_holder}, token {a_token}); "
          f"API server answered {[r.status_code for r in resp]} after {t_cmd_done - t0:.2f}s", flush=True)

    # recovery watcher -----------------------------------------------------------------------------------------------
    ready = {"api_all_ready_s": None, "scheduler_all_ready_s": None, "timeline": []}

    async def watch_ready():
        while time.time() - t0 < 240 and (ready["api_all_ready_s"] is None or ready["scheduler_all_ready_s"] is None):
            api = await asyncio.to_thread(list_pods, "api")
            sch = await asyncio.to_thread(list_pods, "scheduler")
            n_api = sum(p["ready"] and not p["terminating"] for p in api)
            n_sch = sum(p["ready"] and not p["terminating"] for p in sch)
            tr = time.time() - t0
            ready["timeline"].append({"t_s": round(tr, 2), "api_ready": n_api, "api_total": len(api), "scheduler_ready": n_sch, "scheduler_total": len(sch)})
            if ready["api_all_ready_s"] is None and n_api == 3 and len(api) == 3:
                ready["api_all_ready_s"] = tr
            if ready["scheduler_all_ready_s"] is None and n_sch == 2 and len(sch) == 2:
                ready["scheduler_all_ready_s"] = tr
            await asyncio.sleep(0.5)

    watch_task = asyncio.create_task(watch_ready())
    end_db = base.timestamp() + args.window + 45
    while poller.latest["db_now"] < end_db:
        await asyncio.sleep(0.5)
    await watch_task
    wl.stopping = True
    await wl_task
    await asyncio.sleep(2)

    # stale-token probes ----------------------------------------------------------------------------------------------
    probe_t = time.time()
    token = await asyncio.to_thread(secret_value, "screening-closure-secrets", "SCHEDULER_SERVICE_TOKEN")
    async with httpx.AsyncClient(base_url=API_BASE, timeout=15) as c:
        auth = {"Authorization": f"Bearer {token}"}
        r1 = await c.post("/scheduler/sweep", headers={**auth, "X-Leader-Holder": s_holder, "X-Leader-Token": str(s_token)})
        r2 = await c.post("/scheduler/heartbeat", headers=auth,
                          json={"sweep_ok": True, "role": "leader", "holder": s_holder, "fencing_token": s_token})
        probes = {"stale_sweep_status": r1.status_code, "stale_leader_heartbeat_status": r2.status_code,
                  "probe_holder": s_holder, "probe_token": s_token}
    del token
    tx_probe = await asyncio.to_thread(stale_fence_probe, dsn, pathway, calendar, run_ns, s_holder, s_token)
    print(f"  in-transaction fence probe: {tx_probe['summary']}", flush=True)
    await asyncio.sleep(1.5)
    poller.stop()
    streams.stop()

    # analysis ---------------------------------------------------------------------------------------------------------
    off = poller.offset()
    t0_db = t0 + off
    logs = parse_scheduler_logs(streams, started_at.timestamp())
    apilogs = parse_api_logs(streams, probe_t, started_at.timestamp())
    sched_names = {p["name"] for p in pods_sch}

    def handover(name: str, old_holder: str, old_token: int) -> dict:
        ev = next((e for e in poller.lease_events_for(name) if e["db_t"] >= t0_db - 1 and e["holder"] != old_holder and e["token"] > old_token), None)
        last_exp = poller.lease_last_exp.get((name, old_token))
        d = {"killed_holder": old_holder, "killed_token": old_token, "killed_lease_last_expires_at_db": last_exp,
             "killed_holder_last_renewal_db": None if last_exp is None else last_exp - ttl}
        if ev is None:
            d.update(new_holder=None, new_token=None, time_to_new_leader_s=None)
        else:
            new_pod = ev["holder"] if name == "scheduler" else (apilogs["api_background_leaders"].get(ev["holder"]) or {}).get("pod")
            d.update(new_holder=ev["holder"], new_holder_pod=new_pod, new_token=ev["token"], new_leader_first_seen_db=ev["db_t"],
                     time_to_new_leader_s=ev["db_t"] - t0_db,
                     seconds_since_killed_holders_last_renewal=None if last_exp is None else ev["db_t"] - (last_exp - ttl),
                     seconds_after_old_lease_expiry=None if last_exp is None else ev["db_t"] - last_exp,
                     lease_released_by_killed_holder=None if last_exp is None else ev["db_t"] < last_exp - 1.0,
                     new_holder_pod_existed_before_t0=(new_pod in sched_names) if name == "scheduler" else (new_pod in {p["name"] for p in pods_api}))
        return d

    sched_h = handover("scheduler", s_holder, s_token)
    api_h = handover("api-background", a_holder, a_token)

    done = sorted([b for b in poller.beats if b["sweeping"] is False and b["role"] == "leader" and b["sweep_ok"]], key=lambda b: b["beat_at"])
    killed_done = [b for b in done if b["holder"] == s_holder and (b["token"] or 0) == s_token]
    last_killed = killed_done[-1] if killed_done else None
    first_succ = next((b for b in done if last_killed is not None and b["beat_at"] > last_killed["beat_at"] and b["holder"] != s_holder), None)
    gap = (first_succ["beat_at"] - last_killed["beat_at"]) if first_succ else None
    before = [b for b in done if b["beat_at"] <= t0_db]
    after = [b for b in done if b["beat_at"] > t0_db]
    gap_t0 = (after[0]["beat_at"] - before[-1]["beat_at"]) if before and after else None
    healthy_gaps = [b2["beat_at"] - b1["beat_at"] for b1, b2 in zip(killed_done, killed_done[1:]) if b2["beat_at"] > t0_db - 120]
    baseline = statistics.median(healthy_gaps) if healthy_gaps else None
    sw = logs["sweeps"]
    lb = [s for s in sw if s["pod"] == s_pod]
    la = [s for s in sw if s["pod"] != s_pod and lb and s["t"] > lb[-1]["t"]]
    gap_logs = (la[0]["t"] - lb[-1]["t"]) if lb and la else None
    loops, start_beat = [], None
    for b in sorted(poller.beats, key=lambda b: b["beat_at"]):
        if b["sweeping"] is True and start_beat is None:
            start_beat = b
        elif b["sweeping"] is False and start_beat is not None:
            loops.append(b["beat_at"] - start_beat["beat_at"])
            start_beat = None
    max_loop_s = max(loops) if loops else 15.0
    sweep = {
        "scheduler_interval_s": interval,
        "last_sweep_completed_by_killed_leader_db": last_killed["beat_at"] if last_killed else None,
        "killed_leader_last_sweep_after_t0_s": (last_killed["beat_at"] - t0_db) if last_killed else None,
        "first_sweep_completed_by_successor_db": first_succ["beat_at"] if first_succ else None,
        "successor_holder": first_succ["holder"] if first_succ else None, "successor_token": first_succ["token"] if first_succ else None,
        "seconds_from_t0_to_first_successor_sweep": (first_succ["beat_at"] - t0_db) if first_succ else None,
        "sweep_gap_s": gap,
        "missed_sweeps": None if gap is None else max(0, math.floor(gap / interval) - 1),
        "baseline_healthy_gap_s": baseline,
        "missed_sweeps_vs_baseline": None if gap is None or not baseline else max(0, math.floor(gap / baseline) - 1),
        "sweep_gap_around_t0_s": gap_t0,
        "sweep_gap_from_pod_logs_s": gap_logs,
        "healthy_gaps_used_for_baseline": len(healthy_gaps),
        "first_sweeps_after_recovery_from_logs": [{"t_rel_s": round(s["t"] - t0_db, 2), "pod": s["pod"], "evaluated": s["evaluated"],
                                                   "mark_overdue": s["mark_overdue"], "escalate": s["escalate"]} for s in la[:3]],
        "sweep_log_lines_total": len(sw), "max_observed_loop_s": max_loop_s,
    }

    atts = wl.attempts
    api_ready = ready["api_all_ready_s"]
    win_end = t0 + (api_ready if api_ready is not None else 120)

    def failed(a):
        return a["status"] is None or a["status"] >= 500

    win = [a for a in atts if t0 <= a["t"] <= win_end]
    base_lat = [a["latency_s"] for a in atts if a["t"] < t0 and not failed(a)]
    fails = [a for a in atts if failed(a)]
    outage = (max(a["t"] + a["latency_s"] for a in fails) - min(a["t"] for a in fails)) if fails else 0.0
    done_t = sorted(a["t"] + a["latency_s"] for a in atts if not failed(a) and t0 - 5 <= a["t"] <= t0 + 90)
    longest_gap = max((b2 - b1 for b1, b2 in zip(done_t, done_t[1:])), default=None)
    err_by = collections.Counter((a["error"] or f"HTTP {a['status']}") for a in atts if failed(a))
    ops = list(wl.ops.values())
    client = {
        "client_rate_new_case_flows_per_s": args.client_rate, "flows_started": wl.flows_started, "ops": len(ops),
        "attempts_total": len(atts), "attempts_before_t0": sum(a["t"] < t0 for a in atts),
        "error_attempts_total": sum(failed(a) for a in atts), "error_attempts_by_type": dict(err_by),
        "error_attempts_in_disruption_window": sum(failed(a) for a in win),
        "retries_total": sum(max(0, o["attempts"] - 1) for o in ops), "ops_that_needed_a_retry": sum(o["attempts"] > 1 for o in ops),
        "ops_never_succeeded": sum(not o["acked"] for o in ops), "creates_acknowledged_via_409_after_retry": sum(o["acked_via_409"] for o in ops),
        "ops_refused_with_4xx": sum(1 for o in ops if "gave_up_status" in o),
        "client_observed_outage_s": outage, "longest_gap_between_successful_requests_s": longest_gap,
        "disruption_window_s": api_ready, "attempts_in_disruption_window": len(win),
        "max_latency_in_window_s": max((a["latency_s"] for a in win), default=None),
        "max_latency_overall_s": max((a["latency_s"] for a in atts), default=None),
        "baseline_latency_before_t0_s": {"median": statistics.median(base_lat) if base_lat else None, "p99": pct(base_lat, 0.99)},
        "api_pods_ready_again_s": api_ready, "scheduler_pods_ready_again_s": ready["scheduler_all_ready_s"],
        "error_timeline": [{"t_rel_s": round(a["t"] - t0, 2), "kind": a["kind"], "result": a["error"] or a["status"],
                            "latency_s": round(a["latency_s"], 3), "attempt": a["attempt"]} for a in atts if failed(a)][:200],
    }
    refused_4xx = collections.Counter((o.get("kind"), o.get("gave_up_status")) for o in ops if "gave_up_status" in o)
    client["ops_refused_with_4xx_by_kind"] = {f"{k[0]} {k[1]}": v for k, v in refused_4xx.items()}
    client_cases = [o["case_id"] for o in ops if o["kind"] == "create_case"]
    ver = await asyncio.to_thread(verify_database, dsn, pathway, calendar, seeds, client_cases, wl, t_seed_commit, poller, ttl, max_loop_s)
    lateness = ver.pop("_lateness_rows")
    g0 = sweep["last_sweep_completed_by_killed_leader_db"] or t0_db
    g1 = sweep["first_sweep_completed_by_successor_db"] or t0_db
    in_gap = [lat for trig, lat in lateness if g0 < trig <= g1]
    out_gap = [lat for trig, lat in lateness if not (g0 < trig <= g1)]
    ver["late_not_lost"].update({
        "triggers_falling_due_inside_the_sweep_gap": len(in_gap), "lateness_inside_gap_s": dist(in_gap), "lateness_outside_gap_s": dist(out_gap),
        "max_lateness_s": max([*in_gap, *out_gap], default=None),
    })
    refusals = logs["refusals_409"]

    def hist(name: str, lease_name: str):
        return [{"holder": e["holder"], "token": e["token"], "first_seen_db": e["db_t"],
                 "acquired_at_db": None if e["first_sample"] else e["first_expires_at"] - ttl, "held_before_poller_started": e["first_sample"],
                 "last_expiry_seen_db": poller.lease_last_exp.get((lease_name, e["token"]))} for e in poller.lease_events_for(lease_name)]

    fencing = {
        "scheduler_lease_history": hist("scheduler", "scheduler"), "api_background_lease_history": hist("api", "api-background"),
        "refusals_409_in_scheduler_logs": len(refusals), "refusals_409_samples": refusals[:5],
        "refusals_409_in_api_access_logs_incl_probes": apilogs["scheduler_calls_refused_409"],
        "refusals_409_in_api_access_logs_organic": apilogs["scheduler_calls_refused_409_organic"],
        "refusals_409_samples_api": apilogs["samples"][:10],
        "sweeps_stopped_mid_sweep_by_fence": apilogs["sweeps_stopped_mid_sweep_by_fence"][:10],
        "sweeps_stopped_mid_sweep_by_fence_count": len(apilogs["sweeps_stopped_mid_sweep_by_fence"]),
        "stale_leader_409_organic_total": sum(apilogs["scheduler_calls_refused_409_organic"].values()),
        "stale_leader_refusals_seen_by_scheduler_pods_organic": len([r for r in refusals if r["t"] < probe_t]),
        "in_transaction_probe": tx_probe,
        "stale_token_probe_after_recovery": probes,
        "scheduler_leader_log_lines": logs["leader_changes"][:10],
        **ver.pop("fencing_windows"),
    }
    for name in ("scheduler", "api_background"):
        toks = [h["token"] for h in fencing[f"{name}_lease_history"]]
        fencing[f"{name}_tokens_strictly_increasing"] = all(b > a for a, b in zip(toks, toks[1:]))
    fencing["write_from_deposed_leader_evidence"] = (
        "The event log has no holder/token column. Evidence: (1) the fence is checked when each scheduler request enters the "
        f"API (require_current_leader) and again inside every write transaction (leader.check_fence_pg); the stale-token probe "
        f"after recovery got HTTP {probes['stale_sweep_status']} (sweep) and {probes['stale_leader_heartbeat_status']} (heartbeat), "
        f"and the direct in-transaction probe: {tx_probe['summary']}; (2) every scheduler event's insert time lies inside a lease "
        f"validity window (outside strict: {fencing['outside_every_lease_window_strict']}, outside with "
        f"{max_loop_s:.1f}s tolerance: {fencing['outside_every_lease_window_with_tolerance']}); (3) event ids are deterministic, "
        "so a stale write could only be a no-op repeat.")
    result = {
        "run": run_label, "mode": mode, "started_at": started_at.isoformat(), "ended_at": datetime.now(UTC).isoformat(),
        "t0_utc": datetime.fromtimestamp(t0, UTC).isoformat(), "t0_db_epoch": t0_db, "database_clock_minus_host_clock_s": off,
        "delete_request": cmd[0], **({"hard_kill": hard_info} if hard_info else {}), "delete_api_answered_after_s": t_cmd_done - t0, "delete_api_status": [r.status_code for r in resp],
        "killed": {"scheduler_pod": s_pod, "scheduler_holder": s_holder, "scheduler_token": s_token, "api_pod": a_pod,
                   "background_lease_pod": a_holder, "api_background_token": a_token},
        "pods_before": {"scheduler": sorted(sched_names), "api": sorted(p["name"] for p in pods_api)}, "notes": notes,
        "scheduler_handover": sched_h, "api_background_handover": api_h, "sweep": sweep,
        "workload": {"seeded_cases": len(seeds), "seeded_events": len(rows_events), "window_s": args.window, "kill_at_s_after_window_start": args.kill_at,
                     "seed_build_s": round(build_s, 2), "seed_insert_s": round(insert_s, 2), "arms": dict(collections.Counter(s["arm"] for s in seeds))},
        "client_impact": client,
        "integrity": {k: ver[k] for k in ("database", "duplicates", "client_requests", "replay", "late_not_lost")},
        "fencing": fencing,
        "pod_ready_timeline": ready["timeline"][:80],
        "poller": {"samples": poller.samples, "errors": poller.errors, "max_rtt_s": poller.max_rtt, "interval_s": poller.interval},
        "healthy_before": health, "environment_at_start": env_start, "environment_at_end": await asyncio.to_thread(vm_pressure),
    }
    for pth in outdir.glob("*"):
        pth.unlink()
    outdir.rmdir()
    print(f"  scheduler leader in {sched_h['time_to_new_leader_s']}s, api-background in {api_h['time_to_new_leader_s']}s, "
          f"sweep gap {sweep['sweep_gap_s']}s (missed {sweep['missed_sweeps']}), client error attempts {client['error_attempts_total']}, "
          f"retries {client['retries_total']}, duplicates {ver['duplicates']['total']}, losses {ver['late_not_lost']['losses_expected_but_missing']}, "
          f"outstanding {ver['late_not_lost']['cases_with_outstanding_work_on_replan']}", flush=True)
    return result


# ---------------------------------------------------------------------------------------------------------------------
# summary / main
# ---------------------------------------------------------------------------------------------------------------------

def get_path(d: dict, path: str):
    for part in path.split("."):
        d = d.get(part) if isinstance(d, dict) else None
    return d


SUMMARY_METRICS = {
    "scheduler_time_to_new_leader_s": "scheduler_handover.time_to_new_leader_s",
    "api_background_time_to_new_leader_s": "api_background_handover.time_to_new_leader_s",
    "scheduler_seconds_since_killed_holders_last_renewal": "scheduler_handover.seconds_since_killed_holders_last_renewal",
    "api_background_seconds_since_killed_holders_last_renewal": "api_background_handover.seconds_since_killed_holders_last_renewal",
    "time_to_first_successor_sweep_s": "sweep.seconds_from_t0_to_first_successor_sweep",
    "sweep_gap_s": "sweep.sweep_gap_s",
    "missed_sweeps": "sweep.missed_sweeps",
    "missed_sweeps_vs_baseline": "sweep.missed_sweeps_vs_baseline",
    "client_error_attempts": "client_impact.error_attempts_total",
    "client_retries": "client_impact.retries_total",
    "client_observed_outage_s": "client_impact.client_observed_outage_s",
    "client_longest_gap_between_successes_s": "client_impact.longest_gap_between_successful_requests_s",
    "stale_leader_409_organic": "fencing.stale_leader_409_organic_total",
    "client_ops_never_succeeded": "client_impact.ops_never_succeeded",
    "client_max_latency_in_window_s": "client_impact.max_latency_in_window_s",
    "api_pods_ready_again_s": "client_impact.api_pods_ready_again_s",
    "duplicates_total": "integrity.duplicates.total",
    "acknowledged_writes_missing": "integrity.client_requests.acknowledged_missing_from_db",
    "scheduler_events_lost": "integrity.late_not_lost.losses_expected_but_missing",
    "outstanding_cases_on_replan": "integrity.late_not_lost.cases_with_outstanding_work_on_replan",
    "max_event_lateness_s": "integrity.late_not_lost.max_lateness_s",
    "replay_failures": "integrity.replay.replay_failures",
    "stale_fence_write_rows_written": "fencing.in_transaction_probe.stale_fence_rows_written",
    "scheduler_events_outside_lease_windows": "fencing.outside_every_lease_window_with_tolerance",
}


def summarize(runs: list[dict]) -> dict:
    out = {}
    for mode in ("force", "graceful", "hard"):
        rs = [r for r in runs if r["mode"] == mode]
        if not rs:
            continue
        block = {"runs": len(rs)}
        for name, path in SUMMARY_METRICS.items():
            vals = [get_path(r, path) for r in rs]
            clean = [v for v in vals if isinstance(v, (int, float))]
            block[name] = {"values": vals, "min": min(clean) if clean else None,
                           "median": statistics.median(clean) if clean else None, "p95": pct(clean, 0.95),
                           "max": max(clean) if clean else None}
        out[mode] = block
    return out


def git_info() -> dict:
    def g(*a):
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    status = g("status", "--porcelain").splitlines()
    return {"commit": g("rev-parse", "HEAD"), "branch": g("rev-parse", "--abbrev-ref", "HEAD"), "dirty": bool(status), "dirty_files": status[:30]}


def chart_values() -> dict:
    vals = json.loads(subprocess.run(["helm", "--kube-context", CTX, "-n", NS, "get", "values", REL, "--all", "-o", "json"],
                                     capture_output=True, text=True, check=True).stdout)
    app = vals.get("app", {})
    return {
        "leader_lease_ttl_s": float(app.get("leaderLeaseTtlSeconds", 30)), "require_leader_fence": app.get("requireLeaderFence"),
        "scheduler_interval_s": float(vals.get("scheduler", {}).get("intervalSeconds", 10)),
        "scheduler_replicas": vals.get("scheduler", {}).get("replicas"), "api_replicas": vals.get("api", {}).get("replicas"),
        "api_sweep_workers": vals.get("api", {}).get("sweepWorkers"), "api_resources": vals.get("api", {}).get("resources"),
        "automation_enabled": app.get("automationEnabled"), "fhir_projection": app.get("fhirProjection"),
        "scheduler_follower_poll_s_default": 2.0,
        "lease_renewal_period": "ttl/3 (scheduler lease keeper thread, api LeaderLoop)",
        "scheduler_heartbeat_while_sweeping_s": 15.0, "images": vals.get("images"), "clock_mode": "wall (APP_ENV=production)",
    }


async def amain(args) -> int:
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pathway = load_pathway(ROOT / "config/pathways/colorectal_fit.yaml")
    await asyncio.to_thread(start_proxy)
    cv = await asyncio.to_thread(chart_values)
    ttl, interval = cv["leader_lease_ttl_s"], cv["scheduler_interval_s"]
    spec = json.loads(subprocess.run(["bash", str(ROOT / "tools/k8s/machine_spec.sh")], capture_output=True, text=True, check=True).stdout)
    cal_info = await asyncio.to_thread(ensure_calendar, not args.no_calendar_override)
    runs: list[dict] = []
    try:
        cal_text = await asyncio.to_thread(configmap_calendar_text)
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write(cal_text)
        calendar = load_calendar(fh.name)
        os.unlink(fh.name)
        dsn = await asyncio.to_thread(owner_dsn)
        tokens = {"lab": mint("lab", "drill-lab"), "coord": mint("coordinator", "drill-coordinator"),
                  "pco": mint("primary_care_owner", "Dr. Layla Haddad", sub="Dr. Layla Haddad")}  # the actor is role + sub: this is the seeded default owner
        plan = ([("force", i + 1) for i in range(args.runs)] + [("graceful", i + 1) for i in range(args.graceful_runs)]
                + [("hard", i + 1) for i in range(args.hard_runs)])
        started = datetime.now(UTC)
        for mode, idx in plan:
            runs.append(await run_iteration(idx, mode, args, tokens, dsn, pathway, calendar, ttl, interval))
            doc = {"schema_version": 1, "tool": "tools/k8s/failover_drill.py", "method": METHOD, "generated_at": datetime.now(UTC).isoformat(),
                   "drill_started_at": started.isoformat(), "complete": len(runs) == len(plan), "git": git_info(), "machine_spec": spec,
                   "chart_values": cv, "calendar": cal_info,
                   "parameters": {"seeded_cases_per_run": args.cases, "window_s": args.window, "kill_at_s": args.kill_at,
                                  "client_new_case_flows_per_s": args.client_rate, "force_runs": args.runs, "graceful_runs": args.graceful_runs, "hard_runs": args.hard_runs,
                                  "poll_interval_s": 0.15},
                   "summary": summarize(runs), "runs": runs}
            out_path.write_text(json.dumps(doc, indent=2, default=str))
        print(f"\nwrote {out_path}", flush=True)
    finally:
        stop_proxy()
        if cal_info.get("overridden") and not args.keep_calendar_override:
            print("restoring the cluster calendar ConfigMap", flush=True)
            await asyncio.to_thread(restore_calendar)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=5, help="force-delete iterations (--grace-period=0 --force)")
    ap.add_argument("--graceful-runs", type=int, default=0, help="additional iterations with an ordinary (graceful) delete")
    ap.add_argument("--hard-runs", type=int, default=0, help="additional iterations that SIGKILL both containers (crictl stop --timeout 0) "
                    "before the force delete, so no process can release its lease")
    ap.add_argument("--cases", type=int, default=3000, help="seeded open cases per iteration")
    ap.add_argument("--window", type=float, default=240.0, help="seconds over which the seeded triggers fall due")
    ap.add_argument("--kill-at", type=float, default=60.0, help="kill this many seconds after the trigger window opens")
    ap.add_argument("--client-rate", type=float, default=1.0, help="new case flows per second (each is 4 writes + 3 reads)")
    ap.add_argument("--out", default=str(ROOT / "docs/results/scale/failover.json"))
    ap.add_argument("--no-calendar-override", action="store_true")
    ap.add_argument("--keep-calendar-override", action="store_true")
    args = ap.parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
