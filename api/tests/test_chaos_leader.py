"""T-A31 (chaos): kill the scheduler that holds the lease while a sweep is due; the other takes over and nothing is lost or doubled.

Runs only with `make test-chaos`, against a compose stack started with `make up-scale` (two API replicas behind the proxy and two
schedulers, `scheduler-1` and `scheduler-2`). It kills and restarts a real scheduler container, so point it at your OWN compose
project, never the main stack:

    COMPOSE_PROJECT_NAME=sc-m6-ops POSTGRES_PORT=55433 API_PORT=58000 PROXY_PORT=58100 FHIR_PORT=58080 \\
    LEADER_LEASE_TTL_SECONDS=10 make up-scale
    COMPOSE_PROJECT_NAME=sc-m6-ops TEST_API_URL=http://localhost:58100 LEADER_LEASE_TTL_SECONDS=10 make test-chaos

It creates its own cases (random patient refs) and moves the dev simulation clock forward (never back). It skips itself when
the scale stack is not reachable at TEST_API_URL.
"""

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from pathlib import Path

import pytest

from app.config import settings as app_settings

API = os.environ.get("TEST_API_URL", "http://localhost:8000").rstrip("/")
ROOT = Path(__file__).resolve().parents[2]
TTL = float(os.environ.get("LEADER_LEASE_TTL_SECONDS", "30"))
POLL = 2.0
CASES = 12

pytestmark = pytest.mark.chaos


def call(method, path, token=None, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{API}{path}", data=data, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def login(role, name):
    return call("POST", "/auth/dev-login", body={"role": role, "name": name})["token"]


def compose(*args):
    return subprocess.run(["docker", "compose", "--profile", "scale", *args], cwd=ROOT, check=True, capture_output=True,
                          text=True, timeout=180)


def beats(token):
    return call("GET", "/heartbeats", token)


def wait_for(predicate, seconds, every=0.5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(every)
    return None


@pytest.fixture(scope="module")
def stack():
    wd = app_settings.watchdog_service_token
    try:
        b = beats(wd)
    except Exception as exc:
        pytest.skip(f"scale stack not reachable at {API}: {exc}")
    if "followers" not in b:
        pytest.skip("the API at TEST_API_URL predates A31")
    ok = wait_for(lambda: (lambda x: x["leader"] and x["followers"])(beats(wd)), 90)
    if not ok:
        pytest.skip("need a leader and a follower scheduler (make up-scale)")
    yield wd
    for name in ("scheduler", "scheduler-2"):
        compose("start", name)  # whatever happened, leave both schedulers running


def holder_service(holder):
    assert holder in ("scheduler-1", "scheduler-2"), holder
    return "scheduler" if holder == "scheduler-1" else "scheduler-2"


def test_t_a31_killing_the_leader_loses_and_duplicates_no_event(stack):
    wd = stack
    coord, sup = login("coordinator", "chaos coord"), login("supervisor", "chaos sup")
    before = beats(wd)["leader"]
    old_holder, old_token = before["holder"], before["token"]

    ids = [call("POST", "/cases", coord, {"patient_ref": f"Patient/chaos-{uuid.uuid4().hex[:8]}"})["case_id"] for _ in range(CASES)]
    # past every first SLA on the working-day calendar: the next sweep has real work for all of them
    call("POST", "/clock/advance", sup, {"days": 6})
    time.sleep(2)  # the other API replica re-reads the shared clock row within a second

    def scheduler_events():
        out = {}
        for cid in ids:
            case = call("GET", f"/cases/{cid}", sup)
            out[cid] = [e for e in case["events"] if e["actor"] == "system:scheduler"]
        return out

    # kill the leader as soon as its sweep has started to write (or after a few seconds, whichever comes first)
    wait_for(lambda: any(scheduler_events().values()), 12, every=0.2)
    killed_at = time.monotonic()
    compose("kill", holder_service(old_holder))

    def took_over():
        cur = beats(wd)["leader"]
        return cur if cur and cur["holder"] != old_holder else None

    new = wait_for(took_over, TTL + POLL + 15)
    handover = time.monotonic() - killed_at
    print(f"\nchaos: leader {old_holder} (token {old_token}) killed; {new and new['holder']} led after {handover:.1f} s "
          f"(ttl {TTL} s, follower poll {POLL} s), token {new and new['token']}")
    assert new is not None, "no follower took over the lease"
    assert new["token"] == old_token + 1
    assert handover <= TTL + POLL + 5, f"handover took {handover:.1f} s"

    # the new leader finishes the work: wait until every case has its scheduler events and the set stops growing
    def settled():
        a = scheduler_events()
        if not all(a.values()):
            return None
        time.sleep(TTL / 2 + POLL)
        b = scheduler_events()
        return b if {k: len(v) for k, v in a.items()} == {k: len(v) for k, v in b.items()} else None

    final = wait_for(settled, 120, every=1)
    assert final is not None, "the cases did not all get their scheduler events (lost work), or kept growing (a loop)"

    for cid in ids:
        case = call("GET", f"/cases/{cid}", sup)
        events = case["events"]
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1)), f"{cid}: gap in the log"
        assert len({e["event_id"] for e in events}) == len(events), f"{cid}: duplicate event id"
        dup = [k for k, n in Counter((e["type"], json.dumps(e["payload"], sort_keys=True)) for e in final[cid]).items() if n > 1]
        assert not dup, f"{cid}: the same scheduler event twice: {dup}"

    # nothing is left undone: a sweep run as the current leader (its identity and token) plans no new mark or escalation
    cur = beats(wd)["leader"]
    verify = call("POST", "/scheduler/sweep", app_settings.scheduler_service_token,
                  headers={"X-Leader-Holder": cur["holder"], "X-Leader-Token": str(cur["token"])})
    assert verify["mark_overdue"] == 0 and verify["escalate"] == 0, verify

    # the killed scheduler comes back as a follower, and the deposed token is useless
    compose("start", holder_service(old_holder))
    back = wait_for(lambda: next((f for f in beats(wd)["followers"] if f.get("holder") == old_holder), None), 60)
    assert back is not None and back["role"] == "follower"
    with pytest.raises(urllib.error.HTTPError) as stale:
        call("POST", "/scheduler/sweep", app_settings.scheduler_service_token,
             headers={"X-Leader-Holder": old_holder, "X-Leader-Token": str(old_token)})
    assert stale.value.code == 409
