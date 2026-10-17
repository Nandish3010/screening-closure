"""T-A32 (chaos): kill the scheduler container and watch the watchdog notice, then bring it back.

Runs against the compose stack with `make test-chaos` (not part of `make test` or `make test-db`):
it stops and starts the real `scheduler` container, so it needs docker on the host and the stack
up. It writes nothing to the development database: it reads `GET /alerts` and `GET /heartbeats`
with the watchdog service token and creates no cases.
"""

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from app.config import settings as app_settings

API = os.environ.get("TEST_API_URL", "http://localhost:8000").rstrip("/")
ROOT = Path(__file__).resolve().parents[2]
DETECT_WITHIN_SECONDS = 90
CLEAR_WITHIN_SECONDS = 60

pytestmark = pytest.mark.chaos


def get(path: str):
    req = urllib.request.Request(f"{API}{path}", headers={"Authorization": f"Bearer {app_settings.watchdog_service_token}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def compose(*args):
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True, capture_output=True, text=True, timeout=120)


def scheduler_alert():
    return next((a for a in get("/alerts") if a["kind"] == "scheduler_down" and a["cleared_at"] is None), None)


def wait_for(predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(1)
    return None


@pytest.fixture
def scheduler_stopped_then_started():
    try:
        get("/alerts")
    except Exception as exc:
        pytest.skip(f"compose stack with A11 not reachable at {API}: {exc}")
    # start from a healthy state: scheduler running, no open alert
    compose("start", "scheduler")
    assert wait_for(lambda: (get("/heartbeats")["scheduler"] or {"age_seconds": 999})["age_seconds"] < 20 and not scheduler_alert(), 60), \
        "the stack was not healthy before the test"
    yield
    compose("start", "scheduler")  # whatever happened, leave the scheduler running


def test_t_a32_a_dead_scheduler_raises_an_alert_within_90_seconds_and_a_restart_clears_it(scheduler_stopped_then_started):
    stopped_at = time.monotonic()
    compose("stop", "scheduler")

    alert = wait_for(scheduler_alert, DETECT_WITHIN_SECONDS)
    detected_after = time.monotonic() - stopped_at
    assert alert is not None, f"no scheduler_down alert within {DETECT_WITHIN_SECONDS} s of stopping the scheduler"
    assert alert["cleared_at"] is None and alert["since"]
    print(f"\nchaos: scheduler_down alert visible {detected_after:.1f} s after the container stopped")
    assert detected_after <= DETECT_WITHIN_SECONDS

    # the heartbeat is stale, the watchdog is still beating, and the alert is the only change
    beats = get("/heartbeats")
    assert beats["scheduler"]["age_seconds"] > 60 and beats["watchdog"]["age_seconds"] < 60

    started_at = time.monotonic()
    compose("start", "scheduler")
    cleared = wait_for(lambda: next((a for a in get("/alerts") if a["id"] == alert["id"] and a["cleared_at"]), None), CLEAR_WITHIN_SECONDS)
    cleared_after = time.monotonic() - started_at
    assert cleared is not None, f"the alert did not clear within {CLEAR_WITHIN_SECONDS} s of the restart"
    print(f"chaos: alert cleared {cleared_after:.1f} s after the restart")
    assert cleared["since"] == alert["since"] and cleared["cleared_at"] >= cleared["since"]
    assert scheduler_alert() is None
    # the restarted scheduler resumes sweeping: heartbeats are fresh and the sweep works
    beats = get("/heartbeats")
    assert beats["scheduler"]["age_seconds"] < 20 and beats["scheduler"]["sweep_ok"] is True
