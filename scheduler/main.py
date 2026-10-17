"""Scheduler service. Every interval it asks the API to run one SLA sweep, then writes a heartbeat.

The sweep itself (due dates on the working-day calendar, `mark_overdue`, the escalation
ladder) is `api/app/sweep.py`, run inside the API next to the event store and driven by the
API's simulation clock. This process authenticates as `system:scheduler` with a service token
that the API restricts to `mark_overdue`, `escalate` and `watchdog_flagged`; it can never
close a case.

Heartbeat (not a case event): a JSON file the watchdog reads. It is refreshed on every loop,
including when the sweep fails, and records whether the last sweep worked, so a scheduler that
is alive but useless is distinguishable from one that is dead.
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
    while True:
        summary = run_sweep()
        if summary is not None:
            last_ok = time.time()  # wall-clock-ok: liveness heartbeat only
            log.info(
                "sweep sim_time=%s evaluated=%s mark_overdue=%s escalate=%s rejected=%s errors=%s",
                summary["sim_time"], summary["cases_evaluated"], summary["mark_overdue"],
                summary["escalate"], summary["rejected"], len(summary["errors"]),
            )
        write_heartbeat(summary, last_ok)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
