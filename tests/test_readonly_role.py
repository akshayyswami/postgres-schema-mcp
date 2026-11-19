"""Proof that the database refuses writes on its own.

Every statement here bypasses guard.py completely and is sent straight to
Postgres as scout_ro. That is the entire point: if the AST policy had a bug
tomorrow, these are the errors that would still come back. A test that went
through the guard would only be testing the guard twice.

Requires the seeded container (`docker compose up -d`). Marked `db`, so
`pytest -m "not db"` skips the whole module.
"""

from __future__ import annotations

import psycopg
import pytest

from postgres_schema_mcp.db import Settings

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def dsn() -> str:
    settings = Settings()
    try:
        with psycopg.connect(settings.dsn, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"seeded database not reachable ({exc}); run `docker compose up -d`")
    return settings.dsn


@pytest.fixture
def conn(dsn: str) -> psycopg.Connection[tuple[object, ...]]:
    settings = Settings()
    with psycopg.connect(dsn, options=settings.session_options, connect_timeout=5) as c:
        yield c


def test_the_server_connects_as_the_read_only_role(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT current_user")
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "scout_ro", "the server must not connect as an owner or superuser"


def test_the_role_is_not_a_superuser(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
            "FROM pg_roles WHERE rolname = current_user"
        )
        row = cur.fetchone()
    assert row is not None
    assert row == (False, False, False, False)


def test_reads_work(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """The other half of the two-sided test: a read-only role must still read."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM person.person")
        row = cur.fetchone()
    assert row is not None
    assert row[0] > 0


WRITE_ATTEMPTS = [
    pytest.param("DELETE FROM person.person", id="delete"),
    pytest.param("UPDATE person.person SET firstname = 'x'", id="update"),
    pytest.param("INSERT INTO person.businessentity (businessentityid) VALUES (999999)", id="insert"),
    pytest.param("TRUNCATE person.person", id="truncate"),
    pytest.param("DROP TABLE person.person", id="drop"),
    pytest.param("ALTER TABLE person.person ADD COLUMN injected int", id="alter"),
    pytest.param("CREATE TABLE public.evil (i int)", id="create-table"),
    pytest.param("CREATE TEMP TABLE t AS SELECT 1", id="create-temp-table"),
    pytest.param("GRANT ALL ON person.person TO scout_ro", id="grant"),
    pytest.param("CREATE INDEX injected_ix ON person.person (businessentityid)", id="create-index"),
]


@pytest.mark.parametrize("sql", WRITE_ATTEMPTS)
def test_database_refuses_writes_without_help_from_the_guard(
    conn: psycopg.Connection[tuple[object, ...]], sql: str
) -> None:
    with pytest.raises(psycopg.Error) as exc, conn.cursor() as cur:
        cur.execute(sql)  # type: ignore[arg-type]

    message = str(exc.value).lower()
    assert any(
        phrase in message for phrase in ("permission denied", "must be owner", "read-only transaction")
    ), f"expected a privilege error from Postgres, got: {exc.value}"


def test_the_data_is_still_there_afterwards(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """Cheap belt-and-braces: none of the attempts above changed anything."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM person.person")
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 19972


def test_session_is_read_only_by_default(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    with conn.cursor() as cur:
        cur.execute("SHOW default_transaction_read_only")
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "on"


def test_statement_timeout_is_set(conn: psycopg.Connection[tuple[object, ...]]) -> None:
    """An unbounded query is a denial of service even when it is read-only."""
    with conn.cursor() as cur:
        cur.execute("SHOW statement_timeout")
        row = cur.fetchone()
    assert row is not None
    assert row[0] not in ("0", "0ms"), "statement_timeout must be set"


def test_idle_in_transaction_timeout_is_set(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    with conn.cursor() as cur:
        cur.execute("SHOW idle_in_transaction_session_timeout")
        row = cur.fetchone()
    assert row is not None
    assert row[0] not in ("0", "0ms")
