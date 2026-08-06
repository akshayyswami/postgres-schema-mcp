"""The MCP server: four tools over a wide Postgres schema.

Built against protocol revision 2026-07-28, using MCPServer from the official
Python SDK v2. There is no session handling in this file because that revision
removed the initialize handshake, notifications/initialized and Mcp-Session-Id;
the protocol is stateless and every request carries its own _meta.

Tool docstrings are written for a model, not for a human reader. They are the
prompt the model sees, so each one says when to reach for the tool and what to do
when it fails.
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp.server import CacheHint, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .db import Database
from .guard import BlockedQuery, check_read_only
from .types import QueryResult

__all__ = ["main", "mcp"]

INSTRUCTIONS = """\
This server exposes a wide PostgreSQL schema (AdventureWorks: 68 tables across
five schemas) to you by retrieval rather than by dumping it.

Start with search_schema. Do not ask for the whole schema; it does not fit
usefully in context, and the tables you need are a small fraction of it.

Typical loop: search_schema to find the relevant tables, describe_table if one
you need is missing or you need full column detail, explain_query to sanity-check
cost on anything large, then run_query.

Every tool is read-only and enforced as such. Writes are rejected by an AST
policy and, independently, by a database role that holds only SELECT.
"""

mcp = MCPServer(
    "pg-schema-scout",
    version="0.1.0",
    instructions=INSTRUCTIONS,
    # Honest hints. The tool list is derived from this server's own code, so it
    # is stable for the life of the process and identical for every caller:
    # public, five minutes. It is not marked immutable because a redeploy can
    # legitimately change it.
    cache_hints={
        "tools/list": CacheHint(ttl_ms=300_000, scope="public"),
        "server/discover": CacheHint(ttl_ms=300_000, scope="public"),
    },
)

_db: Database | None = None
_db_lock = asyncio.Lock()


async def get_db() -> Database:
    """Open the pool on first use and reuse it thereafter."""
    global _db
    if _db is None:
        async with _db_lock:
            if _db is None:
                db = Database()
                await db.open()
                _db = db
    return _db


def _guard(sql: str) -> None:
    """Apply the read-only policy, converting a rejection into a tool error.

    A blocked query is a *tool execution error*, not a protocol error: the SDK
    turns any non-MCPError exception raised here into a successful response with
    isError: true, which is what lets the model read the reason and retry. A
    JSON-RPC error would be wrong; nothing about the protocol failed.
    """
    try:
        check_read_only(sql)
    except BlockedQuery as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
async def run_query(sql: str, max_rows: int = 200) -> QueryResult:
    """Execute a read-only SELECT and return the rows.

    Use this once you know which tables and columns you need. If you do not yet
    know, call search_schema first rather than guessing table names; a query
    against a table that does not exist wastes a round trip.

    Only a single SELECT is accepted. WITH is fine as long as the whole statement
    is read-only. Anything else, including INSERT/UPDATE/DELETE, DDL, statement
    stacking, and DML hidden inside a CTE, is rejected before it reaches the
    database.

    On failure:
      * "Blocked: ..." means the statement violated the read-only policy. The
        message names the offending construct. Rewrite as a plain SELECT; do not
        retry the same statement.
      * A syntax or "column does not exist" error means the schema assumption was
        wrong. Call describe_table on the table in question and correct the
        column names rather than guessing again.
      * A statement timeout means the query was too expensive. Call explain_query
        to see the plan, then add a WHERE clause or aggregate.

    Results are capped. When truncated is true the rows shown are a prefix, not
    the answer; narrow the query instead of treating them as complete.
    """
    _guard(sql)
    db = await get_db()
    try:
        return await db.fetch_rows(sql, max_rows)
    except Exception as exc:
        raise ToolError(f"Query failed: {exc}") from exc


def main() -> None:
    """Entry point. stdio by default; Streamable HTTP when asked."""
    transport = os.environ.get("PGSS_TRANSPORT", "stdio")
    if "--http" in sys.argv:
        transport = "streamable-http"

    if transport == "streamable-http":
        mcp.run(
            transport="streamable-http",
            host=os.environ.get("PGSS_HOST", "127.0.0.1"),
            port=int(os.environ.get("PGSS_PORT_HTTP", "3000")),
        )
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
