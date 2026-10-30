"""Scheduler service. Every interval it asks the API to run one SLA sweep, then writes a heartbeat.

The sweep itself (due dates on the working-day calendar, `mark_overdue`, the escalation
ladder) is `api/app/sweep.py`, run inside the API next to the event store and driven by the
API's simulation clock; the API splits it over `SWEEP_WORKERS` processes (A17), invisible here.
This process authenticates as `system:scheduler` with a service token
that the API restricts to `mark_overdue`, `escalate` and `watchdog_flagged`; it can never
close a case. Each loop it also asks the API to drain the send outbox (`POST /scheduler/outbox/drain`,
ADR-030): approved drafts whose send was interrupted go out exactly once.

Cadence (M7-8, ADR-047). The sweep, the automation scan, the evidence scan and the agency run are SEPARATE timed tasks, each a
fixed-rate schedule in its own thread (`Ticker`): a task STARTS every `interval` seconds, measured from its own first start, not
`interval` seconds after its last end. A tick that is still running when the next one is due is not run twice and is not made up
later: the missed ticks are skipped and counted, and the next one starts on the grid. The tasks are gated on leadership and each
carries the fencing token on every call, so a deposed leader is refused (HTTP 409) exactly as before.

Each task asks the API to run the automation scans (A21, ADR-032): `POST /scheduler/automation-scan`
(acknowledge and assign anything waiting, re-route rejected referrals, send the due pre-approved messages) and
`POST /scheduler/evidence-scan` (find the colonoscopy in HAPI, propose the closure). Both do the work as the
API's `system:automation` actor and answer `{"enabled": false}` unless the API runs with AUTOMATION_ENABLED=true.

Heartbeat (not a case event): posted to the API (`POST /scheduler/heartbeat`, stored in the
`heartbeats` table) at the START of every sweep tick (before the sweep), every few seconds from a thread while
the sweep runs and while any scan or agency run runs, and when the sweep tick ends, including when the sweep fails, with whether the last sweep worked
and how many in a row have failed, so a scheduler that is alive but useless is distinguishable from one
that is dead. A sweep over a large cohort can take longer than the watchdog's 60-second limit; a beat only
at the end of the loop made a long sweep look like a dead scheduler (`scheduler_down`), which is a false
alarm that teaches people to ignore the real one (SEC-2). The watchdog reads the age from the API and raises
an alert when it is older than 60 seconds (A11). A JSON file in the shared volume is still written for
debugging.
"""

import collections
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s scheduler %(levelname)s %(message)s")
log = logging.getLogger("scheduler")

API_URL = os.environ.get("API_URL", "http://localhost:8000")
HEARTBEAT_PATH = Path(os.environ.get("HEARTBEAT_PATH", "/tmp/scheduler.json"))
INTERVAL_SECONDS = int(os.environ.get("SCHEDULER_INTERVAL_SECONDS", "10"))
# M7-8 (ADR-047): the other periodic jobs have their own start-to-start interval. The scans default to the sweep's, the agency
# to "never" (0): a stack that does not ask for scheduled agency runs gets none (the API's AGENCY_EVERY_SWEEPS is the other switch).
AUTOMATION_INTERVAL_SECONDS = float(os.environ.get("SCHEDULER_AUTOMATION_INTERVAL_SECONDS", str(INTERVAL_SECONDS)))
EVIDENCE_INTERVAL_SECONDS = float(os.environ.get("SCHEDULER_EVIDENCE_INTERVAL_SECONDS", str(INTERVAL_SECONDS)))
AGENCY_INTERVAL_SECONDS = float(os.environ.get("SCHEDULER_AGENCY_INTERVAL_SECONDS", "0"))
# How often the cadence of every task is written to the log (start counts, skipped ticks, start-to-start periods).
CADENCE_LOG_SECONDS = float(os.environ.get("SCHEDULER_CADENCE_LOG_SECONDS", "300"))
# How often a heartbeat is posted while a sweep is running. Well under the watchdog's 60 s limit.
BEAT_SECONDS = float(os.environ.get("SCHEDULER_HEARTBEAT_SECONDS", "15"))
# A follower asks for the lease this often (seconds), so a dead leader is replaced about one lease TTL after its last renewal.
FOLLOWER_POLL_SECONDS = float(os.environ.get("SCHEDULER_FOLLOWER_POLL_SECONDS", "2"))
# Must match the API's SCHEDULER_SERVICE_TOKEN. The default is the development value; the API
# refuses to start outside APP_ENV=dev while it still uses the default.
SERVICE_TOKEN = os.environ.get("SCHEDULER_SERVICE_TOKEN", "dev-only-scheduler-service-token")

# Leader election (A31, ADR-041). Several scheduler instances may run; each asks the API for the "scheduler" lease at the top
# of every loop. Only the holder sweeps, drains and scans; the others beat as `follower` and idle until the lease frees.
# HOLDER names this instance; FENCE carries the current fencing token on every call, so the API refuses a deposed
# leader's late call (HTTP 409). Set by `acquire_lease`; read by `_headers`.
HOLDER = os.environ.get("SCHEDULER_HOLDER") or f"{socket.gethostname()[:40]}-{uuid.uuid4().hex[:8]}"
FENCE: dict[str, str] = {}
LEASE_TTL_GUESS = 9.0  # until the first answer tells the keeper the real TTL (the API default is 30)


def _headers(extra: dict | None = None) -> dict:
    return {"Authorization": f"Bearer {SERVICE_TOKEN}", **FENCE, **(extra or {})}


def acquire_lease() -> dict | None:
    """POST /scheduler/lease: acquire or renew. Returns the API's answer (`leader`, `token`), or None if the API could not
    be reached or answered badly (the caller idles this loop: not knowing is not leading). A win sets FENCE; a loss or a
    failure leaves the OLD token in place on purpose, so a late call from a deposed leader is refused as stale by the API
    instead of passing as an un-fenced one."""
    request = urllib.request.Request(
        f"{API_URL}/scheduler/lease", data=json.dumps({"holder": HOLDER}).encode(), method="POST",
        headers={"Authorization": f"Bearer {SERVICE_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            answer = json.load(resp)
    except Exception as exc:
        log.warning("lease not acquired: %s", exc)
        return None
    if answer.get("leader"):
        FENCE.update({"X-Leader-Holder": HOLDER, "X-Leader-Token": str(answer["token"])})
    return answer


class LeaseKeeper(threading.Thread):
    """Renews the lease every ttl/3 for the life of the process, through the sleep between loops and through a long sweep,
    so the lease lapses only when this process is really gone or cannot reach the API (then a follower takes over)."""

    def __init__(self) -> None:
        super().__init__(name="scheduler-lease", daemon=True)
        self._stop_event = threading.Event()
        self.ttl = LEASE_TTL_GUESS

    def run(self) -> None:
        while not self._stop_event.wait(max(self.ttl / 3, 0.5)):
            answer = acquire_lease()
            if answer and answer.get("ttl_seconds"):
                self.ttl = float(answer["ttl_seconds"])


def release_lease() -> None:
    """Best effort on a clean shutdown, so a follower takes over at its next loop instead of after the TTL."""
    request = urllib.request.Request(
        f"{API_URL}/scheduler/lease/release", data=json.dumps({"holder": HOLDER}).encode(), method="POST",
        headers={"Authorization": f"Bearer {SERVICE_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(request, timeout=5).read()
    except Exception as exc:
        log.warning("lease not released: %s", exc)


def run_sweep() -> dict | None:
    """POST /scheduler/sweep; returns the API's summary, or None if the sweep failed."""
    request = urllib.request.Request(
        f"{API_URL}/scheduler/sweep",
        data=b"",
        method="POST",
        headers=_headers(),
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        log.error("sweep refused: HTTP %s %s", exc.code, exc.read()[:200])
    except Exception as exc:  # keep looping; the watchdog reports a stale or failing heartbeat
        log.warning("sweep failed: %s", exc)
    return None


def drain_outbox() -> dict | None:
    """POST /scheduler/outbox/drain: send approved drafts that have not gone out (ADR-030). The approve
    request drains its own draft; this finishes any whose drain never ran (the API died after the approval
    committed). A failure is logged and retried next loop, never fatal: the row stays pending."""
    request = urllib.request.Request(
        f"{API_URL}/scheduler/outbox/drain", data=b"", method="POST",
        headers=_headers(),
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        log.error("outbox drain refused: HTTP %s %s", exc.code, exc.read()[:200])
    except Exception as exc:
        log.warning("outbox drain failed: %s", exc)
    return None


def run_automation(path: str) -> dict | None:
    """POST /scheduler/<path> (automation-scan or evidence-scan, A21): returns the API's counts, or None on failure. A
    failure is logged and retried next loop, never fatal and never a reason to skip the sweep."""
    request = urllib.request.Request(
        f"{API_URL}/scheduler/{path}", data=b"", method="POST",
        headers=_headers(),
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        log.error("%s refused: HTTP %s %s", path, exc.code, exc.read()[:200])
    except Exception as exc:
        log.warning("%s failed: %s", path, exc)
    return None


def run_agency() -> dict | None:
    """POST /scheduler/agency-run: one agency run on the scheduler's own cadence (ADR-047). Counts only; None on failure."""
    request = urllib.request.Request(
        f"{API_URL}/scheduler/agency-run", data=b"", method="POST",
        headers=_headers(),
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        log.error("agency-run refused: HTTP %s %s", exc.code, exc.read()[:200])
    except Exception as exc:
        log.warning("agency-run failed: %s", exc)
    return None


def post_heartbeat(sweep_ok: bool, failures: int, sim_time: str | None = None, sweeping: bool = False,
                   role: str = "leader") -> None:
    """Tell the API this loop is alive. A failure to reach it is logged, never fatal: the next beat
    tries again, and a missing heartbeat is exactly what the watchdog is there to notice."""
    body = json.dumps({
        "sweep_ok": sweep_ok,
        "consecutive_failures": failures,
        "interval_seconds": INTERVAL_SECONDS,
        "sim_time": sim_time,
        "sweeping": sweeping,
        "role": role,
        "holder": HOLDER,
        **({"fencing_token": int(FENCE["X-Leader-Token"])} if FENCE else {}),
    }).encode()
    request = urllib.request.Request(
        f"{API_URL}/scheduler/heartbeat", data=body, method="POST",
        headers=_headers({"Content-Type": "application/json"}),
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            resp.read()
    except Exception as exc:
        log.warning("heartbeat not delivered: %s", exc)


class BeatWhile:
    """Context manager: posts a heartbeat every BEAT_SECONDS from a thread until the block ends. Used
    around the sweep so a sweep longer than the watchdog's limit does not look like a dead scheduler."""

    def __init__(self, sweep_ok: bool, failures: int) -> None:
        self._args = (sweep_ok, failures)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="scheduler-heartbeat", daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(BEAT_SECONDS):
            post_heartbeat(*self._args, sweeping=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=15)


def write_heartbeat(summary: dict | None, last_ok_wall_ts: float | None, role: str = "leader") -> None:
    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = HEARTBEAT_PATH.with_suffix(".tmp")
    beat = {
        "wall_ts": time.time(),  # wall-clock-ok: liveness heartbeat only, never a decision
        "role": role,
        "holder": HOLDER,
        "sim_time": summary.get("sim_time") if summary else None,
        "sweep_ok": summary is not None,
        "last_ok_wall_ts": last_ok_wall_ts,
        "last_sweep": summary,
    }
    tmp.write_text(json.dumps(beat))
    tmp.replace(HEARTBEAT_PATH)  # atomic


class LoopState:
    """What the tasks remember between ticks. Written by the lease loop (`was_leader`) and the sweep task (the rest)."""

    def __init__(self) -> None:
        self.last_ok: float | None = None  # wall time of the last good sweep (debug file only)
        self.sweep_ok = True  # did the last sweep work? (nothing has failed before the first one)
        self.failures = 0
        self.was_leader = False


class Ticker(threading.Thread):
    """A fixed-rate timed task (ADR-047). Tick k is due at `first + k * interval` on the monotonic clock; the task STARTS then, so
    the period is the interval whatever the work takes, as long as the work takes less than one interval.

    * No overlap with itself: the task runs on this one thread, so a tick that is due while the last is still running waits for it.
    * No burst: when a tick ends late, the grid points that passed meanwhile are SKIPPED (counted in `skipped`), not run back to
      back to catch up. The next tick is the first grid point after now.
    * `work` returns False when it did nothing (not the leader): that tick is not a start and is not recorded.
    * An exception is logged and the schedule goes on.
    """

    def __init__(self, name: str, interval: float, work, offset: float = 0.0, now=time.monotonic,
                 stop: threading.Event | None = None, wait=None) -> None:
        super().__init__(name=f"scheduler-{name}", daemon=True)
        self.task, self.interval, self.work, self.offset, self._now = name, float(interval), work, float(offset), now
        self._stop_event = stop or threading.Event()
        self._wait = wait or self._stop_event.wait  # wait(seconds) -> True when asked to stop (tests inject a fake clock)
        self.starts: collections.deque[float] = collections.deque(maxlen=100000)  # monotonic start of every tick that did work
        self.durations: collections.deque[float] = collections.deque(maxlen=100000)
        self.ticks = 0  # grid points that were run (leader or not)
        self.skipped = 0  # grid points skipped because the previous tick overran them
        self.started_ticks = 0
        self.errors = 0

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        next_at = self._now() + self.offset
        while not self._stop_event.is_set():
            delay = next_at - self._now()
            if delay > 0 and self._wait(delay):
                break
            started = self._now()
            self.ticks += 1
            try:
                did = self.work()
            except Exception:  # noqa: BLE001 - a task that raises must not end its schedule
                self.errors += 1
                log.exception("task %s failed", self.task)
                did = True
            ended = self._now()
            if did is not False:
                self.started_ticks += 1
                self.starts.append(started)
                self.durations.append(ended - started)
                log.info("tick task=%s n=%d start=%.3f duration=%.3f skipped_so_far=%d", self.task, self.started_ticks,
                         time.time(), ended - started, self.skipped)  # wall-clock-ok: cadence measurement label, never a decision
            next_at += self.interval
            if next_at <= ended:
                missed = int((ended - next_at) // self.interval) + 1
                self.skipped += missed
                next_at += missed * self.interval

    def cadence(self) -> dict:
        """The achieved schedule: starts, the gaps between them, and the skipped ticks."""
        starts = list(self.starts)
        gaps = sorted(b - a for a, b in zip(starts, starts[1:]))
        durations = sorted(self.durations)

        def pct(xs, q):
            return round(xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))], 3) if xs else None

        return {"task": self.task, "interval": self.interval, "starts": len(starts), "skipped": self.skipped,
                "errors": self.errors, "gap_mean": round(sum(gaps) / len(gaps), 3) if gaps else None,
                "gap_p95": pct(gaps, 0.95), "gap_max": round(gaps[-1], 3) if gaps else None,
                "duration_p50": pct(durations, 0.5), "duration_p95": pct(durations, 0.95),
                "duration_max": round(durations[-1], 3) if durations else None}


def lease_step(state: LoopState) -> bool:
    """Ask for the lease (the lease loop, every few seconds). Returns True for the leader. A follower beats as `follower` and writes
    the liveness file (role=follower), so the pod's probe can require a fresh file from every instance."""
    lease = acquire_lease()
    if lease is None or not lease.get("leader"):
        # A follower (or an instance that cannot reach the API): idle. Sweeping, draining and scanning belong to the
        # leader; doing them here would only race it.
        if state.was_leader:
            log.info("no longer the leader (lease held by %s)", (lease or {}).get("leader_holder"))
        state.was_leader = False
        write_heartbeat(None, state.last_ok, role="follower")
        post_heartbeat(True, 0, role="follower")
        return False
    if not state.was_leader:
        log.info("this instance (%s) is the leader, fencing token %s", HOLDER, lease["token"])
    state.was_leader = True
    return True


def sweep_tick(state: LoopState) -> dict | bool | None:
    """One sweep tick: heartbeat first (alive, about to sweep), drain the outbox, sweep with a heartbeat thread running, then the
    heartbeat that says how it went. False when this instance is not the leader."""
    if not state.was_leader:
        return False
    post_heartbeat(state.sweep_ok, state.failures, sweeping=True)
    drained = drain_outbox()
    if drained and drained.get("drained"):
        log.info("outbox drained=%s sent=%s blocked=%s still_pending=%s", drained["drained"], drained["sent"],
                 drained["blocked"], drained["pending"])
    with BeatWhile(state.sweep_ok, state.failures):
        summary = run_sweep()
    state.sweep_ok = summary is not None
    state.failures = 0 if summary is not None else state.failures + 1
    if summary is not None:
        state.last_ok = time.time()  # wall-clock-ok: liveness heartbeat only
        log.info(
            "sweep sim_time=%s workers=%s evaluated=%s mark_overdue=%s escalate=%s handoff_bounced=%s rejected=%s errors=%s",
            summary["sim_time"], summary.get("workers", 1), summary["cases_evaluated"], summary["mark_overdue"],
            summary["escalate"], summary.get("handoff_bounced", 0), summary["rejected"],
            len(summary["errors"]),
        )
    write_heartbeat(summary, state.last_ok)
    post_heartbeat(state.sweep_ok, state.failures, summary.get("sim_time") if summary else None)
    return summary


def scan_tick(state: LoopState, scan: str) -> bool | None:
    """One automation or evidence scan, on its own schedule. It does not decide whether the sweep worked (the heartbeat is
    about the sweep). It runs inside the heartbeat thread: a scan over a large cohort, or a HAPI that answers slowly, can take
    longer than the watchdog's 60 seconds, and a busy scheduler must not look dead (`scheduler_down`)."""
    if not state.was_leader:
        return False
    with BeatWhile(state.sweep_ok, state.failures):
        counts = run_automation(scan)
    if counts and counts.get("enabled"):
        log.info("%s %s", scan, {k: v for k, v in counts.items() if k not in ("enabled", "errors")} or "nothing to do")
        if counts.get("errors"):
            log.warning("%s errors=%s", scan, len(counts["errors"]))
    return None


def agency_tick(state: LoopState) -> bool | None:
    """One agency run on its own schedule (ADR-047). Labour, not authority: the API answers `{"enabled": false}` unless the
    agency is switched on, and the run writes no case event as the agency."""
    if not state.was_leader:
        return False
    with BeatWhile(state.sweep_ok, state.failures):
        out = run_agency()
    if out and out.get("enabled"):
        log.info("agency-run %s", {k: v for k, v in out.items() if k != "enabled"})
    return None


def loop_once(state: LoopState) -> dict | None:
    """One synchronous pass of every task, in the old order (lease, sweep, automation scan, evidence scan). The service itself no
    longer loops like this (the tasks are timed separately, see `Ticker`); this is for a manual run and for tests that drive a
    scheduler instance step by step. Returns the sweep's summary, or None for a follower or a failed sweep."""
    if not lease_step(state):
        return None
    summary = sweep_tick(state)
    for scan in ("automation-scan", "evidence-scan"):
        scan_tick(state, scan)
    return summary


def build_tickers(state: LoopState, stop: threading.Event | None = None) -> list[Ticker]:
    """The timed tasks of this process. The scans and the agency start a little after the sweep so the first ticks do not coincide
    (the schedule is fixed-rate, so the offsets stay)."""
    tickers = [Ticker("sweep", INTERVAL_SECONDS, lambda: sweep_tick(state), stop=stop)]
    if AUTOMATION_INTERVAL_SECONDS > 0:
        tickers.append(Ticker("automation-scan", AUTOMATION_INTERVAL_SECONDS, lambda: scan_tick(state, "automation-scan"),
                              offset=min(2.0, AUTOMATION_INTERVAL_SECONDS / 4), stop=stop))
    if EVIDENCE_INTERVAL_SECONDS > 0:
        tickers.append(Ticker("evidence-scan", EVIDENCE_INTERVAL_SECONDS, lambda: scan_tick(state, "evidence-scan"),
                              offset=min(4.0, EVIDENCE_INTERVAL_SECONDS / 2), stop=stop))
    if AGENCY_INTERVAL_SECONDS > 0:
        tickers.append(Ticker("agency", AGENCY_INTERVAL_SECONDS, lambda: agency_tick(state),
                              offset=min(6.0, AGENCY_INTERVAL_SECONDS / 2), stop=stop))
    return tickers


def log_cadence(tickers: list[Ticker]) -> None:
    for t in tickers:
        log.info("cadence %s", json.dumps(t.cadence()))


def main() -> None:
    log.info("starting; holder=%s interval=%ss automation=%ss evidence=%ss agency=%ss heartbeat=%s api=%s", HOLDER,
             INTERVAL_SECONDS, AUTOMATION_INTERVAL_SECONDS, EVIDENCE_INTERVAL_SECONDS, AGENCY_INTERVAL_SECONDS,
             HEARTBEAT_PATH, API_URL)
    state = LoopState()
    LeaseKeeper().start()
    signal.signal(signal.SIGTERM, lambda *_: (release_lease(), sys.exit(0)))  # docker stop: hand the lease over at once
    lease_step(state)  # the first answer before the first tick, so a leader's first sweep is not skipped
    tickers = build_tickers(state)
    for t in tickers:
        t.start()
    if CADENCE_LOG_SECONDS > 0:  # the achieved cadence of every task, as a timed task of its own
        Ticker("cadence-log", CADENCE_LOG_SECONDS, lambda: log_cadence(tickers), offset=CADENCE_LOG_SECONDS).start()
    while True:
        # The lease loop: a follower polls every few seconds, so a dead leader is replaced about one TTL after its last renewal.
        time.sleep(min(INTERVAL_SECONDS, FOLLOWER_POLL_SECONDS))
        lease_step(state)


if __name__ == "__main__":
    main()
