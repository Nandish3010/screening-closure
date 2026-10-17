"""Simulation clock control (demo use). Supervisor only; see app.policy.ROUTE_POLICY."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.clock import clock

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
    """Return the clock to its configured start (the demo reset)."""
    return {"sim_time": clock.reset().isoformat()}
