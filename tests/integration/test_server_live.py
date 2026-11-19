"""The four tools end to end against the seeded database, through the real start-up path."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from postgres_schema_mcp import server

pytestmark = pytest.mark.usefixtures("dsn")


@pytest.fixture(autouse=True)
async def cold_live_server(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """Start each test with no pool and no index, and close whatever it opened."""
    monkeypatch.setattr(server, "_db", None)
    monkeypatch.setattr(server, "_index", None)
    monkeypatch.setattr(server, "_lock", asyncio.Lock())
    yield
    if server._db is not None:
        await server._db.close()


async def test_search_then_describe_then_query() -> None:
    found = await server.search_schema("Which sales orders had the highest total due in 2013?")
    names = [t.qualified_name for t in found.tables]
    assert "sales.salesorderheader" in names

    detail = await server.describe_table("sales.salesorderheader")
    assert any(c.name == "totaldue" for c in detail.columns)

    result = await server.run_query(
        "SELECT salesorderid, totaldue FROM sales.salesorderheader ORDER BY totaldue DESC",
        max_rows=3,
    )
    assert result.columns == ["salesorderid", "totaldue"]
    assert result.row_count == 3
    assert result.truncated is True


async def test_explain_query_plans_a_join() -> None:
    plan = await server.explain_query(
        "SELECT h.salesorderid FROM sales.salesorderheader h "
        "JOIN sales.salesorderdetail d USING (salesorderid)"
    )
    assert plan.total_cost is not None
    assert "Join" in plan.plan


async def test_a_blocked_write_never_reaches_the_database() -> None:
    with pytest.raises(ToolError, match="Blocked"):
        await server.run_query(
            "WITH gone AS (DELETE FROM sales.store RETURNING *) SELECT * FROM gone"
        )
    after = await server.run_query("SELECT count(*) AS n FROM sales.store", max_rows=1)
    assert after.rows[0][0] > 0


async def test_a_database_error_comes_back_as_a_tool_error_the_model_can_read() -> None:
    with pytest.raises(ToolError, match=r"Query failed: .*nope"):
        await server.run_query("SELECT nope FROM person.person")
    with pytest.raises(ToolError, match=r"Could not plan the query: .*nope"):
        await server.explain_query("SELECT nope FROM person.person")
