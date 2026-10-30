"""M7-8 (ADR-047): the scheduler's tasks are fixed-rate timed tasks. The sweep STARTS every interval, a late tick is skipped
rather than run back to back, a task never overlaps itself, leadership gates the work, and the tasks are independent of each other.

`scheduler/main.py` is loaded by path (it is a service script, not a package). The clock is a fake one: waiting advances it and
work takes the time the test says, so the schedule is checked exactly, not by sleeping."""

import importlib.util
import threading
import time
from pathlib import Path

import pytest

MAIN = Path(__file__).resolve().parents[2] / "scheduler" / "main.py"


@pytest.fixture
def sched():
    spec = importlib.util.spec_from_file_location("scheduler_main_cadence", MAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeClock:
    """Time moves only when the task waits or works. `stop_after` ends the run after that many ticks of work."""

    def __init__(self, work_seconds, stop_after):
        self.t = 1000.0
        self.work_seconds = work_seconds
        self.stop_after = stop_after
        self.starts = []
        self.running = 0
        self.max_running = 0

    def now(self):
        return self.t

    def wait(self, delay):
        self.t += delay
        return len(self.starts) >= self.stop_after

    def work(self):
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        self.starts.append(self.t)
        self.t += self.work_seconds.pop(0) if isinstance(self.work_seconds, list) else self.work_seconds
        self.running -= 1


def run_ticker(sched, interval, work_seconds, stop_after, offset=0.0):
    fake = FakeClock(work_seconds, stop_after)
    ticker = sched.Ticker("sweep", interval, fake.work, offset=offset, now=fake.now, wait=fake.wait)
    # run() ends when wait() answers True, which is checked before each tick; one extra pass to observe the stop.
    ticker.run()
    return ticker, fake


def test_the_sweep_starts_every_interval_even_when_it_takes_a_good_part_of_it(sched):
    """'Start every 10 s', not '10 s sleep plus work': 3 s of work per tick still gives starts exactly 10 s apart."""
    ticker, fake = run_ticker(sched, 10, 3.0, stop_after=20)
    gaps = [b - a for a, b in zip(fake.starts, fake.starts[1:])]
    assert gaps and all(abs(g - 10.0) < 1e-9 for g in gaps)
    assert ticker.skipped == 0 and ticker.started_ticks == 20
    assert ticker.cadence()["gap_max"] == 10.0


def test_a_tick_that_overruns_is_skipped_not_made_up_and_the_grid_is_kept(sched):
    """One tick takes 27 s: the grid points at +10 and +20 pass while it runs and are SKIPPED (no burst of catch-up runs); the next
    start is the next grid point, +30 after the first, so the schedule keeps its phase."""
    ticker, fake = run_ticker(sched, 10, [1.0, 27.0, 1.0, 1.0, 1.0], stop_after=5)
    first = fake.starts[0]
    assert [round(s - first, 6) for s in fake.starts] == [0.0, 10.0, 40.0, 50.0, 60.0]
    assert ticker.skipped == 2  # +20 and +30 are due while the 27 s tick (started +10, ended +37) is running: +20, +30
    assert fake.max_running == 1


def test_a_tick_never_overlaps_itself(sched):
    ticker, fake = run_ticker(sched, 10, 35.0, stop_after=4)
    assert fake.max_running == 1
    assert all(b - a >= 35.0 for a, b in zip(fake.starts, fake.starts[1:]))


def test_a_tick_that_ends_exactly_on_the_next_grid_point_skips_it_and_starts_on_the_one_after(sched):
    ticker, fake = run_ticker(sched, 10, 10.0, stop_after=3)
    first = fake.starts[0]
    assert [round(s - first, 6) for s in fake.starts] == [0.0, 20.0, 40.0]
    assert ticker.skipped == 3  # one grid point skipped after each of the three ticks


def test_a_tick_that_returns_false_is_not_a_start(sched):
    """A follower's tick does nothing: it is not counted as a start (the cadence is the leader's), and the schedule goes on."""
    answers = [False, False, None, None]
    t = {"now": 0.0, "n": 0}
    stopped = {"v": False}

    def work():
        n = t["n"]
        t["n"] += 1
        if t["n"] >= len(answers):
            stopped["v"] = True
        return answers[n]

    def wait(d):
        t["now"] += d
        return stopped["v"]

    ticker = sched.Ticker("sweep", 10, work, now=lambda: t["now"], wait=wait)
    ticker.run()
    assert ticker.ticks == 4 and ticker.started_ticks == 2 and len(ticker.starts) == 2


def test_a_task_that_raises_keeps_its_schedule(sched):
    t = {"now": 0.0, "n": 0}

    def work():
        t["n"] += 1
        if t["n"] == 2:
            raise RuntimeError("boom")

    def wait(d):
        t["now"] += d
        return t["n"] >= 4

    ticker = sched.Ticker("scan", 10, work, now=lambda: t["now"], wait=wait)
    ticker.run()
    assert ticker.errors == 1 and ticker.started_ticks == 4


def test_the_tasks_have_their_own_intervals_and_do_not_wait_for_each_other(sched, monkeypatch):
    """A slow automation scan does not delay the sweep: they are separate threads on separate schedules (real clock, short intervals)."""
    stop = threading.Event()
    sweep_starts, scan_starts = [], []
    release = threading.Event()

    def sweep():
        sweep_starts.append(time.monotonic())

    def scan():
        scan_starts.append(time.monotonic())
        release.wait(5)  # the scan is stuck for far longer than the sweep's interval

    a = sched.Ticker("sweep", 0.05, sweep, stop=stop)
    b = sched.Ticker("automation-scan", 0.05, scan, stop=stop)
    a.start()
    b.start()
    deadline = time.monotonic() + 30
    while len(sweep_starts) < 5 and time.monotonic() < deadline:
        time.sleep(0.01)
    release.set()
    stop.set()
    a.join(5)
    b.join(5)
    assert len(sweep_starts) >= 5 and len(scan_starts) == 1  # the stuck scan ran once and did not hold the sweep back


def test_only_the_leader_works_and_every_call_carries_the_fencing_token(sched, monkeypatch):
    """The tasks are gated on leadership; a follower's tick is a no-op, and a leader's call sends the lease headers."""
    calls = []
    monkeypatch.setattr(sched, "run_sweep", lambda: calls.append("sweep") or {
        "sim_time": "2026-01-05T08:00:00+00:00", "cases_evaluated": 0, "mark_overdue": 0, "escalate": 0, "rejected": 0, "errors": []})
    monkeypatch.setattr(sched, "run_automation", lambda path: calls.append(path))
    monkeypatch.setattr(sched, "run_agency", lambda: calls.append("agency"))
    monkeypatch.setattr(sched, "drain_outbox", lambda: None)
    monkeypatch.setattr(sched, "post_heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(sched, "write_heartbeat", lambda *a, **k: None)
    state = sched.LoopState()
    assert sched.sweep_tick(state) is False and sched.scan_tick(state, "automation-scan") is False \
        and sched.agency_tick(state) is False and calls == []
    state.was_leader = True
    sched.sweep_tick(state)
    sched.scan_tick(state, "evidence-scan")
    sched.agency_tick(state)
    assert calls == ["sweep", "evidence-scan", "agency"]
    monkeypatch.setitem(sched.FENCE, "X-Leader-Holder", "h")
    monkeypatch.setitem(sched.FENCE, "X-Leader-Token", "7")
    assert sched._headers()["X-Leader-Token"] == "7" and sched._headers()["X-Leader-Holder"] == "h"


def test_build_tickers_follows_the_configured_intervals(sched, monkeypatch):
    monkeypatch.setattr(sched, "INTERVAL_SECONDS", 10)
    monkeypatch.setattr(sched, "AUTOMATION_INTERVAL_SECONDS", 20.0)
    monkeypatch.setattr(sched, "EVIDENCE_INTERVAL_SECONDS", 30.0)
    monkeypatch.setattr(sched, "AGENCY_INTERVAL_SECONDS", 0.0)
    ticks = {t.task: t.interval for t in sched.build_tickers(sched.LoopState())}
    assert ticks == {"sweep": 10.0, "automation-scan": 20.0, "evidence-scan": 30.0}  # agency 0 = no agency task
    monkeypatch.setattr(sched, "AGENCY_INTERVAL_SECONDS", 60.0)
    assert {t.task: t.interval for t in sched.build_tickers(sched.LoopState())}["agency"] == 60.0
