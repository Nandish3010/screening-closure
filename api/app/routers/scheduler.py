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
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app import automation, leader, ops
from app.agency import runner as agency_runner
from app.calendar import default_calendar
from app.clock import clock
from app.config import settings
from app.routers import cases, drafts
from app.sweep import SweepPool, run_sweep, sweep_workers_from_env

log = logging.getLogger("app.scheduler")

FOLLOWER_PREFIX = "scheduler-follower-"
HOLDER_PATTERN = r"^[A-Za-z0-9._-]{1,64}$"


def require_current_leader(
    x_leader_holder: str | None = Header(default=None, pattern=HOLDER_PATTERN),
    x_leader_token: int | None = Header(default=None, ge=1),
) -> None:
    """Fencing (A31, ADR-041): a periodic-work call that carries leader headers is honoured only while that holder's
    token is the current one. A scheduler that was deposed (paused, partitioned, its container stopped and started) and
    still sends its old token gets 409 and does nothing. With REQUIRE_LEADER_FENCE=true a call with no headers is refused
    too; otherwise it is allowed (a manual run, a single-scheduler stack)."""
    if x_leader_holder is None and x_leader_token is None:
        if settings.require_leader_fence:
            raise HTTPException(409, "leader fencing is required: send X-Leader-Holder and X-Leader-Token")
        return
    if x_leader_holder is None or x_leader_token is None or not leader.store.is_current(
        leader.SCHEDULER_LEASE, x_leader_holder, x_leader_token
    ):
        raise HTTPException(409, "stale leader: the lease is held by another instance or has expired")


router = APIRouter(prefix="/scheduler", tags=["scheduler"])
fenced = [Depends(require_current_leader)]

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
    sweeping: bool = False  # posted before and during a sweep (a long one must not look like a dead scheduler)
    # A31: a scheduler instance says who it is and whether it leads. A follower idles and is listed apart on /heartbeats;
    # a leader that names itself must hold the current lease (a deposed one cannot beat as `scheduler`).
    role: Literal["leader", "follower"] = "leader"
    holder: str | None = Field(default=None, pattern=HOLDER_PATTERN)
    fencing_token: int | None = Field(default=None, ge=1)


class LeaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    holder: str = Field(pattern=HOLDER_PATTERN)


@router.post("/sweep", dependencies=fenced)
def sweep() -> dict:
    now = clock.now()
    out = _sweep(now)
    # A22: every AGENCY_EVERY_SWEEPS-th sweep starts an agency run in the background (labour, not authority: it
    # writes no case event as the agency). Counted after the sweep, so a sweep is never delayed or failed by it.
    out["agency"] = agency_runner.after_sweep(now)
    return out


def _sweep(now) -> dict:
    calendar = default_calendar()
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


@router.post("/outbox/drain", dependencies=fenced)
def drain_outbox() -> dict:
    """Send every approved draft that has not gone out yet (ADR-030). The approve route drains its own
    row at the end of the request; this is the safety net for an approval whose drain never ran (the
    process died after the approval committed) or failed. Idempotent: a row is sent once. Counts only."""
    outcomes = drafts.drain_outbox()
    counts = {"sent": 0, "blocked": 0, "pending": 0}
    for o in outcomes:
        counts[o.status] = counts.get(o.status, 0) + 1
    return {"drained": len(outcomes), **counts}


@router.post("/automation-scan", dependencies=fenced)
def automation_scan() -> dict:
    """One pass of the automation over the open cases (A21, ADR-032): acknowledge and assign anything still waiting,
    re-route rejected referrals, send the due pre-approved messages. Idempotent (deterministic event ids); counts only.
    A no-op, answering `{"enabled": false}`, unless AUTOMATION_ENABLED is true."""
    return automation.run_automation_scan(cases.store, cases.pathway, clock.now(), default_calendar())


@router.post("/evidence-scan", dependencies=fenced)
def evidence_scan() -> dict:
    """Ask HAPI whether the colonoscopy exists for each booked or completed case; complete the confirmatory step and
    propose the closure when it does (A21). Closure itself stays a person's. Counts only; a no-op unless enabled."""
    return automation.scan_evidence(cases.store, cases.pathway, clock.now())


@router.post("/heartbeat")
def heartbeat(body: Heartbeat) -> dict:
    """Written on every scheduler loop, including after a failed sweep (alive but useless is
    distinguishable from dead). Not a case event."""
    info = body.model_dump(exclude_none=True)
    if body.role == "follower":
        if body.holder is None:
            raise HTTPException(422, "a follower heartbeat names its holder")
        ops.store.beat(FOLLOWER_PREFIX + body.holder, info)
        return {"ok": True, "role": "follower"}
    if body.holder is not None and not leader.store.is_current(leader.SCHEDULER_LEASE, body.holder, body.fencing_token or 0):
        raise HTTPException(409, "stale leader: not the current lease holder")
    ops.store.beat("scheduler", info)
    return {"ok": True, "role": "leader"}


@router.post("/lease")
def lease(body: LeaseRequest) -> dict:
    """Acquire or renew the scheduler lease for `holder` (A31). The scheduler calls it at the top of every loop: `leader: true`
    with the fencing token means do the work, sending the token on each call; `leader: false` means idle and beat as a
    follower. The expiry is the database's, `LEADER_LEASE_TTL_SECONDS` (default 30) after the last renewal."""
    got = leader.store.acquire(leader.SCHEDULER_LEASE, body.holder, settings.leader_lease_ttl_seconds)
    if got is not None:
        return {"leader": True, "holder": got.holder, "token": got.token, "ttl_seconds": settings.leader_lease_ttl_seconds}
    cur = leader.store.current(leader.SCHEDULER_LEASE)
    return {"leader": False, "holder": body.holder, "token": None, "ttl_seconds": settings.leader_lease_ttl_seconds,
            "leader_holder": cur.holder if cur else None}


@router.post("/lease/release")
def release_lease(body: LeaseRequest) -> dict:
    """Give the lease up on a clean shutdown so a follower takes over at its next loop instead of after the TTL."""
    return {"released": leader.store.release(leader.SCHEDULER_LEASE, body.holder)}
