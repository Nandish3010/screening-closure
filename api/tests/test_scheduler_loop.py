"""SEC-2 fix 6: the scheduler posts its heartbeat before the sweep and while it runs, so a sweep longer than
the watchdog's 60-second limit does not raise `scheduler_down` (a false alarm hides the real one).

`scheduler/main.py` is loaded by path (it is a service script, not a package); its network calls are replaced."""

import importlib.util
import threading
import time
from pathlib import Path

import pytest

from api_helpers import make_client
from app import ops
from app.config import settings as app_settings

MAIN = Path(__file__).resolve().parents[2] / "scheduler" / "main.py"


@pytest.fixture
def sched(monkeypatch):
    spec = importlib.util.spec_from_file_location("scheduler_main_under_test", MAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.calls = []
    lock = threading.Lock()

    def record(name):
        def _f(*args, **kwargs):
            with lock:
                mod.calls.append((name, args, kwargs))
        return _f

    monkeypatch.setattr(mod, "post_heartbeat", lambda *a, **k: record("beat")(*a, **k))
    monkeypatch.setattr(mod, "write_heartbeat", record("write"))
    monkeypatch.setattr(mod, "drain_outbox", lambda: record("drain")() or None)
    monkeypatch.setattr(mod, "BEAT_SECONDS", 0.05)
    return mod


def names(mod):
    return [c[0] for c in mod.calls]


def test_a_heartbeat_is_posted_before_the_sweep_starts(sched, monkeypatch):
    def sweep():
        sched.calls.append(("sweep", (), {}))
        return {"sim_time": "2026-01-05T08:00:00+00:00", "cases_evaluated": 0, "mark_overdue": 0, "escalate": 0,
                "rejected": 0, "errors": []}

    monkeypatch.setattr(sched, "run_sweep", sweep)
    sched.loop_once(sched.LoopState())
    order = names(sched)
    assert order[0] == "beat" and order.index("beat") < order.index("sweep")
    assert order.index("drain") < order.index("sweep")  # approved drafts are not stuck behind a long sweep
    assert sched.calls[0][2] == {"sweeping": True}


def test_heartbeats_keep_coming_while_a_long_sweep_runs_and_stop_after_it(sched, monkeypatch):
    def long_sweep():
        sched.calls.append(("sweep-start", (), {}))
        time.sleep(0.4)  # several beat periods
        sched.calls.append(("sweep-end", (), {}))
        return {"sim_time": "2026-01-05T08:00:00+00:00", "cases_evaluated": 5000, "mark_overdue": 0, "escalate": 0,
                "rejected": 0, "errors": []}

    monkeypatch.setattr(sched, "run_sweep", long_sweep)
    sched.loop_once(sched.LoopState())
    start, end = names(sched).index("sweep-start"), names(sched).index("sweep-end")
    during = [c for c in sched.calls[start:end] if c[0] == "beat"]
    assert len(during) >= 3 and all(c[2] == {"sweeping": True} for c in during)
    after = len(sched.calls)
    time.sleep(0.2)
    assert len(sched.calls) == after  # the thread is stopped with the sweep
    final = sched.calls[-1]
    assert final[0] == "beat" and final[1][:2] == (True, 0) and final[2] == {}  # the closing beat says it worked


def test_a_failed_sweep_is_still_reported_and_counted(sched, monkeypatch):
    monkeypatch.setattr(sched, "run_sweep", lambda: None)
    state = sched.LoopState()
    sched.loop_once(state)
    sched.loop_once(state)
    assert state.failures == 2 and state.sweep_ok is False
    beats = [c for c in sched.calls if c[0] == "beat"]
    assert beats[-1][1][:2] == (False, 2)
    # the next loop's first beat already carries the failure count (the watchdog's "failing" verdict needs it)
    assert [c for c in beats if c[2] == {"sweeping": True}][-1][1][:2] == (False, 1)


# ---- the API side: what the watchdog concludes ----------------------------------------------------------------


class Wall:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_a_long_sweep_with_beats_is_ok_and_without_them_is_down(monkeypatch):
    wall = Wall()
    store = ops.InMemoryOpsStore(wall=wall)
    monkeypatch.setattr(ops, "store", store)
    client = make_client()
    sched = {"Authorization": f"Bearer {app_settings.scheduler_service_token}"}

    def beat(**body):
        assert client.post("/scheduler/heartbeat", json={"sweep_ok": True, "consecutive_failures": 0, **body}, headers=sched).status_code == 200

    # a 150-second sweep, beating every 15 seconds from its thread
    beat(sweeping=True)
    for _ in range(10):
        wall.now += 15
        beat(sweeping=True)
        status, _detail = ops.scheduler_status(store.heartbeats()["scheduler"], watchdog_uptime_seconds=600)
        assert status == "ok"
    assert store.heartbeats()["scheduler"].info["sweeping"] is True
    # the same sweep with a beat only at the end of the loop: the watchdog would have called it dead at +61 s
    quiet = ops.InMemoryOpsStore(wall=wall)
    quiet.beat("scheduler", {"sweep_ok": True})
    wall.now += 61
    assert ops.scheduler_status(quiet.heartbeats()["scheduler"], 600)[0] == "down"


def test_the_heartbeat_body_still_rejects_unknown_fields():
    client = make_client()
    sched = {"Authorization": f"Bearer {app_settings.scheduler_service_token}"}
    assert client.post("/scheduler/heartbeat", json={"sweep_ok": True, "pwn": 1}, headers=sched).status_code == 422
