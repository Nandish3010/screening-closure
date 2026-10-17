"""T-A31 against the running compose stack (`make test-db` with the stack up): the real scheduler is
beating, and the API reports a heartbeat age below twice its interval. Read-only: it writes nothing
to the development database (it authenticates with the watchdog service token, not a user)."""

import json
import os
import time
import urllib.error
import urllib.request

import pytest

from app.config import settings as app_settings

API = os.environ.get("TEST_API_URL", "http://localhost:8000").rstrip("/")


def get(path: str):
    req = urllib.request.Request(f"{API}{path}", headers={"Authorization": f"Bearer {app_settings.watchdog_service_token}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def stack_up() -> bool:
    try:
        urllib.request.urlopen(f"{API}/health", timeout=3)
        get("/heartbeats")
        return True
    except Exception:
        return False


pytestmark = [pytest.mark.integration, pytest.mark.skipif(not stack_up(), reason=f"compose API with A11 not reachable at {API}")]


def test_t_a31_the_running_scheduler_keeps_its_heartbeat_younger_than_twice_the_interval():
    deadline = time.monotonic() + 30
    while get("/heartbeats")["scheduler"] is None and time.monotonic() < deadline:
        time.sleep(1)
    first = get("/heartbeats")["scheduler"]
    assert first is not None, "the scheduler has not sent a heartbeat"
    interval = first["interval_seconds"]
    ages = []
    for _ in range(int(2.5 * interval)):
        beat = get("/heartbeats")["scheduler"]
        ages.append(beat["age_seconds"])
        time.sleep(1)
    assert max(ages) < 2 * interval, ages
    assert beat["sweep_ok"] is True


def test_t_a31_the_watchdog_beats_too_and_no_alert_is_open_while_both_run():
    beats = get("/heartbeats")
    assert beats["watchdog"] is not None and beats["watchdog"]["age_seconds"] < 60
    assert [a for a in get("/alerts?active=true") if a["kind"] in ("scheduler_down", "scheduler_failing", "watchdog_down")] == []
