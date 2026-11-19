"""Render a table as CREATE TABLE text for the model to read."""

from __future__ import annotations

from .types import TableDetail

__all__ = ["render_ddl"]


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
