"""T-F2: the integration tests refuse to run against anything not marked as a test database."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from testdb import NotATestDatabase, assert_test_database, database_name

DEV = "postgresql://closure:closure_dev_password@localhost:5433/closure"
TEST = "postgresql://closure:closure_dev_password@localhost:5433/closure_test"


def test_dev_database_refused():
    with pytest.raises(NotATestDatabase, match="refusing to run integration tests"):
        assert_test_database(DEV)


@pytest.mark.parametrize("url", [DEV + "?sslmode=disable", DEV + "/", "postgresql://u@h/prod", "postgresql://u@h/"])
def test_anything_unmarked_is_refused(url):
    with pytest.raises(NotATestDatabase):
        assert_test_database(url)


def test_test_database_accepted():
    assert database_name(TEST) == "closure_test"
    assert assert_test_database(TEST) == "closure_test"


def test_session_aborts_on_the_dev_url():
    """A real pytest run, with the dev URL, over an integration test: aborts before running it."""
    tests_dir = Path(__file__).parent
    env = {**os.environ, "DATABASE_URL": DEV}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(tests_dir / "test_store_pg.py"), "-q", "-m", "integration",
         "-p", "no:cacheprovider"],
        env=env, capture_output=True, text=True, timeout=120, cwd=tests_dir.parents[1],
    )
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "refusing to run integration tests against database 'closure'" in proc.stdout + proc.stderr
    assert " passed" not in proc.stdout
