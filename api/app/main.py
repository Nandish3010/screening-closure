import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import auth, automation, confirmatory, evidence, fhir_projection, notify, ops, outreach_policy, policy, users
from app.clock import clock
from app.config import settings
from app.events import InMemoryEventStore
from app.pathway import load_pathway
from app.routers import auth as auth_router
from app.routers import agency as agency_router
from app.routers import cases, drafts, erasure as erasure_router, replies, scheduler, watchdog
from app.routers import hl7 as hl7_router
from app.routers import clock as clock_router
from app.routers import metrics as metrics_router

logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
log = logging.getLogger("app.main")

pathway = load_pathway(settings.pathway_path)
cases.pathway = pathway


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
        resumed = clock.bind(PostgresClockStore(pool, "main"))
        log.info("event store: postgres; sim clock resumed at %s", resumed.isoformat())
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
if settings.app_env == "dev":  # the demo clock exists only in development (SEC-1, ADR-029)
    app.include_router(clock_router.router)
app.include_router(scheduler.router)
app.include_router(watchdog.router)
app.include_router(metrics_router.router)
app.include_router(drafts.router)
app.include_router(replies.router)
app.include_router(agency_router.router)  # A22: the agency (labour, not authority, ADR-033)
app.include_router(erasure_router.router)  # A32: erasure of a patient's personal data (ADR-025)
app.include_router(hl7_router.router)  # A28: the HL7 v2 adapter's transport (ADR-039)


@app.get("/health")
def health() -> dict:
    """Public, no patient data. `outreach` (A26, ADR-037) says whether automatic patient messages are signed off:
    `demo_approval` is true while they run only on the demo placeholder, and the web shows an amber banner with
    `approved_by`."""
    return {"status": "ok", "sim_time": clock.now().isoformat(), "outreach": outreach_policy.public_status()}


@app.get("/pathways")
def pathways() -> dict:
    """Return the loaded pathway config as parsed from YAML."""
    return pathway.raw
