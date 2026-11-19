"""Fixtures for tests that need the seeded Postgres.

Everything under tests/integration is marked `db` automatically. If the database is
not reachable these tests FAIL rather than skip: a skipped test reports green, and
a green run that never touched the database would be a lie. To run without it,
deselect them: `pytest -m "not db"` (or `make test-fast`).
"""

from __future__ import annotations

import psycopg
import pytest

from postgres_schema_mcp.adapters.db import Settings


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" in item.path.parts:
            item.add_marker(pytest.mark.db)


@pytest.fixture(scope="session")
def dsn() -> str:
    settings = Settings()
    try:
        with psycopg.connect(settings.dsn, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception as exc:
        pytest.fail(
            f"the seeded Postgres is not reachable ({exc}). Run `make db`, "
            "or deselect these tests with `pytest -m 'not db'`.",
            pytrace=False,
        )
    return settings.dsn
