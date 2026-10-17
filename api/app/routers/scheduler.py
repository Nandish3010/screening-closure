"""Scheduler-facing routes: one sweep over the open cases at the current sim time, and the
scheduler's heartbeat.

Called periodically by the scheduler service with its service token (policy: scheduler only).
The sweep itself is `app.sweep.run_sweep`; keeping it in the API process lets it read and
write the event store directly instead of shipping every case over HTTP.
"""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app import ops
from app.calendar import default_calendar
from app.clock import clock
from app.routers import cases
from app.sweep import run_sweep

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


class Heartbeat(BaseModel):
    """What the scheduler says about itself. No timestamps: the API stamps the arrival."""

    model_config = ConfigDict(extra="forbid")
    sweep_ok: bool  # did this loop's sweep work?
    consecutive_failures: int = Field(default=0, ge=0, le=100000)
    interval_seconds: float = Field(default=10, gt=0, le=3600)
    sim_time: str | None = Field(default=None, max_length=40)


@router.post("/sweep")
def sweep() -> dict:
    return run_sweep(cases.store, cases.pathway, default_calendar(), clock.now()).to_json()


@router.post("/heartbeat")
def heartbeat(body: Heartbeat) -> dict:
    """Written on every scheduler loop, including after a failed sweep (alive but useless is
    distinguishable from dead). Not a case event."""
    ops.store.beat("scheduler", body.model_dump())
    return {"ok": True}
