import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app import auth, evidence, notify, policy, users
from app.clock import clock
from app.config import settings
from app.events import InMemoryEventStore
from app.pathway import load_pathway
from app.routers import auth as auth_router
from app.routers import cases, scheduler
from app.routers import clock as clock_router

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
            PostgresUserDirectory,
        )

        pool = create_pool(database_url)
        run_migrations(pool)
        cases.store = PostgresEventStore(pool)
        notify.log = PostgresNotificationLog(pool)
        users.directory = PostgresUserDirectory(pool)
        resumed = clock.bind(PostgresClockStore(pool, "main"))
        log.info("event store: postgres; sim clock resumed at %s", resumed.isoformat())
    else:
        cases.store = InMemoryEventStore()
        notify.log = notify.InMemoryNotificationLog()
        users.directory = users.InMemoryUserDirectory()
        log.warning("event store: IN-MEMORY (DATABASE_URL not set); data is not durable")
    try:
        yield
    finally:
        if pool is not None:
            pool.close()


# Every route passes through the policy table; a route without an entry is refused.
app = FastAPI(
    title="Screening Closure Engine",
    version="0.1.0",
    lifespan=lifespan,
    dependencies=[Depends(policy.enforce_route_policy)],
)
app.include_router(auth_router.router)
app.include_router(cases.router)
app.include_router(clock_router.router)
app.include_router(scheduler.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "sim_time": clock.now().isoformat()}


@app.get("/pathways")
def pathways() -> dict:
    """Return the loaded pathway config as parsed from YAML."""
    return pathway.raw
