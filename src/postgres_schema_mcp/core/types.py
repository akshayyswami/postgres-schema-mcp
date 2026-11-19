"""Pydantic models for the four tools' return values.

Returning a model rather than a string is what makes the SDK emit an
`outputSchema` on tools/list and `structuredContent` on tools/call, so the client
gets typed results without any hand-written JSON schema.

Field descriptions are not decoration. They are rendered into the outputSchema
the model reads, so they are written for that reader.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "ColumnInfo",
    "ForeignKey",
    "QueryPlan",
    "QueryResult",
    "SchemaSlice",
    "TableDetail",
    "TableMatch",
]


class ColumnInfo(BaseModel):
    """One column of a table."""

    name: str
    type: str = Field(description="PostgreSQL type, e.g. 'integer' or 'character varying(50)'.")
    nullable: bool
    default: str | None = None
    comment: str | None = None
    is_primary_key: bool = False


class ForeignKey(BaseModel):
    """A foreign-key edge from one table to another."""

    constraint_name: str
    columns: list[str]
    references_schema: str
    references_table: str
    references_columns: list[str]

    @property
    def references_qualified(self) -> str:
        return f"{self.references_schema}.{self.references_table}"


class TableMatch(BaseModel):
    """One table in a retrieved slice, with the score that put it there."""

    schema_name: str = Field(description="Schema the table lives in, e.g. 'sales'.")
    name: str = Field(description="Table name without the schema prefix.")
    qualified_name: str = Field(description="schema.table, the form to use in SQL.")
    score: float = Field(description="Retrieval score. Comparable within one result set only.")
    row_estimate: int | None = Field(
        default=None, description="Planner's row estimate, useful for judging query cost."
    )
    comment: str | None = None
    ddl: str = Field(description="CREATE TABLE statement for this table, including column types.")


class SchemaSlice(BaseModel):
    """The tables relevant to a question, instead of the whole schema."""

    question: str
    tables: list[TableMatch]
    foreign_keys: list[ForeignKey] = Field(
        default_factory=list,
        description=(
            "Foreign-key edges *between tables in this slice*, so joins can be written "
            "without fetching more schema."
        ),
    )
    estimated_tokens: int = Field(
        description="Token count of this slice's DDL, by the tokenizer named in tokenizer."
    )
    full_schema_tokens: int = Field(
        description="Token count of the entire schema's DDL, for comparison with estimated_tokens."
    )
    tokenizer: str = Field(description="Which tokenizer produced the counts.")
    backend: str = Field(
        description="Retrieval backend that ranked these tables: 'bm25' or 'titan'."
    )
    note: str | None = Field(
        default=None,
        description="Guidance for the model, including what to do if the table it needs is absent.",
    )


class TableDetail(BaseModel):
    """Everything known about one table."""

    schema_name: str
    name: str
    qualified_name: str
    comment: str | None = None
    columns: list[ColumnInfo]
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKey] = Field(
        default_factory=list, description="Edges from this table to others."
    )
    referenced_by: list[ForeignKey] = Field(
        default_factory=list, description="Edges from other tables into this one."
    )
    ddl: str
    row_estimate: int | None = None


class QueryPlan(BaseModel):
    """An EXPLAIN result. The query is planned but never executed."""

    sql: str
    plan: str = Field(description="EXPLAIN output, one line per plan node.")
    total_cost: float | None = Field(
        default=None, description="Planner's estimated total cost of the top node."
    )
    plan_rows: int | None = Field(default=None, description="Planner's estimated output row count.")
    note: str | None = None


class QueryResult(BaseModel):
    """Rows from a read-only query."""

    columns: list[str]
    rows: list[list[Any]] = Field(description="Row-major values, aligned with columns.")
    row_count: int = Field(description="Number of rows returned, after any truncation.")
    truncated: bool = Field(description="True when the result hit max_rows and more rows exist.")
    note: str | None = Field(
        default=None,
        description="Set when truncated, explaining how to narrow the query.",
    )
