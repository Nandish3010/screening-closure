"""T-A31 and the logic half of T-A32 (A11): the scheduler's heartbeat, its age as the API reports it,
and the alert the watchdog raises when it stops (and clears when it resumes). The same logic is
run against the real compose stack by test_live_stack.py and test_chaos.py."""

import pytest

from api_helpers import login_headers, make_client
from app import ops
from app.config import settings as app_settings
from app.events import InMemoryEventStore
from app.ops import Heartbeat, InMemoryOpsStore, scheduler_status
from app.routers import cases
from datetime import UTC, datetime

client = make_client()
WD = {"Authorization": f"Bearer {app_settings.watchdog_service_token}"}
SCHED = {"Authorization": f"Bearer {app_settings.scheduler_service_token}"}


class Wall:
    """A fake wall clock for the in-memory ops store."""

    def __init__(self, t=1_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def wall(monkeypatch):
    w = Wall()
    monkeypatch.setattr(ops, "store", InMemoryOpsStore(wall=w))
    monkeypatch.setattr(cases, "store", InMemoryEventStore())
    return w


def H(role):
    return login_headers(client, role, f"hb {role}")


def beat(**kw):
    body = {"sweep_ok": True, "consecutive_failures": 0, "interval_seconds": 10, **kw}
    r = client.post("/scheduler/heartbeat", json=body, headers=SCHED)
    assert r.status_code == 200, r.text


def scan(uptime=300):
    r = client.post("/watchdog/scan", json={"uptime_seconds": uptime}, headers=WD)
    assert r.status_code == 200, r.text
    return r.json()


def alerts(active=False):
    r = client.get("/alerts", params={"active": "true"} if active else {}, headers=H("coordinator"))
    assert r.status_code == 200, r.text
    return r.json()


# ---- T-A31: the heartbeat and its age ------------------------------------------------------------------------


def test_t_a31_the_api_exposes_the_age_of_the_latest_heartbeat(wall):
    assert client.get("/heartbeats", headers=H("coordinator")).json() == {"scheduler": None, "watchdog": None}
    beat(sim_time="2026-01-05T08:00:00+00:00")
    wall.t += 7
    hb = client.get("/heartbeats", headers=H("supervisor")).json()["scheduler"]
    assert hb["service"] == "scheduler" and hb["age_seconds"] == 7 and hb["sweep_ok"] is True
    assert hb["interval_seconds"] == 10 and hb["consecutive_failures"] == 0
    wall.t += 30
    assert client.get("/heartbeats", headers=H("coordinator")).json()["scheduler"]["age_seconds"] == 37
    beat()  # the next loop resets the age
    assert client.get("/heartbeats", headers=H("coordinator")).json()["scheduler"]["age_seconds"] == 0


def test_t_a31_the_age_stays_below_twice_the_interval_while_the_scheduler_beats_every_interval(wall):
    ages = []
    for _ in range(30):
        beat()
        wall.t += 9.9  # the scheduler loops every 10 s, a little less than the interval
        ages.append(client.get("/heartbeats", headers=WD).json()["scheduler"]["age_seconds"])
    assert max(ages) < 2 * 10


def test_t_a31_a_failed_sweep_still_beats_and_says_so(wall):
    beat(sweep_ok=False, consecutive_failures=3)
    hb = client.get("/heartbeats", headers=H("coordinator")).json()["scheduler"]
    assert hb["sweep_ok"] is False and hb["consecutive_failures"] == 3


def test_t_a31_only_the_scheduler_posts_heartbeats_and_only_known_fields(wall):
    body = {"sweep_ok": True}
    for headers in (WD, H("supervisor"), H("coordinator")):
        assert client.post("/scheduler/heartbeat", json=body, headers=headers).status_code == 403
    assert client.post("/scheduler/heartbeat", json=body).status_code == 401
    assert client.post("/scheduler/heartbeat", json={**body, "note": "x"}, headers=SCHED).status_code == 422
    assert client.post("/scheduler/heartbeat", json={}, headers=SCHED).status_code == 422
    assert client.get("/heartbeats", headers=H("coordinator")).json()["scheduler"] is None


def test_t_a31_heartbeats_and_alerts_are_for_coordinators_supervisors_and_the_watchdog(wall):
    for path in ("/heartbeats", "/alerts"):
        for role in ("coordinator", "supervisor"):
            assert client.get(path, headers=H(role)).status_code == 200
        assert client.get(path, headers=WD).status_code == 200
        for role in ("lab", "primary_care_owner", "specialist_owner", "patient"):
            assert client.get(path, headers=H(role)).status_code == 403
        assert client.get(path, headers=SCHED).status_code == 403
        assert client.get(path).status_code == 401


# ---- T-A32 (logic): down after 60 s, cleared on resume ------------------------------------------------------------


def hb(age, **info):
    return Heartbeat("scheduler", datetime(2026, 1, 1, tzinfo=UTC), age, {"sweep_ok": True, **info})


@pytest.mark.parametrize("beat_, uptime, status", [
    (None, 10, "warming"),
    (None, 59.9, "warming"),
    (None, 60, "down"),
    (hb(0), 0, "ok"),
    (hb(60), 999, "ok"),  # exactly 60 s old is not older than 60 s
    (hb(60.1), 999, "down"),
    (hb(61), 5, "down"),  # a stale heartbeat is down even during warm-up
    (hb(5, sweep_ok=False, consecutive_failures=5, interval_seconds=10), 999, "ok"),
    (hb(5, sweep_ok=False, consecutive_failures=6, interval_seconds=10), 999, "failing"),
    (hb(5, sweep_ok=True, consecutive_failures=6, interval_seconds=10), 999, "ok"),
])
def test_t_a32_scheduler_status_table(beat_, uptime, status):
    assert scheduler_status(beat_, uptime)[0] == status


def test_t_a32_a_silent_scheduler_raises_an_alert_within_a_scan_and_a_resumed_one_clears_it(wall):
    beat()
    assert scan()["scheduler"]["status"] == "ok" and alerts() == []
    wall.t += 59
    assert scan()["scheduler"]["status"] == "ok"
    wall.t += 2  # 61 s since the last heartbeat
    body = scan()
    assert body["scheduler"]["status"] == "down" and body["scheduler"]["reason"] == "stale"
    (alert,) = alerts(active=True)
    assert alert["kind"] == "scheduler_down" and alert["cleared_at"] is None and alert["since"]
    assert body["alerts_open"][0]["kind"] == "scheduler_down"
    scan()  # still down: no second alert
    assert len(alerts()) == 1
    beat()  # the scheduler comes back
    assert scan()["scheduler"]["status"] == "ok"
    assert alerts(active=True) == []
    (cleared,) = alerts()
    assert cleared["kind"] == "scheduler_down" and cleared["cleared_at"] is not None
    assert cleared["id"] == alert["id"] and cleared["since"] == alert["since"]
    wall.t += 120  # a second outage is a second episode
    scan()
    assert [a["cleared_at"] is None for a in alerts()] == [True, False]


def test_t_a32_a_missing_heartbeat_is_forgiven_during_warm_up_and_an_alert_afterwards(wall):
    assert scan(uptime=10)["scheduler"]["status"] == "warming" and alerts() == []
    assert scan(uptime=70)["scheduler"]["status"] == "down"
    assert alerts(active=True)[0]["kind"] == "scheduler_down"
    beat()
    scan(uptime=80)
    assert alerts(active=True) == []


def test_t_a32_a_scheduler_that_is_alive_but_failing_is_its_own_alert(wall):
    beat(sweep_ok=False, consecutive_failures=6)
    body = scan()
    assert body["scheduler"]["status"] == "failing"
    assert [a["kind"] for a in alerts(active=True)] == ["scheduler_failing"]
    beat(sweep_ok=True, consecutive_failures=0)
    scan()
    assert alerts(active=True) == []


def test_t_a32_a_silent_watchdog_shows_up_as_a_derived_alert(wall):
    beat()
    scan()
    assert alerts() == []
    wall.t += 61
    beat()  # the scheduler is fine; only the watchdog went quiet
    (derived,) = alerts()
    assert derived["kind"] == "watchdog_down" and derived["derived"] is True and derived["cleared_at"] is None
    hbs = client.get("/heartbeats", headers=H("coordinator")).json()
    assert hbs["watchdog"]["age_seconds"] == 61 and hbs["watchdog"]["uptime_seconds"] == 300
    scan()
    assert alerts() == []
