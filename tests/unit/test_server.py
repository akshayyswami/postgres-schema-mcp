"""The four tools, the lazy start-up, and the entry point, against a fake database.

Nothing here needs Postgres. The index is the real one, built from the frozen
catalog, so ranking and DDL are exercised for real. Only the connection is fake.
The same tools run against a live database in tests/integration/test_server_live.py.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from postgres_schema_mcp import server
from postgres_schema_mcp.core.retrieval import SchemaIndex
from postgres_schema_mcp.core.types import QueryPlan, QueryResult, TableDetail


class FakeDatabase:
    """Stands in for adapters.db.Database. Records what reached it."""

    def __init__(self, tables: list[TableDetail] | None = None) -> None:
        self.tables = tables or []
        self.open_calls = 0
        self.introspect_calls = 0
        self.statements: list[str] = []
        self.error: Exception | None = None

    async def open(self) -> None:
        self.open_calls += 1
        await asyncio.sleep(0)  # yield, so concurrent callers really do interleave

    async def introspect(self) -> list[TableDetail]:
        self.introspect_calls += 1
        return self.tables

    async def fetch_rows(self, sql: str, max_rows: int) -> QueryResult:
        self.statements.append(sql)
        if self.error:
            raise self.error
        return QueryResult(
            columns=["n"], rows=[[max_rows]], row_count=1, truncated=False, note=None
        )

    async def explain(self, sql: str) -> QueryPlan:
        self.statements.append(sql)
        if self.error:
            raise self.error
        return QueryPlan(sql=sql, plan="Seq Scan on person", total_cost=1.5, plan_rows=3)


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch, catalog: list[TableDetail]) -> FakeDatabase:
    """Install a fake database and a real index over the frozen catalog."""
    db = FakeDatabase(catalog)
    monkeypatch.setattr(server, "_db", db)
    monkeypatch.setattr(server, "_index", SchemaIndex(catalog))
    return db


@pytest.fixture
def cold_server(monkeypatch: pytest.MonkeyPatch, catalog: list[TableDetail]) -> FakeDatabase:
    """A server that has not connected yet, so the lazy start-up path runs."""
    db = FakeDatabase(catalog)
    monkeypatch.setattr(server, "_db", None)
    monkeypatch.setattr(server, "_index", None)
    monkeypatch.setattr(server, "_lock", asyncio.Lock())
    monkeypatch.setattr(server, "Database", lambda: db)
    return db


async def test_four_tools_are_registered_with_honest_read_only_hints() -> None:
    tools = await server.mcp.list_tools()
    assert [t.name for t in tools] == [
        "search_schema",
        "describe_table",
        "explain_query",
        "run_query",
    ]
    for tool in tools:
        hints = tool.annotations
        assert hints is not None
        assert hints.read_only_hint is True
        assert hints.destructive_hint is False
        assert hints.idempotent_hint is True
        assert hints.open_world_hint is False


async def test_search_schema_finds_the_table_a_question_needs(fake_db: FakeDatabase) -> None:
    result = await server.search_schema("Which sales orders had the highest total due in 2013?")
    assert "sales.salesorderheader" in [t.qualified_name for t in result.tables]
    assert len(result.tables) == 8
    assert result.backend == "bm25"
    assert result.tokenizer
    assert fake_db.statements == []  # search never touches the database after start-up


async def test_search_schema_reports_the_saving_it_is_measured_against(
    fake_db: FakeDatabase,
) -> None:
    result = await server.search_schema("employee pay history", limit=3)
    assert len(result.tables) == 3
    assert 0 < result.estimated_tokens < result.full_schema_tokens
    assert "describe_table" in (result.note or "")


async def test_search_schema_only_lists_foreign_keys_between_returned_tables(
    fake_db: FakeDatabase,
) -> None:
    result = await server.search_schema("what products were on each sales order", limit=4)
    present = {t.qualified_name for t in result.tables}
    assert result.foreign_keys, "a slice of joined tables should carry at least one edge"
    for fk in result.foreign_keys:
        assert f"{fk.references_schema}.{fk.references_table}" in present


async def test_describe_table_accepts_qualified_and_bare_names(fake_db: FakeDatabase) -> None:
    qualified = await server.describe_table("sales.salesorderheader")
    bare = await server.describe_table("  SalesOrderHeader ")
    assert qualified.qualified_name == bare.qualified_name == "sales.salesorderheader"
    assert qualified.primary_key


async def test_describe_table_unknown_name_lists_close_matches(fake_db: FakeDatabase) -> None:
    with pytest.raises(ToolError, match=r"Close matches:.*sales\.salesorderheader"):
        await server.describe_table("salesorder")


async def test_describe_table_unknown_name_without_matches_still_points_to_search(
    fake_db: FakeDatabase,
) -> None:
    with pytest.raises(ToolError) as exc:
        await server.describe_table("zzz")
    assert "No table named 'zzz'" in str(exc.value)
    assert "Close matches" not in str(exc.value)
    assert "search_schema" in str(exc.value)


BLOCKED = [
    "DROP TABLE customer;",
    "WITH gone AS (DELETE FROM customer RETURNING *) SELECT * FROM gone;",
    "SELECT 1; SELECT 2;",
]


@pytest.mark.parametrize("sql", BLOCKED)
async def test_run_query_blocks_before_the_database_is_reached(
    fake_db: FakeDatabase, sql: str
) -> None:
    with pytest.raises(ToolError, match="Blocked"):
        await server.run_query(sql)
    assert fake_db.statements == []


@pytest.mark.parametrize("sql", BLOCKED)
async def test_explain_query_applies_the_same_policy(fake_db: FakeDatabase, sql: str) -> None:
    with pytest.raises(ToolError, match="Blocked"):
        await server.explain_query(sql)
    assert fake_db.statements == []


async def test_run_query_passes_a_clean_select_through(fake_db: FakeDatabase) -> None:
    result = await server.run_query("SELECT 'DROP TABLE customer' AS note", max_rows=7)
    assert result.rows == [[7]]
    assert fake_db.statements == ["SELECT 'DROP TABLE customer' AS note"]


async def test_run_query_wraps_a_database_failure_as_a_tool_error(fake_db: FakeDatabase) -> None:
    fake_db.error = RuntimeError('column "nope" does not exist')
    with pytest.raises(ToolError, match=r"Query failed: column \"nope\" does not exist"):
        await server.run_query("SELECT nope FROM person.person")


async def test_explain_query_returns_a_plan(fake_db: FakeDatabase) -> None:
    plan = await server.explain_query("SELECT * FROM person.person")
    assert plan.total_cost == 1.5
    assert plan.plan_rows == 3


async def test_explain_query_wraps_a_database_failure_as_a_tool_error(
    fake_db: FakeDatabase,
) -> None:
    fake_db.error = RuntimeError("relation does not exist")
    with pytest.raises(ToolError, match="Could not plan the query: relation does not exist"):
        await server.explain_query("SELECT * FROM nope")


async def test_the_pool_is_opened_once_even_when_calls_overlap(cold_server: FakeDatabase) -> None:
    dbs = await asyncio.gather(*(server.get_db() for _ in range(5)))
    assert all(db is dbs[0] for db in dbs)
    assert cold_server.open_calls == 1


async def test_the_schema_is_introspected_once_and_indexed_once(cold_server: FakeDatabase) -> None:
    first = await server.get_index()
    second = await server.get_index()
    assert first is second
    assert cold_server.introspect_calls == 1
    assert len(first.tables) == 68


def _record_run(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: calls.append(kwargs))
    return calls


def test_main_defaults_to_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_run(monkeypatch)
    monkeypatch.delenv("PGSS_TRANSPORT", raising=False)
    monkeypatch.setattr(sys, "argv", ["postgres-schema-mcp"])
    server.main()
    assert calls == [{"transport": "stdio"}]


def test_main_serves_streamable_http_with_the_http_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_run(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["postgres-schema-mcp", "--http"])
    monkeypatch.setenv("PGSS_HOST", "0.0.0.0")  # noqa: S104
    monkeypatch.setenv("PGSS_PORT_HTTP", "3999")
    server.main()
    assert calls == [{"transport": "streamable-http", "host": "0.0.0.0", "port": 3999}]  # noqa: S104


def test_main_http_defaults_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_run(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["postgres-schema-mcp"])
    monkeypatch.setenv("PGSS_TRANSPORT", "streamable-http")
    monkeypatch.delenv("PGSS_HOST", raising=False)
    monkeypatch.delenv("PGSS_PORT_HTTP", raising=False)
    server.main()
    assert calls == [{"transport": "streamable-http", "host": "127.0.0.1", "port": 3000}]
