"""Test-session guard: integration tests run only against a database marked as a test database."""

import os

import pytest

import xdist_db
from testdb import NotATestDatabase, assert_test_database


def pytest_collection_finish(session):
    """Before any test runs: if integration tests were selected and a database is configured,
    it must be a test database. Aborts the whole session with a clear error otherwise."""
    url = os.environ.get("DATABASE_URL")
    if not url or not any(i.get_closest_marker("integration") for i in session.items):
        return
    try:
        assert_test_database(url)
    except NotATestDatabase as exc:
        pytest.exit(str(exc), returncode=3)
    # F15: under xdist every worker works in its own copy of the test database (see xdist_db.py).
    worker = xdist_db.worker_id(session.config)
    if worker:
        worker_url = xdist_db.prepare_worker_database(url, worker)
        os.environ["DATABASE_URL"] = worker_url
        session.config._closure_worker_db = worker_url


def pytest_sessionfinish(session, exitstatus):
    worker_url = getattr(session.config, "_closure_worker_db", None)
    if worker_url:
        xdist_db.drop_worker_database(worker_url)


@pytest.fixture(scope="session", autouse=True)
def _test_database_ready():
    """Migrate the test database once per session (idempotent; modules also call it)."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        yield
        return
    from app.db import create_pool, run_migrations

    try:
        assert_test_database(url)
    except NotATestDatabase:
        yield  # no integration test selected (the collection guard would have aborted)
        return
    pool = create_pool(url, max_size=2)
    try:
        run_migrations(pool)
    finally:
        pool.close()
    yield


@pytest.fixture(autouse=True)
def _outreach_demo_approval_on(monkeypatch):
    """The shipped outreach policy is unsigned (A26, ADR-037) and an unsigned policy sends nothing. Most tests exercise the
    automatic path, as the compose demo does, under OUTREACH_DEMO_APPROVAL; the tests of the unsigned behaviour turn it off."""
    from app.config import settings

    monkeypatch.setattr(settings, "outreach_demo_approval", True)


@pytest.fixture(autouse=True)
def _agency_hook_off(monkeypatch):
    """The scheduler hook (A22) starts an agency run every AGENCY_EVERY_SWEEPS sweeps, in the background. Tests that
    sweep must not get agency drafts on their cases at an arbitrary moment, so it is off unless a test turns it on."""
    from app.config import settings

    monkeypatch.setattr(settings, "agency_every_sweeps", 0)


@pytest.fixture(autouse=True)
def _clock_mode_wall_for_prod_config(monkeypatch):
    """A30: outside APP_ENV=dev the API refuses to start unless CLOCK_MODE=wall. `settings.clock_mode` only feeds that check
    and the route registration at import; the `clock` object is fixed at import, so setting it here changes nothing else.
    The tests of the check itself set it explicitly."""
    from app.config import settings

    monkeypatch.setattr(settings, "clock_mode", "wall")
