"""The MCP server: four tools over a wide Postgres schema.

Built against protocol revision 2026-07-28, using MCPServer from the official
Python SDK v2. There is no session handling in this file because that revision
removed the initialize handshake, notifications/initialized and Mcp-Session-Id;
the protocol is stateless and every request carries its own _meta.

Four tools, deliberately. Every tool description is paid for on every request, so
a faithful mirror of a REST surface is a tax on each call. These four match the
loop an agent actually runs: find the tables, look one up in detail, check the
cost, run it.

They are defined in the order they should be read, and the SDK preserves
registration order in tools/list, so that ordering is stable across processes.
A stable list is worth having: the spec cites prompt-cache hit rate as the
reason.

Tool docstrings are written for a model, not a human. They are the prompt the
model sees, so each says when to reach for the tool and what to do when it fails.
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp.server import CacheHint, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

# Absolute, not relative, imports. `mcp dev src/pg_schema_scout/server.py` loads
# this file by path rather than as a package member, and a relative import then
# fails with "attempted relative import with no known parent package". The
# package is installed, so absolute imports resolve under both loaders.
from pg_schema_scout.db import Database
from pg_schema_scout.guard import BlockedQuery, check_read_only
from pg_schema_scout.retrieval import SchemaIndex, count_tokens, tokenizer_name
from pg_schema_scout.types import QueryPlan, QueryResult, SchemaSlice, TableDetail, TableMatch

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

# Applied to all four tools. The defaults in the spec are pessimistic
# (destructive, open-world, non-idempotent), so omitting these would describe
# this server as the opposite of what it is.
#
# These are hints and nothing more. The spec says clients must treat annotations
# from an untrusted server as untrusted, which is correct and is why the real
# enforcement is guard.py and the scout_ro role rather than this line.
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

mcp = MCPServer(
    "pg-schema-scout",
    version="0.1.0",
    instructions=INSTRUCTIONS,
    # Honest hints. The tool list is derived from this server's own code, so it
    # is stable for the life of the process and identical for every caller:
    # public, five minutes. Not longer, because a redeploy can legitimately
    # change it, and a stale tool list is worse than a re-fetch.
    cache_hints={
        "tools/list": CacheHint(ttl_ms=300_000, scope="public"),
        "server/discover": CacheHint(ttl_ms=300_000, scope="public"),
    },
)

_db: Database | None = None
_index: SchemaIndex | None = None
_lock = asyncio.Lock()


async def get_db() -> Database:
    """Open the pool on first use and reuse it thereafter."""
    global _db
    if _db is None:
        async with _lock:
            if _db is None:
                db = Database()
                await db.open()
                _db = db
    return _db


async def get_index() -> SchemaIndex:
    """Introspect and index the schema once, on first use."""
    global _index
    if _index is None:
        db = await get_db()
        async with _lock:
            if _index is None:
                _index = SchemaIndex(await db.introspect())
    return _index


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


@mcp.tool(annotations=READ_ONLY)
async def search_schema(question: str, limit: int = 8) -> SchemaSlice:
    """Find the tables relevant to a question and return their DDL.

    Prefer this over trying to read the whole schema. The schema is far larger
    than the part of it any one question needs, and sending all of it costs
    context you will want for the answer. Pass the user's question in natural
    language, not a table name guess: this ranks on column names, types and the
    schema's own comments.

    Returns the highest-scoring tables with their columns and keys, plus the
    foreign-key edges *between the returned tables*, which is what you need to
    write the joins.

    On failure or a disappointing result:
      * If a table you know you need is missing from the results, call
        describe_table with its name. Retrieval ranks, and a needed table can
        fall below the cutoff; describe_table is the escape hatch and does not
        depend on the ranking.
      * If nothing looks relevant, re-ask with the domain words the user used
        (for example "reseller", "purchase order", "pay history") before
        widening limit. Raising limit costs tokens and rarely fixes a
        vocabulary mismatch.
      * Do not invent table names. If you cannot find it here, say so.
    """
    index = await get_index()
    matches = index.search(question, limit)
    tables = [t for t, _ in matches]

    slice_ddl = "\n\n".join(t.ddl for t in tables)
    note = (
        "These are the highest-ranked tables, not the whole schema. If the table you need is "
        "not here, call describe_table with its name; retrieval can rank a needed table below "
        "the cutoff."
    )
    return SchemaSlice(
        question=question,
        tables=[
            TableMatch(
                schema_name=t.schema_name,
                name=t.name,
                qualified_name=t.qualified_name,
                score=round(score, 4),
                row_estimate=t.row_estimate,
                comment=t.comment,
                ddl=t.ddl,
            )
            for t, score in matches
        ],
        foreign_keys=index.edges_within(tables),
        estimated_tokens=count_tokens(slice_ddl),
        full_schema_tokens=index.full_schema_tokens(),
        tokenizer=tokenizer_name(),
        backend=index.backend_name,
        note=note,
    )


@mcp.tool(annotations=READ_ONLY)
async def describe_table(name: str) -> TableDetail:
    """Return full detail for one table by name.

    Use this when you already know the table name: either search_schema returned
    it and you need the complete column list, or search_schema did *not* return
    it and you believe it exists anyway. This does not use the ranking, so it is
    the reliable way to reach a table that retrieval scored poorly.

    Accepts either a qualified name ("sales.salesorderheader") or a bare one
    ("salesorderheader"). Returns columns with types and comments, the primary
    key, foreign keys out, and the foreign keys pointing in, which is how you
    find the tables that join to this one.

    On failure:
      * "No table named ..." means it does not exist under that name. The error
        lists close matches; try one of those. Do not retry the same name, and
        do not guess a third spelling. Fall back to search_schema with a
        description of what the table should contain.
    """
    index = await get_index()
    table = index.lookup(name)
    if table is None:
        wanted = name.strip().lower()
        close = sorted(t.qualified_name for t in index.tables if wanted in t.qualified_name.lower())[:8]
        hint = f" Close matches: {', '.join(close)}." if close else ""
        raise ToolError(
            f"No table named {name!r} in the indexed schemas "
            f"({', '.join(sorted({t.schema_name for t in index.tables}))}).{hint} "
            "Use search_schema to find it by description."
        )
    return table


@mcp.tool(annotations=READ_ONLY)
async def explain_query(sql: str) -> QueryPlan:
    """Plan a SELECT without executing it.

    Use this before run_query when the query touches a large table, has no
    WHERE clause, or joins several tables, so you can see the cost before
    committing to it. The query is planned only; no rows are read and nothing is
    executed. EXPLAIN ANALYZE is deliberately not offered, because it would run
    the statement.

    Read total_cost as a relative number, useful for comparing two phrasings of
    the same query rather than as a time.

    On failure:
      * "Blocked: ..." means the same read-only policy as run_query rejected it.
        Rewrite as a plain SELECT.
      * A "column does not exist" error means the schema assumption was wrong.
        Call describe_table and fix the column names.
    """
    _guard(sql)
    db = await get_db()
    try:
        return await db.explain(sql)
    except Exception as exc:
        raise ToolError(f"Could not plan the query: {exc}") from exc


@mcp.tool(annotations=READ_ONLY)
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
    """Entry point. stdio by default; Streamable HTTP when asked.

    HTTP+SSE is not offered: it is deprecated as of this protocol revision, and
    the SDK's remaining "sse" transport exists for older clients.
    """
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
