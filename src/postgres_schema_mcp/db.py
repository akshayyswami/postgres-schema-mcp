"""Connection pool and the read-only execution path.

Layer 3 of the safety model lives here in the sense that this module is what
chooses *which role to authenticate as*. It connects as scout_ro, which holds
SELECT and nothing else (see init/20-grants.sql). Everything below that is
belt-and-braces on top of a role that already cannot write:

  * `default_transaction_read_only=on` at the session level, so every
    transaction is READ ONLY without the caller having to remember;
  * an explicit read-only transaction around each query;
  * `statement_timeout`, so a legitimate but expensive query cannot become a
    denial of service;
  * `idle_in_transaction_session_timeout`, so a wedged client cannot hold locks;
  * a row cap, with the truncation reported to the model rather than hidden.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from decimal import Decimal
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from typing import Any, Self
from uuid import UUID

import psycopg
from psycopg.rows import tuple_row
from psycopg_pool import AsyncConnectionPool

from .types import ColumnInfo, ForeignKey, QueryPlan, QueryResult, TableDetail

__all__ = ["Database", "Settings"]

DEFAULT_DSN = "postgresql://scout_ro:scout_ro_pw@localhost:55432/adventureworks"

# The five real AdventureWorks schemas. The shorthand view schemas that the
# upstream loader also creates (pe, hr, pr, pu, sa) are excluded: they are 68
# convenience views over these same 68 tables, so indexing them would double
# every table and inflate the "full schema" baseline that the README compares
# against. Excluding them makes the headline ratio harder to hit, not easier.
DEFAULT_SCHEMAS = ("person", "humanresources", "production", "purchasing", "sales")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw is None or not raw.strip() else int(raw)


@dataclass(frozen=True)
class Settings:
    """Runtime configuration, all overridable by environment variable."""

    dsn: str = field(default_factory=lambda: os.environ.get("PGSS_DSN", DEFAULT_DSN))
    schemas: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            s.strip()
            for s in os.environ.get("PGSS_SCHEMAS", ",".join(DEFAULT_SCHEMAS)).split(",")
            if s.strip()
        )
    )
    statement_timeout_ms: int = field(default_factory=lambda: _env_int("PGSS_STATEMENT_TIMEOUT_MS", 10_000))
    idle_in_transaction_timeout_ms: int = field(
        default_factory=lambda: _env_int("PGSS_IDLE_TX_TIMEOUT_MS", 10_000)
    )
    # Hard ceiling on max_rows, regardless of what the caller asks for.
    max_rows_cap: int = field(default_factory=lambda: _env_int("PGSS_MAX_ROWS_CAP", 1_000))
    pool_min_size: int = field(default_factory=lambda: _env_int("PGSS_POOL_MIN", 1))
    pool_max_size: int = field(default_factory=lambda: _env_int("PGSS_POOL_MAX", 4))

    @property
    def session_options(self) -> str:
        """libpq `options` string applied to every connection in the pool."""
        return " ".join(
            (
                f"-c statement_timeout={self.statement_timeout_ms}",
                f"-c idle_in_transaction_session_timeout={self.idle_in_transaction_timeout_ms}",
                "-c default_transaction_read_only=on",
            )
        )


def _jsonable(value: Any) -> Any:
    """Coerce a Postgres value into something that survives JSON serialisation.

    Decimal becomes a string rather than a float: these are money and quantity
    columns, and silently rounding them would be worse than a type change the
    model can see.
    """
    match value:
        case None | bool() | int() | float() | str():
            return value
        case Decimal():
            return str(value)
        case dt.datetime() | dt.date() | dt.time():
            return value.isoformat()
        case dt.timedelta():
            return str(value)
        case UUID() | IPv4Address() | IPv6Address() | IPv4Network() | IPv6Network():
            return str(value)
        case bytes() | bytearray() | memoryview():
            return bytes(value).hex()
        case list():
            return [_jsonable(v) for v in value]
        case tuple():
            return [_jsonable(v) for v in value]
        case dict():
            return {str(k): _jsonable(v) for k, v in value.items()}
        case _:
            return str(value)


_COLUMNS_SQL = """
SELECT n.nspname                                        AS schema_name,
       c.relname                                        AS table_name,
       a.attname                                        AS column_name,
       format_type(a.atttypid, a.atttypmod)             AS data_type,
       NOT a.attnotnull                                 AS nullable,
       pg_get_expr(ad.adbin, ad.adrelid)                AS column_default,
       col_description(c.oid, a.attnum)                 AS column_comment,
       a.attnum                                         AS ordinal
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
LEFT JOIN pg_attrdef ad ON ad.adrelid = c.oid AND ad.adnum = a.attnum
WHERE c.relkind = 'r'
  AND a.attnum > 0
  AND NOT a.attisdropped
  AND n.nspname = ANY(%(schemas)s)
ORDER BY n.nspname, c.relname, a.attnum
"""

_TABLES_SQL = """
SELECT n.nspname                    AS schema_name,
       c.relname                    AS table_name,
       obj_description(c.oid)       AS table_comment,
       c.reltuples::bigint          AS row_estimate
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname = ANY(%(schemas)s)
ORDER BY n.nspname, c.relname
"""

_CONSTRAINTS_SQL = """
SELECT con.contype                                       AS kind,
       con.conname                                       AS constraint_name,
       sn.nspname                                        AS schema_name,
       sc.relname                                        AS table_name,
       (SELECT array_agg(att.attname ORDER BY u.ord)
          FROM unnest(con.conkey) WITH ORDINALITY AS u(attnum, ord)
          JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = u.attnum
       )                                                 AS columns,
       tn.nspname                                        AS ref_schema,
       tc.relname                                        AS ref_table,
       (SELECT array_agg(att.attname ORDER BY u.ord)
          FROM unnest(con.confkey) WITH ORDINALITY AS u(attnum, ord)
          JOIN pg_attribute att ON att.attrelid = con.confrelid AND att.attnum = u.attnum
       )                                                 AS ref_columns
FROM pg_constraint con
JOIN pg_class sc ON sc.oid = con.conrelid
JOIN pg_namespace sn ON sn.oid = sc.relnamespace
LEFT JOIN pg_class tc ON tc.oid = con.confrelid
LEFT JOIN pg_namespace tn ON tn.oid = tc.relnamespace
WHERE con.contype IN ('p', 'f') AND sn.nspname = ANY(%(schemas)s)
"""


def render_ddl(table: TableDetail) -> str:
    """A CREATE TABLE rendering of a table, for the model to read.

    Deliberately not pg_dump output: no storage parameters, no tablespaces, no
    ownership. Those cost tokens and answer no question a query author has.
    """
    lines = [f"CREATE TABLE {table.qualified_name} ("]
    body: list[str] = []
    for col in table.columns:
        piece = f"    {col.name} {col.type}"
        if not col.nullable:
            piece += " NOT NULL"
        if col.comment:
            piece += f"  -- {col.comment}"
        body.append(piece)
    if table.primary_key:
        body.append(f"    PRIMARY KEY ({', '.join(table.primary_key)})")
    for fk in table.foreign_keys:
        body.append(
            f"    FOREIGN KEY ({', '.join(fk.columns)}) "
            f"REFERENCES {fk.references_schema}.{fk.references_table} "
            f"({', '.join(fk.references_columns)})"
        )
    lines.append(",\n".join(body))
    lines.append(");")
    if table.comment:
        lines.append(f"-- {table.comment}")
    return "\n".join(lines)


class Database:
    """Async connection pool, always connected as the read-only role."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self._pool: AsyncConnectionPool | None = None

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def open(self) -> None:
        if self._pool is not None:
            return
        pool = AsyncConnectionPool(
            conninfo=self.settings.dsn,
            min_size=self.settings.pool_min_size,
            max_size=self.settings.pool_max_size,
            kwargs={"options": self.settings.session_options, "row_factory": tuple_row},
            open=False,
        )
        await pool.open(wait=True, timeout=30)
        self._pool = pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> AsyncConnectionPool:
        if self._pool is None:
            raise RuntimeError("Database.open() has not been called")
        return self._pool

    async def fetch_rows(self, sql: str, max_rows: int) -> QueryResult:
        """Run an already-guarded SELECT and return at most `max_rows` rows.

        The caller is responsible for having passed `sql` through guard.py first.
        """
        limit = max(1, min(max_rows, self.settings.max_rows_cap))
        # conn.transaction() is an explicit read-only transaction, on top of the
        # session-level default_transaction_read_only.
        async with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            await cur.execute(psycopg.sql.SQL(sql))
            if cur.description is None:
                return QueryResult(
                    columns=[],
                    rows=[],
                    row_count=0,
                    truncated=False,
                    note="The statement returned no result set.",
                )
            columns = [d.name for d in cur.description]
            # Fetch one extra row: that is how truncation is detected without
            # a second COUNT query.
            fetched = await cur.fetchmany(limit + 1)

        truncated = len(fetched) > limit
        rows = [[_jsonable(v) for v in row] for row in fetched[:limit]]
        note = None
        if truncated:
            note = (
                f"Truncated to {limit} rows; more rows matched. This is a display limit, not the "
                "full answer. To see the rest, narrow the query: add a WHERE clause, aggregate "
                "with GROUP BY, or raise max_rows (capped at "
                f"{self.settings.max_rows_cap})."
            )
        return QueryResult(columns=columns, rows=rows, row_count=len(rows), truncated=truncated, note=note)

    async def explain(self, sql: str) -> QueryPlan:
        """EXPLAIN a guarded SELECT. Never ANALYZE, so the query does not run."""
        async with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            await cur.execute(psycopg.sql.SQL(f"EXPLAIN (FORMAT JSON) {sql}"))
            row = await cur.fetchone()

        payload: Any = row[0] if row else None
        top: dict[str, Any] = {}
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            top = payload[0].get("Plan", {})

        async with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            await cur.execute(psycopg.sql.SQL(f"EXPLAIN {sql}"))
            text_rows = await cur.fetchall()

        return QueryPlan(
            sql=sql,
            plan="\n".join(str(r[0]) for r in text_rows),
            total_cost=top.get("Total Cost"),
            plan_rows=top.get("Plan Rows"),
            note=(
                "Planner estimate only; the query was not executed. Costs are in arbitrary "
                "planner units, useful for comparing two queries against each other."
            ),
        )

    async def introspect(self) -> list[TableDetail]:
        """Read the schema once. Called at startup to build the retrieval index."""
        params = {"schemas": list(self.settings.schemas)}
        async with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            await cur.execute(_TABLES_SQL, params)
            table_rows = await cur.fetchall()
            await cur.execute(_COLUMNS_SQL, params)
            column_rows = await cur.fetchall()
            await cur.execute(_CONSTRAINTS_SQL, params)
            constraint_rows = await cur.fetchall()

        primary_keys: dict[tuple[str, str], list[str]] = {}
        outgoing: dict[tuple[str, str], list[ForeignKey]] = {}
        incoming: dict[tuple[str, str], list[ForeignKey]] = {}

        for kind, name, schema_name, table_name, cols, ref_schema, ref_table, ref_cols in constraint_rows:
            key = (schema_name, table_name)
            if kind == "p":
                primary_keys[key] = list(cols or [])
                continue
            if not ref_schema or not ref_table:
                continue
            fk = ForeignKey(
                constraint_name=name,
                columns=list(cols or []),
                references_schema=ref_schema,
                references_table=ref_table,
                references_columns=list(ref_cols or []),
            )
            outgoing.setdefault(key, []).append(fk)
            incoming.setdefault((ref_schema, ref_table), []).append(fk)

        columns_by_table: dict[tuple[str, str], list[ColumnInfo]] = {}
        for schema_name, table_name, col, dtype, nullable, default, comment, _ord in column_rows:
            key = (schema_name, table_name)
            columns_by_table.setdefault(key, []).append(
                ColumnInfo(
                    name=col,
                    type=dtype,
                    nullable=bool(nullable),
                    default=default,
                    comment=comment,
                    is_primary_key=False,
                )
            )

        tables: list[TableDetail] = []
        for schema_name, table_name, comment, row_estimate in table_rows:
            key = (schema_name, table_name)
            pk = primary_keys.get(key, [])
            cols = columns_by_table.get(key, [])
            for c in cols:
                c.is_primary_key = c.name in pk
            detail = TableDetail(
                schema_name=schema_name,
                name=table_name,
                qualified_name=f"{schema_name}.{table_name}",
                comment=comment,
                columns=cols,
                primary_key=pk,
                foreign_keys=sorted(outgoing.get(key, []), key=lambda f: f.constraint_name),
                referenced_by=sorted(incoming.get(key, []), key=lambda f: f.constraint_name),
                ddl="",
                row_estimate=int(row_estimate) if row_estimate is not None else None,
            )
            detail.ddl = render_ddl(detail)
            tables.append(detail)

        return tables
