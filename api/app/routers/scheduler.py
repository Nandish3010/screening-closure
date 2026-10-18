"""Scheduler-facing routes: one sweep over the open cases at the current sim time, and the
scheduler's heartbeat.

Called periodically by the scheduler service with its service token (policy: scheduler only).
The sweep itself is `app.sweep`; keeping it in the API process lets it read and write the event
store directly instead of shipping every case over HTTP.

Parallel sweep (A17). With the Postgres store and `SWEEP_WORKERS` > 1 (default min(4, cpus)) the
route runs `run_sweep_parallel` on a long-lived pool of worker processes, each sweeping a disjoint
slice of the cases (replaying every open case is CPU-bound Python, so slices scale with cores).
`SWEEP_WORKERS=1`, or any store that is not Postgres (the in-memory store of the unit tests), keeps
the single-process `run_sweep`. Both give the same events: ids are deterministic and every append
goes through the transactional store, so a second sweep, or a sweep that overlaps another, emits
nothing new. The workers write through their own plain stores, so when FHIR projection is on the
events they appended are handed to the projector here (its retry queue, then one drain).
"""

import atexit
import logging
import threading
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app import ops
from app.calendar import default_calendar
from app.clock import clock
from app.routers import cases, drafts
from app.sweep import SweepPool, run_sweep, sweep_workers_from_env

log = logging.getLogger("app.scheduler")

router = APIRouter(prefix="/scheduler", tags=["scheduler"])

_pool: SweepPool | None = None
_pool_lock = threading.Lock()


def get_pool() -> SweepPool:
    """The process-wide sweep pool, sized from SWEEP_WORKERS on first use."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = SweepPool(sweep_workers_from_env())
            atexit.register(reset_pool)
        return _pool


def reset_pool() -> None:
    """Shut the pool down and forget it (tests, and process exit)."""
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown()


def _postgres_url(store) -> str | None:
    """The connection string of the Postgres store behind `store` (through the FHIR-projecting
    wrapper if there is one), or None for any other store."""
    inner = getattr(store, "_inner", store)
    try:
        from app.store_pg import PostgresEventStore
    except ImportError:  # psycopg not installed: only the in-memory store can exist
        return None
    if isinstance(inner, PostgresEventStore):
        return inner._pool.conninfo
    return None


def _project_emitted(store, refs) -> None:
    """Workers append without the API's projecting wrapper, so queue what they wrote for FHIR
    projection (durable) and drain it in the background, off the request."""
    projector = getattr(store, "projector", None)
    if projector is None or not refs:
        return
    for event_id, case_id in refs:
        projector.queue.add(UUID(event_id), UUID(case_id), "appended by a sweep worker")
    threading.Thread(target=projector.drain, kwargs={"limit": len(refs)}, name="fhir-sweep-drain", daemon=True).start()


class Heartbeat(BaseModel):
    """What the scheduler says about itself. No timestamps: the API stamps the arrival."""

    model_config = ConfigDict(extra="forbid")
    sweep_ok: bool  # did this loop's sweep work?
    consecutive_failures: int = Field(default=0, ge=0, le=100000)
    interval_seconds: float = Field(default=10, gt=0, le=3600)
    sim_time: str | None = Field(default=None, max_length=40)


@router.post("/sweep")
def sweep() -> dict:
    now, calendar = clock.now(), default_calendar()
    pool = get_pool()
    url = _postgres_url(cases.store) if pool.workers > 1 else None
    if url is None:
        out = run_sweep(cases.store, cases.pathway, calendar, now).to_json()
        out["workers"] = 1
        return out
    record = getattr(cases.store, "projector", None) is not None
    result = pool.run(url, cases.pathway, calendar, now, record=record)
    _project_emitted(cases.store, result.emitted_refs)
    out = result.to_json()
    out["workers"] = pool.workers
    return out


@router.post("/outbox/drain")
def drain_outbox() -> dict:
    """Send every approved draft that has not gone out yet (ADR-030). The approve route drains its own
    row at the end of the request; this is the safety net for an approval whose drain never ran (the
    process died after the approval committed) or failed. Idempotent: a row is sent once. Counts only."""
    outcomes = drafts.drain_outbox()
    counts = {"sent": 0, "blocked": 0, "pending": 0}
    for o in outcomes:
        counts[o.status] = counts.get(o.status, 0) + 1
    return {"drained": len(outcomes), **counts}


@router.post("/heartbeat")
def heartbeat(body: Heartbeat) -> dict:
    """Written on every scheduler loop, including after a failed sweep (alive but useless is
    distinguishable from dead). Not a case event."""
    ops.store.beat("scheduler", body.model_dump())
    return {"ok": True}
