"""Scheduler-facing route: one sweep over the open cases at the current sim time.

Called periodically by the scheduler service with its service token (policy: scheduler only).
The sweep itself is `app.sweep.run_sweep`; keeping it in the API process lets it read and
write the event store directly instead of shipping every case over HTTP.
"""

from fastapi import APIRouter

from app.calendar import default_calendar
from app.clock import clock
from app.routers import cases
from app.sweep import run_sweep

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


@router.post("/sweep")
def sweep() -> dict:
    return run_sweep(cases.store, cases.pathway, default_calendar(), clock.now()).to_json()
