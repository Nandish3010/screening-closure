"""Test-session guard: integration tests run only against a database marked as a test database."""

import os

import pytest

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
