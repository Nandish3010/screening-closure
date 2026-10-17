"""Guard for the integration tests' database (F2, ADR-015).

Integration tests write cases and events and never clean up, so they must never run against
the dev database (`closure`), which holds the seeded cohort. The rule: the database named in
DATABASE_URL must end in `_test`. `make test-db` points at `closure_test`.
"""

from urllib.parse import unquote, urlparse

TEST_DB_SUFFIX = "_test"


class NotATestDatabase(RuntimeError):
    pass


def database_name(url: str) -> str:
    return unquote(urlparse(url).path.lstrip("/"))


def assert_test_database(url: str) -> str:
    """Return the database name if it is marked as a test database, else raise."""
    name = database_name(url)
    if not name.endswith(TEST_DB_SUFFIX):
        raise NotATestDatabase(
            f"refusing to run integration tests against database {name!r}: the name must end in "
            f"{TEST_DB_SUFFIX!r} (the dev database is 'closure' and holds the seeded cohort). "
            "Use `make test-db`, or DATABASE_URL=postgresql://closure:closure_dev_password"
            "@localhost:5433/closure_test"
        )
    return name
