import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import auth, automation, confirmatory, evidence, fhir_projection, leader, notify, ops, outreach_policy, policy, users
from app.clock import clock
from app.config import settings
from app.events import InMemoryEventStore
from app.pathway import load_pathway
from app.routers import auth as auth_router
from app.routers import agency as agency_router
from app.routers import cases, drafts, replies, scheduler, watchdog
from app.routers import hl7 as hl7_router
from app.routers import clock as clock_router
from app.routers import metrics as metrics_router

logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
log = logging.getLogger("app.main")

pathway = load_pathway(settings.pathway_path)
cases.pathway = pathway


def _start_clock(clock_store) -> None:
    """Bring the clock up against the event log (A30, ADR-041). Sim mode resumes from the persisted row, and if the log
    already holds events later than that (a stretch in wall mode wrote them) moves forward to the latest, never back.
    Wall mode touches no clock row, and refuses to start if the log holds an event dated in the future (a sim stretch ran
    ahead of real time): every timer computed from that log would be wrong."""
    latest = cases.store.latest_event_at()
    if clock.mode == "wall":
        now = clock.now()
        if latest is not None and latest > now:
            raise RuntimeError(
                f"CLOCK_MODE=wall but the event log holds an event dated {latest.isoformat()}, after now ({now.isoformat()}): "
                "the database was written under a simulation clock that ran ahead of real time. Use CLOCK_MODE=sim, or "
                "restore a snapshot."
            )
        log.info("clock: WALL (real UTC time); the persisted sim clock row is left untouched")
        return
    resumed = clock.bind(clock_store)
    if latest is not None and latest > resumed:
        log.warning("clock: SIM resumed at %s but the log holds events up to %s (written in wall mode); moving forward",
                    resumed.isoformat(), latest.isoformat())
        resumed = clock.advance_to(latest)
    log.info("clock: SIM resumed at %s", resumed.isoformat())


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.assert_secure_config()
    # Read the process environment (not .env/settings): a stray .env must never silently
    # switch a local run onto a database. Unset means the in-memory store.
    database_url = os.environ.get("DATABASE_URL")
    pool = None
    confirmatory.configure(settings)
    if isinstance(evidence.configure(settings), evidence.FhirVerifier):
        log.info("closure evidence is verified against %s", settings.fhir_base_url)
    else:
        log.warning("closure evidence is NOT verified (FHIR_VERIFY_EVIDENCE is not true)")
    if database_url:
        from app.db import create_pool, run_migrations
        from app.store_pg import (
            PostgresClockStore,
            PostgresEventStore,
            PostgresNotificationLog,
            PostgresOpsStore,
            PostgresUserDirectory,
        )

        pool = create_pool(database_url)
        run_migrations(pool)
        cases.store = PostgresEventStore(pool)
        notify.log = PostgresNotificationLog(pool)
        users.directory = PostgresUserDirectory(pool)
        ops.store = PostgresOpsStore(pool)
        _start_clock(PostgresClockStore(pool, "main"))
        leader.store = leader.PostgresLeaseStore(pool)
        leader.api_loop = leader.LeaderLoop(leader.API_LEASE, ttl=settings.leader_lease_ttl_seconds)
        leader.api_loop.start()  # A31: one API replica runs the background loops; the others idle until it stops renewing
        log.info("event store: postgres; clock mode: %s; api background leader: %s", clock.mode, leader.api_loop.is_leader)
    else:
        cases.store = InMemoryEventStore()
        notify.log = notify.InMemoryNotificationLog()
        users.directory = users.InMemoryUserDirectory()
        ops.store = ops.InMemoryOpsStore()
        log.warning("event store: IN-MEMORY (DATABASE_URL not set); data is not durable")
    if settings.fhir_projection:
        from app.calendar import default_calendar

        cases.store = fhir_projection.wrap_store(cases.store, settings, pathway, default_calendar(), pool)
        log.info("FHIR projection is ON: events are written to %s", settings.fhir_base_url)
    else:
        log.warning("FHIR projection is OFF (FHIR_PROJECTION is not true)")
    automation.configure(settings)
    try:
        yield
    finally:
        projector = getattr(cases.store, "projector", None)
        if projector is not None:
            projector.stop()
        if leader.api_loop is not None:
            leader.api_loop.stop()
            leader.api_loop = None
        if pool is not None:
            pool.close()


# Every route passes through the policy table; a route without an entry is refused.
app = FastAPI(
    title="Screening Closure Engine",
    version="0.1.0",
    lifespan=lifespan,
    dependencies=[Depends(policy.enforce_route_policy)],
)
# The web app is served from another local port; it sends `Authorization: Bearer ...`, so the browser
# preflights every call. Only the configured local origins are allowed (settings.cors_allow_origins).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(auth_router.router)
app.include_router(cases.router)
if settings.app_env == "dev" and clock.mode == "sim":  # the demo clock exists only in development, and only in sim mode (SEC-1, A30)
    app.include_router(clock_router.router)
app.include_router(scheduler.router)
app.include_router(watchdog.router)
app.include_router(metrics_router.router)
app.include_router(drafts.router)
app.include_router(replies.router)
app.include_router(agency_router.router)  # A22: the agency (labour, not authority, ADR-033)
app.include_router(hl7_router.router)  # A28: the HL7 v2 adapter's transport (ADR-039)


@app.get("/health")
def health() -> dict:
    """Public, no patient data. `outreach` (A26, ADR-037) says whether automatic patient messages are signed off:
    `demo_approval` is true while they run only on the demo placeholder, and the web shows an amber banner with
    `approved_by`."""
    return {"status": "ok", "sim_time": clock.now().isoformat(), "clock_mode": clock.mode, "outreach": outreach_policy.public_status()}


@app.get("/pathways")
def pathways() -> dict:
    """Return the loaded pathway config as parsed from YAML."""
    return pathway.raw
