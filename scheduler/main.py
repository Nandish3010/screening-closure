"""Scheduler service. Every interval it asks the API to run one SLA sweep, then writes a heartbeat.

The sweep itself (due dates on the working-day calendar, `mark_overdue`, the escalation
ladder) is `api/app/sweep.py`, run inside the API next to the event store and driven by the
API's simulation clock; the API splits it over `SWEEP_WORKERS` processes (A17), invisible here.
This process authenticates as `system:scheduler` with a service token
that the API restricts to `mark_overdue`, `escalate` and `watchdog_flagged`; it can never
close a case. Each loop it also asks the API to drain the send outbox (`POST /scheduler/outbox/drain`,
ADR-030): approved drafts whose send was interrupted go out exactly once.

Heartbeat (not a case event): posted to the API (`POST /scheduler/heartbeat`, stored in the
`heartbeats` table) on every loop, including when the sweep fails, with whether the sweep worked
and how many in a row have failed, so a scheduler that is alive but useless is distinguishable
from one that is dead. The watchdog reads its age from the API and raises an alert when it is
older than 60 seconds (A11). A JSON file in the shared volume is still written for debugging.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s scheduler %(levelname)s %(message)s")
log = logging.getLogger("scheduler")

API_URL = os.environ.get("API_URL", "http://localhost:8000")
HEARTBEAT_PATH = Path(os.environ.get("HEARTBEAT_PATH", "/tmp/scheduler.json"))
INTERVAL_SECONDS = int(os.environ.get("SCHEDULER_INTERVAL_SECONDS", "10"))
# Must match the API's SCHEDULER_SERVICE_TOKEN. The default is the development value; the API
# refuses to start outside APP_ENV=dev while it still uses the default.
SERVICE_TOKEN = os.environ.get("SCHEDULER_SERVICE_TOKEN", "dev-only-scheduler-service-token")


def run_sweep() -> dict | None:
    """POST /scheduler/sweep; returns the API's summary, or None if the sweep failed."""
    request = urllib.request.Request(
        f"{API_URL}/scheduler/sweep",
        data=b"",
        method="POST",
        headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
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
        headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        log.error("outbox drain refused: HTTP %s %s", exc.code, exc.read()[:200])
    except Exception as exc:
        log.warning("outbox drain failed: %s", exc)
    return None


def post_heartbeat(summary: dict | None, failures: int) -> None:
    """Tell the API this loop ran. A failure to reach it is logged, never fatal: the next loop
    tries again, and a missing heartbeat is exactly what the watchdog is there to notice."""
    body = json.dumps({
        "sweep_ok": summary is not None,
        "consecutive_failures": failures,
        "interval_seconds": INTERVAL_SECONDS,
        "sim_time": summary.get("sim_time") if summary else None,
    }).encode()
    request = urllib.request.Request(
        f"{API_URL}/scheduler/heartbeat", data=body, method="POST",
        headers={"Authorization": f"Bearer {SERVICE_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            resp.read()
    except Exception as exc:
        log.warning("heartbeat not delivered: %s", exc)


def write_heartbeat(summary: dict | None, last_ok_wall_ts: float | None) -> None:
    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = HEARTBEAT_PATH.with_suffix(".tmp")
    beat = {
        "wall_ts": time.time(),  # wall-clock-ok: liveness heartbeat only, never a decision
        "sim_time": summary.get("sim_time") if summary else None,
        "sweep_ok": summary is not None,
        "last_ok_wall_ts": last_ok_wall_ts,
        "last_sweep": summary,
    }
    tmp.write_text(json.dumps(beat))
    tmp.replace(HEARTBEAT_PATH)  # atomic


def main() -> None:
    log.info("starting; interval=%ss heartbeat=%s api=%s", INTERVAL_SECONDS, HEARTBEAT_PATH, API_URL)
    last_ok = None
    failures = 0
    while True:
        summary = run_sweep()
        failures = 0 if summary is not None else failures + 1
        if summary is not None:
            last_ok = time.time()  # wall-clock-ok: liveness heartbeat only
            log.info(
                "sweep sim_time=%s workers=%s evaluated=%s mark_overdue=%s escalate=%s handoff_bounced=%s rejected=%s errors=%s",
                summary["sim_time"], summary.get("workers", 1), summary["cases_evaluated"], summary["mark_overdue"],
                summary["escalate"], summary.get("handoff_bounced", 0), summary["rejected"],
                len(summary["errors"]),
            )
        drained = drain_outbox()
        if drained and drained.get("drained"):
            log.info("outbox drained=%s sent=%s blocked=%s still_pending=%s", drained["drained"], drained["sent"],
                     drained["blocked"], drained["pending"])
        write_heartbeat(summary, last_ok)
        post_heartbeat(summary, failures)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
