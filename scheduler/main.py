"""Scheduler stub. Reads the sim clock from the API and writes a heartbeat.

TODO: evaluate SLAs for every open case against the sim clock and emit
`overdue` / `escalated` events (never `close`; automated actors cannot close).
"""

import json
import logging
import os
import time
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s scheduler %(levelname)s %(message)s")
log = logging.getLogger("scheduler")

API_URL = os.environ.get("API_URL", "http://localhost:8000")
HEARTBEAT_PATH = Path(os.environ.get("HEARTBEAT_PATH", "/tmp/scheduler.json"))
INTERVAL_SECONDS = int(os.environ.get("SCHEDULER_INTERVAL_SECONDS", "10"))


def read_sim_time() -> str | None:
    try:
        with urllib.request.urlopen(f"{API_URL}/health", timeout=3) as resp:
            return json.load(resp).get("sim_time")
    except Exception as exc:  # keep sweeping; the watchdog reports a stale heartbeat
        log.warning("could not read sim clock: %s", exc)
        return None


def write_heartbeat(sim_time: str | None) -> None:
    HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = HEARTBEAT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"wall_ts": time.time(), "sim_time": sim_time}))
    tmp.replace(HEARTBEAT_PATH)  # atomic


def main() -> None:
    log.info("starting; interval=%ss heartbeat=%s", INTERVAL_SECONDS, HEARTBEAT_PATH)
    while True:
        sim_time = read_sim_time()
        log.info("sweep sim_time=%s", sim_time)
        # TODO: evaluate SLAs, emit overdue/escalated events
        write_heartbeat(sim_time)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
