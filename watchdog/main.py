"""Watchdog service. The dangerous failure here is the absence of events.

Every interval it asks the API for one scan (`POST /watchdog/scan`) with its own service token,
which the API accepts for nothing else and which may emit `watchdog_flagged` and nothing else.
The scan, which runs in the API next to the event store and on the simulation clock:

  1. flags open cases with no events for WATCHDOG_IDLE_DAYS (once per idle episode; any new
     event clears the flag; closed cases are never flagged), and
  2. judges the scheduler from the age of its latest heartbeat (wall clock, stored by the API):
     older than 60 seconds, or missing once this process has been up for 60 seconds, opens a
     `scheduler_down` row in `system_alerts` (`GET /alerts`); a fresh heartbeat clears it.

The scan also records this service's own heartbeat (`GET /heartbeats`). If the API is
unreachable the watchdog can only log it and try again: the compose healthcheck covers the API.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s watchdog %(levelname)s %(message)s")
log = logging.getLogger("watchdog")

API_URL = os.environ.get("API_URL", "http://localhost:8000")
# Must match the API's WATCHDOG_SERVICE_TOKEN. The default is the development value; the API
# refuses to start outside APP_ENV=dev while it still uses the default.
SERVICE_TOKEN = os.environ.get("WATCHDOG_SERVICE_TOKEN", "dev-only-watchdog-service-token")
IDLE_DAYS = int(os.environ.get("WATCHDOG_IDLE_DAYS", "7"))
INTERVAL_SECONDS = int(os.environ.get("WATCHDOG_INTERVAL_SECONDS", "10"))
STARTED_AT = time.monotonic()


def scan() -> dict | None:
    """POST /watchdog/scan; returns the API's summary, or None if it could not be done."""
    body = json.dumps({"uptime_seconds": time.monotonic() - STARTED_AT, "idle_days": IDLE_DAYS}).encode()
    request = urllib.request.Request(
        f"{API_URL}/watchdog/scan", data=body, method="POST",
        headers={"Authorization": f"Bearer {SERVICE_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        log.error("scan refused: HTTP %s %s", exc.code, exc.read()[:200])
    except Exception as exc:
        log.warning("scan failed (API unreachable?): %s", exc)
    return None


def main() -> None:
    log.info("starting; interval=%ss idle_days=%s api=%s", INTERVAL_SECONDS, IDLE_DAYS, API_URL)
    while True:
        result = scan()
        if result is not None:
            idle, scheduler = result["scan"], result["scheduler"]
            log.info("scan sim_time=%s evaluated=%s flagged=%s scheduler=%s",
                     idle["sim_time"], idle["cases_evaluated"], idle["flagged"], scheduler["status"])
            if scheduler["status"] in ("down", "failing"):
                log.error("ALERT scheduler %s: %s", scheduler["status"], scheduler.get("reason"))
            if idle["errors"]:  # e.g. cases written before the current rules that no longer replay
                log.error("scan skipped %d case(s) it could not read; first: %s", len(idle["errors"]), idle["errors"][0])
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
