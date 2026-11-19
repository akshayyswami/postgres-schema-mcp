"""The Postgres adapter against the seeded database: introspection, row cap, EXPLAIN, limits."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import psycopg
import pytest

from postgres_schema_mcp.adapters.db import Database, Settings
from postgres_schema_mcp.core.types import TableDetail

pytestmark = pytest.mark.usefixtures("dsn")


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    async with Database() as database:
        yield database


async def test_introspect_reads_every_table_with_columns_keys_and_ddl(db: Database) -> None:
    tables = await db.introspect()
    assert len(tables) == 68
    assert {t.schema_name for t in tables} == {
        "person",
        "humanresources",
        "production",
        "purchasing",
        "sales",
    }
    for table in tables:
        assert table.columns
        assert table.ddl.startswith(f"CREATE TABLE {table.qualified_name} (")
    by_name = {t.qualified_name: t for t in tables}
    header = by_name["sales.salesorderheader"]
    assert header.primary_key == ["salesorderid"]
    assert any(c.is_primary_key for c in header.columns)
    assert any(fk.references_table == "customer" for fk in header.foreign_keys)
    assert any(
        fk.references_table == "salesorderheader"
        for fk in by_name["sales.salesorderdetail"].foreign_keys
    )
    assert any(r.constraint_name for r in header.referenced_by)


async def test_introspect_matches_the_frozen_catalog(
    db: Database, catalog: list[TableDetail]
) -> None:
    live = sorted(await db.introspect(), key=lambda t: t.qualified_name)
    for table in live:
        table.row_estimate = None  # planner statistics vary, so the fixture drops them
    assert [t.model_dump() for t in live] == [t.model_dump() for t in catalog]


async def test_fetch_rows_returns_everything_when_under_the_cap(db: Database) -> None:
    result = await db.fetch_rows("SELECT 1 AS one, 'a' AS letter", max_rows=10)
    assert result.columns == ["one", "letter"]
    assert result.rows == [[1, "a"]]
    assert result.truncated is False
    assert result.note is None


async def test_fetch_rows_says_so_when_it_truncates(db: Database) -> None:
    result = await db.fetch_rows(
        "SELECT businessentityid FROM person.person ORDER BY 1", max_rows=3
    )
    assert result.row_count == 3
    assert result.truncated is True
    assert "Truncated to 3 rows" in (result.note or "")


async def test_fetch_rows_never_exceeds_the_configured_cap() -> None:
    async with Database(Settings(max_rows_cap=5)) as db:
        result = await db.fetch_rows("SELECT businessentityid FROM person.person", max_rows=1000)
    assert result.row_count == 5
    assert result.truncated is True
    assert "capped at 5" in (result.note or "")


async def test_fetch_rows_returns_money_as_a_string_not_a_float(db: Database) -> None:
    result = await db.fetch_rows(
        "SELECT listprice FROM production.product WHERE listprice > 0 ORDER BY productid",
        max_rows=1,
    )
    assert isinstance(result.rows[0][0], str)


async def test_fetch_rows_reports_a_statement_with_no_result_set(db: Database) -> None:
    result = await db.fetch_rows("SET statement_timeout = 5000", max_rows=5)
    assert result.columns == []
    assert result.rows == []
    assert "no result set" in (result.note or "")


async def test_fetch_rows_cannot_write_even_without_the_guard(db: Database) -> None:
    with pytest.raises(psycopg.Error):
        await db.fetch_rows("DELETE FROM person.person", max_rows=1)


async def test_a_statement_that_runs_too_long_is_cancelled() -> None:
    async with Database(Settings(statement_timeout_ms=200)) as db:
        with pytest.raises(psycopg.errors.QueryCanceled):
            await db.fetch_rows("SELECT pg_sleep(5)", max_rows=1)


async def test_explain_returns_the_plan_and_estimates(db: Database) -> None:
    plan = await db.explain("SELECT * FROM person.person WHERE businessentityid = 1")
    assert "person" in plan.plan
    assert plan.total_cost is not None
    assert plan.plan_rows is not None
    assert "not executed" in (plan.note or "")


async def test_explain_plans_without_running_the_query(db: Database) -> None:
    started = time.perf_counter()
    plan = await db.explain("SELECT pg_sleep(5)")
    assert time.perf_counter() - started < 3, "EXPLAIN must not execute the statement"
    assert plan.plan


async def test_the_pool_is_reused_and_close_is_safe_to_repeat() -> None:
    database = Database()
    await database.open()
    first = database.pool
    await database.open()
    assert database.pool is first
    await database.close()
    await database.close()
