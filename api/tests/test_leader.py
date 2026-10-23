"""A31 (ADR-041): leader election, unit tests on the in-memory lease. The Postgres lease is test_leader_pg.py."""

import importlib.util
import json
import threading
import time
from pathlib import Path

import pytest

from api_helpers import make_client
from app import leader, ops
from app.config import settings
from app.events import InMemoryEventStore
from app.leader import InMemoryLeaseStore, LeaderLoop
from app.routers import cases


class Tick:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


@pytest.fixture
def tick():
    return Tick()


@pytest.fixture
def lease_store(tick, monkeypatch):
    s = InMemoryLeaseStore(now=tick)
    monkeypatch.setattr(leader, "store", s)
    return s


def test_two_contenders_exactly_one_leader(lease_store):
    a = lease_store.acquire("scheduler", "a", 30)
    b = lease_store.acquire("scheduler", "b", 30)
    assert a is not None and a.token == 1 and b is None
    assert lease_store.current("scheduler").holder == "a"


def test_contention_across_threads_has_one_winner(lease_store):
    wins = []
    barrier = threading.Barrier(16)

    def go(i):
        barrier.wait()
        if lease_store.acquire("scheduler", f"h{i}", 30):
            wins.append(i)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(16)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(wins) == 1


def test_renewal_keeps_the_token_and_extends_the_expiry(lease_store, tick):
    first = lease_store.acquire("scheduler", "a", 30)
    tick.t += 20
    again = lease_store.acquire("scheduler", "a", 30)
    assert again.token == first.token == 1 and again.expires_in == pytest.approx(30)
    tick.t += 20  # 40 s after the first, but only 20 after the renewal: still held
    assert lease_store.acquire("scheduler", "b", 30) is None


def test_takeover_after_expiry_bumps_the_token_and_a_stale_token_is_rejected(lease_store, tick):
    a = lease_store.acquire("scheduler", "a", 30)
    assert lease_store.is_current("scheduler", "a", a.token)
    tick.t += 31  # the leader crashed: no renewal
    b = lease_store.acquire("scheduler", "b", 30)
    assert b is not None and b.token == a.token + 1
    assert not lease_store.is_current("scheduler", "a", a.token)  # the deposed leader's token is dead
    assert lease_store.is_current("scheduler", "b", b.token)
    assert lease_store.acquire("scheduler", "a", 30) is None  # and it cannot just take it back while b is alive
    assert not lease_store.is_current("scheduler", "b", a.token)  # a token is bound to its holder


def test_an_expired_lease_is_not_current_even_for_its_holder(lease_store, tick):
    a = lease_store.acquire("scheduler", "a", 30)
    tick.t += 31
    assert not lease_store.is_current("scheduler", "a", a.token)
    # if nobody took over, the old holder renews and keeps its token
    assert lease_store.acquire("scheduler", "a", 30).token == a.token


def test_release_lets_a_follower_in_at_once(lease_store):
    a = lease_store.acquire("scheduler", "a", 30)
    assert lease_store.release("scheduler", "b") is False
    assert lease_store.release("scheduler", "a") is True
    b = lease_store.acquire("scheduler", "b", 30)
    assert b.token == a.token + 1


def test_leases_are_independent_by_name(lease_store):
    assert lease_store.acquire("scheduler", "a", 30) and lease_store.acquire("api-background", "b", 30)


def test_leader_loop_follows_the_lease(lease_store, tick):
    one, two = LeaderLoop("api-background", "one", 30), LeaderLoop("api-background", "two", 30)
    assert one.tick() and not two.tick()
    assert one.is_leader and not two.is_leader and one.token == 1
    one.stop()  # releases
    assert two.tick() and two.token == 2


def test_api_is_leader_defaults_to_true_without_a_loop(monkeypatch):
    monkeypatch.setattr(leader, "api_loop", None)
    assert leader.api_is_leader()
    monkeypatch.setattr(leader, "api_loop", LeaderLoop("api-background", "x", 30, lease_store=InMemoryLeaseStore()))
    assert not leader.api_is_leader()  # a loop that has not (yet) won is a follower


# ---- the routes ---------------------------------------------------------------------------------------------

client = make_client()
SCHED = {"Authorization": f"Bearer {settings.scheduler_service_token}"}


@pytest.fixture
def api(lease_store, monkeypatch):
    monkeypatch.setattr(ops, "store", ops.InMemoryOpsStore())
    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    return client


def lease(holder):
    r = client.post("/scheduler/lease", json={"holder": holder}, headers=SCHED)
    assert r.status_code == 200, r.text
    return r.json()


def fence(holder, token):
    return {**SCHED, "X-Leader-Holder": holder, "X-Leader-Token": str(token)}


def test_lease_route_elects_one_and_names_the_other_as_follower(api):
    a, b = lease("sched-a"), lease("sched-b")
    assert a["leader"] is True and a["token"] == 1
    assert b["leader"] is False and b["token"] is None and b["leader_holder"] == "sched-a"


def test_a_stale_leaders_calls_are_refused_with_409_and_do_nothing(api, lease_store, tick):
    a = lease("sched-a")
    assert client.post("/scheduler/sweep", headers=fence("sched-a", a["token"])).status_code == 200
    tick.t += 31
    b = lease("sched-b")
    assert b["leader"] and b["token"] == 2
    for path in ("sweep", "outbox/drain", "automation-scan", "evidence-scan"):
        stale = client.post(f"/scheduler/{path}", headers=fence("sched-a", a["token"]))
        assert stale.status_code == 409 and "stale leader" in stale.json()["detail"], path
        assert client.post(f"/scheduler/{path}", headers=fence("sched-b", b["token"])).status_code == 200, path
    # a forged token for the right holder, and half a header pair, are refused too
    assert client.post("/scheduler/sweep", headers=fence("sched-b", 1)).status_code == 409
    assert client.post("/scheduler/sweep", headers={**SCHED, "X-Leader-Holder": "sched-b"}).status_code == 409


def test_calls_without_headers_are_allowed_unless_fencing_is_required(api, monkeypatch):
    assert client.post("/scheduler/sweep", headers=SCHED).status_code == 200  # a manual run
    monkeypatch.setattr(settings, "require_leader_fence", True)
    r = client.post("/scheduler/sweep", headers=SCHED)
    assert r.status_code == 409 and "fencing is required" in r.json()["detail"]
    a = lease("sched-a")
    assert client.post("/scheduler/sweep", headers=fence("sched-a", a["token"])).status_code == 200


def test_followers_report_follower_on_heartbeats_and_cannot_beat_as_the_scheduler(api, lease_store, tick):
    a, _ = lease("sched-a"), lease("sched-b")
    body = {"sweep_ok": True, "consecutive_failures": 0, "interval_seconds": 10}
    ok = client.post("/scheduler/heartbeat", json={**body, "role": "leader", "holder": "sched-a", "fencing_token": a["token"]}, headers=SCHED)
    assert ok.status_code == 200 and ok.json()["role"] == "leader"
    f = client.post("/scheduler/heartbeat", json={**body, "role": "follower", "holder": "sched-b"}, headers=SCHED)
    assert f.status_code == 200 and f.json()["role"] == "follower"
    # a follower that claims to lead is refused
    assert client.post("/scheduler/heartbeat", json={**body, "role": "leader", "holder": "sched-b", "fencing_token": 1},
                       headers=SCHED).status_code == 409
    from api_helpers import login_headers

    beats = client.get("/heartbeats", headers=login_headers(client, "coordinator", "lease coord")).json()
    assert beats["scheduler"]["role"] == "leader" and beats["scheduler"]["holder"] == "sched-a"
    assert [x["role"] for x in beats["followers"]] == ["follower"] and beats["followers"][0]["holder"] == "sched-b"
    assert beats["leader"]["holder"] == "sched-a" and beats["leader"]["token"] == 1


def test_release_route_hands_over(api):
    a = lease("sched-a")
    assert client.post("/scheduler/lease/release", json={"holder": "sched-b"}, headers=SCHED).json() == {"released": False}
    assert client.post("/scheduler/lease/release", json={"holder": "sched-a"}, headers=SCHED).json() == {"released": True}
    assert lease("sched-b")["token"] == a["token"] + 1


def test_the_lease_routes_are_scheduler_only(api):
    from api_helpers import login_headers

    for role in ("supervisor", "coordinator"):
        h = login_headers(client, role, "x")
        assert client.post("/scheduler/lease", json={"holder": "h"}, headers=h).status_code == 403
    assert client.post("/scheduler/lease", json={"holder": "h"}).status_code == 401


# ---- the scheduler service -----------------------------------------------------------------------------------

MAIN = Path(__file__).resolve().parents[2] / "scheduler" / "main.py"


def load_scheduler(holder):
    spec = importlib.util.spec_from_file_location(f"scheduler_{holder}", MAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.HOLDER = holder
    return mod


def test_two_scheduler_instances_contend_and_only_the_leader_sweeps(api, monkeypatch):
    """The real loop_once of two instances against the real routes (urlopen replaced by the TestClient)."""
    import urllib.error
    import urllib.request

    scheds = [load_scheduler("sched-a"), load_scheduler("sched-b")]
    prefix = "http://testserver"

    class Resp:
        def __init__(self, content): self.content = content
        def read(self): return self.content
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(request, timeout=0):
        resp = client.post(request.full_url.removeprefix(prefix), content=request.data or b"",
                           headers={k: v for k, v in request.header_items()})
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(request.full_url, resp.status_code, resp.text, {}, None)
        return Resp(resp.content)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    for mod in scheds:
        monkeypatch.setattr(mod, "write_heartbeat", lambda *a, **k: None)
        mod.API_URL = prefix
    states = [m.LoopState() for m in scheds]
    swept = []
    for _ in range(3):
        for mod, st in zip(scheds, states):
            mod.FENCE.clear()  # each module copy has its own FENCE; loop_once sets it from its own lease answer
            if mod.loop_once(st) is not None:
                swept.append(mod.HOLDER)
    assert swept == ["sched-a"] * 3  # a was first; b idled all three loops
    assert states[0].was_leader and not states[1].was_leader


def test_a_deposed_scheduler_keeps_its_old_token_so_late_calls_are_refused_not_unfenced(api, lease_store, tick, monkeypatch):
    import urllib.request

    mod = load_scheduler("sched-a")
    answers = iter([{"leader": True, "token": 1, "ttl_seconds": 30}, {"leader": False, "leader_holder": "sched-b"}])

    class Resp:
        def __init__(self, body): self.body = body
        def read(self): return json.dumps(self.body).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: Resp(next(answers)))
    assert mod.acquire_lease()["leader"] and mod.FENCE["X-Leader-Token"] == "1"
    assert not mod.acquire_lease()["leader"]
    assert mod.FENCE == {"X-Leader-Holder": "sched-a", "X-Leader-Token": "1"}  # still the old token
    assert mod._headers()["X-Leader-Token"] == "1"
