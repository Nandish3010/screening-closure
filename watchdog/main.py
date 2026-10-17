"""Watchdog stub. The dangerous failure here is the absence of events.

Checks (both TODO beyond the heartbeat):
  1. TODO: flag open cases with no events for WATCHDOG_IDLE_DAYS (sim clock).
  2. Flag the scheduler itself if its heartbeat is older than 60 seconds (wall clock).
"""

import json
import logging
import os
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s watchdog %(levelname)s %(message)s")
log = logging.getLogger("watchdog")

API_URL = os.environ.get("API_URL", "http://localhost:8000")
HEARTBEAT_PATH = Path(os.environ.get("HEARTBEAT_PATH", "/tmp/scheduler.json"))
IDLE_DAYS = int(os.environ.get("WATCHDOG_IDLE_DAYS", "7"))
INTERVAL_SECONDS = int(os.environ.get("WATCHDOG_INTERVAL_SECONDS", "15"))
HEARTBEAT_MAX_AGE_SECONDS = 60


def check_scheduler_heartbeat() -> None:
    try:
        beat = json.loads(HEARTBEAT_PATH.read_text())
        age = time.time() - beat["wall_ts"]
    except (FileNotFoundError, ValueError, KeyError):
        log.error("ALERT scheduler heartbeat missing or unreadable at %s", HEARTBEAT_PATH)
        return
    if age > HEARTBEAT_MAX_AGE_SECONDS:
        log.error("ALERT scheduler heartbeat is %.0fs old (limit %ss): scheduler may be down",
                  age, HEARTBEAT_MAX_AGE_SECONDS)
    else:
        log.info("scheduler heartbeat ok (%.0fs old)", age)


def check_idle_cases() -> None:
    # TODO: list open cases via the API; flag any whose last event is older than
    # IDLE_DAYS on the sim clock. Silence is the failure mode we are watching for.
    log.info("idle-case check not implemented yet (threshold=%s days)", IDLE_DAYS)


def main() -> None:
    log.info("starting; interval=%ss api=%s", INTERVAL_SECONDS, API_URL)
    while True:
        check_scheduler_heartbeat()
        check_idle_cases()
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
