"""Simulation clock control (demo use). Supervisor only; see app.policy.ROUTE_POLICY.

Development only (SEC-1, ADR-029): app.main registers this router only when APP_ENV=dev, so in any
other environment `/clock/*` does not exist (404). Moving a clinical safety clock is a demo
affordance, never a production one.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.clock import clock
from app.routers import cases

router = APIRouter(prefix="/clock", tags=["clock"])


class Advance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: float | None = Field(default=None, ge=0, le=3650)
    hours: float | None = Field(default=None, ge=0, le=24 * 3650)


@router.post("/advance")
def advance(body: Advance) -> dict:
    """Move the sim clock forward by `days` and/or `hours`. It never moves backwards."""
    if not (body.days or body.hours):
        raise HTTPException(422, "give days and/or hours, greater than zero")
    return {"sim_time": clock.advance(days=body.days or 0, hours=body.hours or 0).isoformat()}


@router.post("/reset")
def reset() -> dict:
    """Return the clock to its configured start (the demo reset).

    Refused (409) once any event is dated after that start: rewinding past recorded events would
    leave a log whose events lie in the clock's future, and every timer computed from it wrong.
    To undo a demo, restore the database snapshot (`make demo-reset`) instead.
    """
    if cases.store.has_events_after(clock.start):
        raise HTTPException(
            409,
            "events exist after the clock's start time: the clock cannot be reset "
            "(restore the demo snapshot instead)",
        )
    return {"sim_time": clock.reset().isoformat()}
