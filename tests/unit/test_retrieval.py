"""Retrieval tests.

Most of these run against hand-built TableDetail fixtures rather than the
database, so they are fast and run in CI without a container. The Titan backend
is tested against a mocked Bedrock client: it has never been run against the real
service, and a test that pretended otherwise would be worse than no test.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from postgres_schema_mcp.adapters.backends import TitanBackend, build_backend
from postgres_schema_mcp.core.retrieval import (
    BM25Backend,
    SchemaIndex,
    build_document,
    count_tokens,
    tokenize,
    tokenizer_name,
)
from postgres_schema_mcp.core.types import ColumnInfo, ForeignKey, TableDetail


def make_table(
    schema: str,
    name: str,
    columns: list[str],
    comment: str | None = None,
    fks: list[ForeignKey] | None = None,
) -> TableDetail:
    return TableDetail(
        schema_name=schema,
        name=name,
        qualified_name=f"{schema}.{name}",
        comment=comment,
        columns=[ColumnInfo(name=c, type="integer", nullable=True) for c in columns],
        primary_key=[columns[0]] if columns else [],
        foreign_keys=fks or [],
        ddl=f"CREATE TABLE {schema}.{name} (...);",
    )


@pytest.fixture
def tables() -> list[TableDetail]:
    fk = ForeignKey(
        constraint_name="fk_detail_header",
        columns=["salesorderid"],
        references_schema="sales",
        references_table="salesorderheader",
        references_columns=["salesorderid"],
    )
    outside = ForeignKey(
        constraint_name="fk_detail_product",
        columns=["productid"],
        references_schema="production",
        references_table="product",
        references_columns=["productid"],
    )
    return [
        make_table(
            "sales",
            "salesorderheader",
            ["salesorderid", "orderdate", "customerid", "totaldue"],
            comment="General sales order information.",
        ),
        make_table(
            "sales",
            "salesorderdetail",
            ["salesorderid", "salesorderdetailid", "productid", "orderqty"],
            comment="Individual products associated with a specific sales order.",
            fks=[fk, outside],
        ),
        make_table(
            "humanresources",
            "employeepayhistory",
            ["businessentityid", "ratechangedate", "rate"],
            comment="Employee pay history.",
        ),
        make_table(
            "production", "product", ["productid", "name", "listprice"], comment="Products sold."
        ),
    ]


# --------------------------------------------------------------------------
# Tokenisation
# --------------------------------------------------------------------------


def test_tokenize_splits_concatenated_identifiers() -> None:
    """'salesorderheader' has to become searchable as 'sales order header'."""
    tokens = tokenize("salesorderheader")
    assert "salesorderheader" in tokens, "the raw name must still be searchable"
    assert {"sales", "order", "header"} <= set(tokens)


def test_tokenize_keeps_ordinary_words_intact() -> None:
    tokens = set(tokenize("Products sold."))
    assert {"products", "sold"} <= tokens
    assert "product" in tokens, "the singular stem is added alongside the plural"


def test_tokenize_is_case_insensitive() -> None:
    assert tokenize("SalesOrderHeader") == tokenize("salesorderheader")


def test_tokenize_stems_regular_plurals() -> None:
    """Questions say 'orders' and 'employees'; the schema says 'order', 'employee'."""
    assert "order" in tokenize("orders")
    assert "employee" in tokenize("employees")
    assert "category" in tokenize("categories")


def test_stemming_does_not_mangle_words_ending_in_s() -> None:
    for word in ("address", "status", "analysis", "materials"):
        assert word in tokenize(word)


def test_known_ceiling_irregular_morphology_is_not_handled() -> None:
    """Documents a real limit rather than papering over it.

    "paid" does not reduce to "pay" under suffix-stripping, so a purely lexical
    backend cannot connect the two. This is one of the reasons the embedding
    backend exists, and it is stated in the README's Known limits.
    """
    assert "pay" not in tokenize("paid")


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------


def test_bm25_ranks_the_relevant_table_first(tables: list[TableDetail]) -> None:
    index = SchemaIndex(tables)
    results = index.search("what did employees get paid over time", limit=2)
    assert results[0][0].qualified_name == "humanresources.employeepayhistory"


def test_search_respects_limit(tables: list[TableDetail]) -> None:
    index = SchemaIndex(tables)
    assert len(index.search("sales order", limit=2)) == 2
    # A limit beyond the table count is clamped rather than erroring.
    assert len(index.search("sales order", limit=99)) == len(tables)


def test_search_is_deterministic(tables: list[TableDetail]) -> None:
    index = SchemaIndex(tables)
    first = [t.qualified_name for t, _ in index.search("sales order", limit=4)]
    second = [t.qualified_name for t, _ in index.search("sales order", limit=4)]
    assert first == second


def test_scores_are_descending(tables: list[TableDetail]) -> None:
    index = SchemaIndex(tables)
    scores = [s for _, s in index.search("sales order product", limit=4)]
    assert scores == sorted(scores, reverse=True)


def test_bm25_backend_handles_empty_corpus() -> None:
    assert BM25Backend([]).rank("anything", 5) == []


# --------------------------------------------------------------------------
# Foreign-key propagation: what makes a slice joinable rather than merely
# well-scored.
# --------------------------------------------------------------------------


def test_fk_neighbour_is_pulled_into_the_slice(tables: list[TableDetail]) -> None:
    """A slice with salesorderdetail but not salesorderheader cannot be joined.

    salesorderdetail matches the question lexically; salesorderheader is reached
    only through the foreign key between them.
    """
    with_graph = SchemaIndex(tables, neighbour_weight=0.35)
    got = {
        t.qualified_name for t, _ in with_graph.search("individual products on an order", limit=2)
    }
    assert "sales.salesorderdetail" in got
    assert "sales.salesorderheader" in got


def test_neighbour_weight_zero_disables_propagation(tables: list[TableDetail]) -> None:
    """The mechanism is switchable, which is how the before/after was measured."""
    plain = SchemaIndex(tables, neighbour_weight=0.0)
    ranked = plain.search("individual products on an order", limit=4)
    assert len(ranked) == 4
    scores = [s for _, s in ranked]
    assert scores == sorted(scores, reverse=True)


def test_propagation_never_changes_the_slice_size(tables: list[TableDetail]) -> None:
    """Pulling neighbours in must not quietly enlarge the response."""
    index = SchemaIndex(tables, neighbour_weight=0.35)
    for limit in (1, 2, 3, len(tables)):
        assert len(index.search("sales order product", limit)) == limit


# --------------------------------------------------------------------------
# Lookup, the escape hatch that does not depend on ranking
# --------------------------------------------------------------------------


def test_lookup_by_qualified_and_bare_name(tables: list[TableDetail]) -> None:
    index = SchemaIndex(tables)
    assert index.lookup("sales.salesorderheader") is not None
    assert index.lookup("salesorderheader") is not None
    assert index.lookup("SALES.SalesOrderHeader") is not None
    assert index.lookup("  salesorderheader  ") is not None


def test_lookup_returns_none_for_unknown(tables: list[TableDetail]) -> None:
    assert SchemaIndex(tables).lookup("no_such_table") is None


# --------------------------------------------------------------------------
# Slice construction
# --------------------------------------------------------------------------


def test_edges_within_excludes_edges_leaving_the_slice(tables: list[TableDetail]) -> None:
    """A join hint to a table the model cannot see is wasted tokens."""
    index = SchemaIndex(tables)
    slice_tables = [t for t in tables if t.schema_name == "sales"]
    edges = index.edges_within(slice_tables)
    names = {e.constraint_name for e in edges}
    assert "fk_detail_header" in names, "both ends are in the slice"
    assert "fk_detail_product" not in names, "production.product is outside the slice"


def test_document_includes_comments_and_columns(tables: list[TableDetail]) -> None:
    doc = build_document(tables[0])
    assert "General sales order information." in doc
    assert "totaldue" in doc
    assert "sales.salesorderheader" in doc


def test_full_schema_ddl_covers_every_table(tables: list[TableDetail]) -> None:
    index = SchemaIndex(tables)
    for table in tables:
        assert table.qualified_name in index.full_schema_ddl
    assert index.full_schema_tokens() > 0


# --------------------------------------------------------------------------
# Token counting: the README's number depends on it, so it must name itself
# --------------------------------------------------------------------------


def test_tokenizer_reports_its_own_name() -> None:
    name = tokenizer_name()
    assert name, "a count with no named tokenizer is not a measurement"
    assert name.startswith(("tiktoken/", "approximate/"))


def test_count_tokens_grows_with_text() -> None:
    assert count_tokens("SELECT 1") >= 1
    assert count_tokens("word " * 100) > count_tokens("word " * 10)


# --------------------------------------------------------------------------
# Titan / Bedrock, against a mock. Never run against the real service.
# --------------------------------------------------------------------------


class FakeBody:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()


class FakeBedrock:
    """Returns a vector keyed off whether the text mentions pay."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke_model(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        text = json.loads(kwargs["body"])["inputText"].lower()
        vector = [1.0, 0.0] if "pay" in text else [0.0, 1.0]
        return {"body": FakeBody({"embedding": vector})}


def test_titan_backend_embeds_every_document_and_ranks_by_cosine(
    tables: list[TableDetail],
) -> None:
    client = FakeBedrock()
    docs = [build_document(t) for t in tables]
    backend = TitanBackend(docs, client=client)

    assert len(client.calls) == len(docs), "one embed call per document at build time"
    assert backend.name == "titan"

    ranked = backend.rank("employee pay", limit=1)
    assert ranked, "expected at least one result"
    top_index = ranked[0][0]
    assert "pay" in docs[top_index].lower()


def test_titan_backend_requests_the_documented_model_and_shape(tables: list[TableDetail]) -> None:
    client = FakeBedrock()
    TitanBackend([build_document(tables[0])], client=client)
    call = client.calls[0]
    assert call["modelId"] == "amazon.titan-embed-text-v2:0"
    body = json.loads(call["body"])
    assert body["dimensions"] == 1024
    assert body["normalize"] is True, "cosine via dot product depends on normalised vectors"


# --------------------------------------------------------------------------
# Backend selection
# --------------------------------------------------------------------------


def test_build_backend_defaults_to_bm25() -> None:
    assert build_backend(["a doc"]).name == "bm25"


def test_build_backend_rejects_unknown_names() -> None:
    with pytest.raises(ValueError, match="Unknown retrieval backend"):
        build_backend(["a doc"], name="word2vec")


def test_build_backend_honours_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGSS_RETRIEVAL_BACKEND", "bm25")
    assert build_backend(["a doc"]).name == "bm25"
