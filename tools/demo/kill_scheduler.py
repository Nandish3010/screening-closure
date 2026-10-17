#!/usr/bin/env python3
"""F11 rehearsal: kill the scheduler on stage and show that nothing is lost.

    python3 tools/demo/kill_scheduler.py            one rehearsal
    python3 tools/demo/kill_scheduler.py --runs 3   three in a row (the F11 bar: it must work three times)
    make demo-kill-scheduler [RUNS=3]

Each run: record the event log (count and a hash of every event id in order), stop the `scheduler` container,
wait for the watchdog to raise the `scheduler_down` alert (the dashboard banner reads the same alert; the
limit is 90 s), check the API still answers while the scheduler is dead, start the scheduler again, wait for the
alert to clear, and compare the event log with the one recorded: the same count and the same ids in the same
order (so nothing was lost, nothing duplicated, nothing rewritten). The rehearsal writes no events itself.

Needs the compose stack up (`make up`) and docker on the host. Exit status 0 only if every run passed.
Environment: API_URL (default http://localhost:8000), WATCHDOG_SERVICE_TOKEN (the development default).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
WATCHDOG_TOKEN = os.environ.get("WATCHDOG_SERVICE_TOKEN", "dev-only-watchdog-service-token")
DETECT_WITHIN = 90
CLEAR_WITHIN = 60
POSTGRES_USER = os.environ.get("POSTGRES_USER", "closure")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "closure")


def compose(*args: str, timeout: int = 120) -> str:
    out = subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True, capture_output=True, text=True, timeout=timeout)
    return out.stdout


def get(path: str, token: str = WATCHDOG_TOKEN):
    req = urllib.request.Request(f"{API}{path}", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def event_log() -> tuple[int, str]:
    """(event count, md5 over every event id in insertion order) straight from Postgres."""
    out = compose(
        "exec", "-T", "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-U", POSTGRES_USER, "-d", POSTGRES_DB, "-At", "-F", "|", "-c",
        "SELECT count(*), coalesce(md5(string_agg(event_id::text, ',' ORDER BY id)), '-') FROM events",
    ).strip()
    count, digest = out.split("|")
    return int(count), digest


def prefix_hash(n: int) -> str:
    """The same hash over only the first n events (to prove the old log is intact when new events exist)."""
    out = compose(
        "exec", "-T", "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-U", POSTGRES_USER, "-d", POSTGRES_DB, "-At", "-c",
        f"SELECT coalesce(md5(string_agg(event_id::text, ',' ORDER BY id)), '-') FROM (SELECT id, event_id FROM events ORDER BY id LIMIT {n}) t",
    ).strip()
    return out


def scheduler_alert():
    return next((a for a in get("/alerts") if a["kind"] == "scheduler_down" and a["cleared_at"] is None), None)


def wait_for(predicate, seconds: float, label: str):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(1)
    raise TimeoutError(f"{label} did not happen within {seconds:.0f} s")


def beat_age() -> float:
    beat = get("/heartbeats")["scheduler"]
    return beat["age_seconds"] if beat else 999.0


def one_run(n: int) -> dict:
    print(f"--- run {n}")
    compose("start", "scheduler")
    wait_for(lambda: beat_age() < 20 and not scheduler_alert(), 90, "a healthy start (scheduler beating, no open alert)")
    count_before, hash_before = event_log()
    print(f"before: {count_before} events, id hash {hash_before[:12]}")

    stopped = time.monotonic()
    compose("stop", "scheduler")
    print("scheduler stopped; waiting for the watchdog's alert ...")
    try:
        alert = wait_for(scheduler_alert, DETECT_WITHIN, "the scheduler_down alert")
        detect = time.monotonic() - stopped
        print(f"ALERT scheduler_down after {detect:.1f} s (since {alert['since']})")
        # The API keeps working with the scheduler dead: reads answer, only SLA sweeps stop.
        cases = get("/cases", token=_coordinator_token())
        print(f"API still answers while the scheduler is down ({len(cases)} cases listed)")
    finally:
        compose("start", "scheduler")  # whatever happened, leave the scheduler running
    restarted = time.monotonic()
    print("scheduler started; waiting for the alert to clear ...")
    wait_for(lambda: not scheduler_alert() and beat_age() < 20, CLEAR_WITHIN, "the alert to clear")
    clear = time.monotonic() - restarted
    print(f"ALERT cleared {clear:.1f} s after the restart")

    count_after, hash_after = event_log()
    lost = count_after < count_before
    intact = prefix_hash(count_before) == hash_before
    new = count_after - count_before
    print(f"after:  {count_after} events ({new:+d}), the first {count_before} ids {'unchanged' if intact else 'CHANGED'}")
    ok = detect <= DETECT_WITHIN and not lost and intact
    print("PASS" if ok else "FAIL")
    return {"run": n, "detect_s": round(detect, 1), "clear_s": round(clear, 1), "events_before": count_before,
            "events_after": count_after, "intact": intact, "ok": ok}


_TOKEN: list[str] = []


def _coordinator_token() -> str:
    if not _TOKEN:
        req = urllib.request.Request(
            f"{API}/auth/dev-login", data=json.dumps({"role": "coordinator", "name": "Maryam Al Suwaidi"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            _TOKEN.append(json.load(resp)["token"])
    return _TOKEN[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=int(os.environ.get("RUNS", "1")))
    args = ap.parse_args(argv)
    try:
        get("/alerts")
    except Exception as exc:
        print(f"kill-scheduler: the stack is not reachable at {API} ({exc}); start it with `make up`", file=sys.stderr)
        return 2
    results = []
    for n in range(1, args.runs + 1):
        try:
            results.append(one_run(n))
        except (TimeoutError, subprocess.CalledProcessError, OSError) as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            compose("start", "scheduler")
            results.append({"run": n, "ok": False, "error": str(exc)})
            break
    print(json.dumps(results, indent=2))
    return 0 if len(results) == args.runs and all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
