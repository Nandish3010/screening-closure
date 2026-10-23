"""Watchdog-facing routes (A11): the scan, the watchdog's heartbeat, and the read side.

* `POST /watchdog/scan`  (watchdog service token only) one pass: records the watchdog's own
  heartbeat, flags idle cases on the sim clock, and decides from the scheduler's heartbeat whether
  the scheduler is down, opening or clearing a `system_alerts` row.
* `POST /scheduler/heartbeat` (scheduler service token only) is in `routers/scheduler.py`.
* `GET /heartbeats`, `GET /alerts` (coordinator, supervisor; the watchdog may read them too).
"""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app import fhir_projection, leader, ops
from app.clock import clock
from app.config import settings
from app.routers import cases
from app.routers.scheduler import FOLLOWER_PREFIX
from app.watchdog import run_idle_scan

router = APIRouter(tags=["watchdog"])
FOLLOWER_MAX_AGE_SECONDS = 120  # a follower that has not beaten for this long is gone, not idle


class Scan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uptime_seconds: float = Field(default=0, ge=0)  # how long the watchdog process has been up (warm-up)
    idle_days: int | None = Field(default=None, ge=1, le=3650)  # the watchdog's WATCHDOG_IDLE_DAYS


def _apply_scheduler_verdict(status: str, detail: dict) -> None:
    """Open or clear the scheduler alerts so the table says what the verdict says."""
    if status == "down":
        ops.store.open_alert(ops.SCHEDULER_DOWN, detail)
        ops.store.clear_alert(ops.SCHEDULER_FAILING)
    elif status == "failing":
        ops.store.open_alert(ops.SCHEDULER_FAILING, detail)
        ops.store.clear_alert(ops.SCHEDULER_DOWN)
    elif status == "ok":
        ops.store.clear_alert(ops.SCHEDULER_DOWN)
        ops.store.clear_alert(ops.SCHEDULER_FAILING)
    # "warming": no verdict yet, leave the alerts as they are


@router.post("/watchdog/scan")
def scan(body: Scan | None = None) -> dict:
    body = body or Scan()
    ops.store.beat("watchdog", {"uptime_seconds": round(body.uptime_seconds, 1)})
    idle_days = body.idle_days or settings.watchdog_idle_days
    idle = run_idle_scan(cases.store, cases.pathway, clock.now(), idle_days)
    status, detail = ops.scheduler_status(ops.store.heartbeats().get("scheduler"), body.uptime_seconds)
    _apply_scheduler_verdict(status, detail)
    return {
        "scan": idle.to_json(),
        "scheduler": {"status": status, **detail},
        "alerts_open": [a.to_json() for a in ops.store.alerts(active_only=True)],
    }


@router.get("/heartbeats")
def heartbeats() -> dict:
    """The age of the latest heartbeat of each service (None until it has beaten once)."""
    beats = ops.store.heartbeats()
    out = {name: (beats[name].to_json() if name in beats else None) for name in ("scheduler", "watchdog")}
    # A31: scheduler instances that are not leading beat as `scheduler-follower-<holder>` and report role "follower".
    cur = leader.store.current(leader.SCHEDULER_LEASE)
    out["followers"] = sorted(
        (b.to_json() for n, b in beats.items()
         if n.startswith(FOLLOWER_PREFIX) and b.age_seconds <= FOLLOWER_MAX_AGE_SECONDS
         and not (cur and b.info.get("holder") == cur.holder)),  # a follower that has since won is the leader now
        key=lambda b: b["service"],
    )
    out["leader"] = cur.to_json() if cur else None
    return out


@router.get("/alerts")
def alerts(active: bool = False) -> list[dict]:
    """System alerts, newest first: `{id, kind, since, cleared_at, detail}`. `cleared_at` is null
    while the alert is open. `watchdog_down` is derived here from the watchdog's own heartbeat (no
    process is left to open it); the others are opened and cleared by the watchdog scan."""
    rows = [a.to_json() for a in ops.store.alerts(active_only=active)]
    wd = ops.store.heartbeats().get("watchdog")
    if wd is not None and wd.age_seconds > ops.HEARTBEAT_MAX_AGE_SECONDS:
        rows.insert(0, {"id": None, "kind": ops.WATCHDOG_DOWN, "since": wd.last_beat_at.isoformat(),
                        "cleared_at": None, "derived": True,
                        "detail": {"age_seconds": round(wd.age_seconds, 1)}})
    dead = fhir_projection.dead_letter_alert(cases.store)  # SEC-1: events given up on after repeated failures
    if dead is not None:
        rows.insert(0, dead)
    return rows
