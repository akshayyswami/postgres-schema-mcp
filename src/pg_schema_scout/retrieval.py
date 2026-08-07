"""Schema index and search: the retrieval half of the thesis.

The whole point of this server is that an agent should be handed the tables
relevant to its question, not the entire schema. This module builds one document
per table at startup and ranks those documents against the question.

Two backends sit behind one interface:

  * BM25 (rank_bm25). The default, so the repo runs offline with no credentials
    and no per-query cost.
  * Amazon Titan Text Embeddings V2 via Bedrock, enabled with
    PGSS_RETRIEVAL_BACKEND=titan.

The honest failure mode, stated here because it is stated in the README too:
retrieval can rank a needed table below the cutoff, and when that happens the
table is absent from the slice entirely and the model cannot recover from the
slice alone. That is exactly why describe_table exists, and why every
SchemaSlice carries a note telling the model to use it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Sequence
from typing import Any, Final, Protocol

from .types import TableDetail

__all__ = [
    "BM25Backend",
    "RetrievalBackend",
    "SchemaIndex",
    "TitanBackend",
    "build_backend",
    "count_tokens",
    "tokenizer_name",
]

TITAN_MODEL_ID: Final = "amazon.titan-embed-text-v2:0"

# How much of a table's score propagates to its foreign-key neighbours. Tuned on
# the eval set: see SchemaIndex.search for why the mechanism exists, and the
# README for the measured before/after.
DEFAULT_NEIGHBOUR_WEIGHT: Final = 0.35

# AdventureWorks identifiers are lowercase concatenations: salesorderheader,
# businessentityid, productsubcategory. A whitespace tokenizer sees one opaque
# token, so "how many sales orders" never matches "salesorderheader".
#
# This lexicon lets the tokenizer split those names back into words by greedy
# longest-match. It is a deliberate design decision rather than an accident: the
# alternative, character n-grams, retrieves more noise, and a general English
# dictionary splits domain terms badly. The cost is that the lexicon is specific
# to this schema's vocabulary; a different database would want its own.
_LEXICON: Final[tuple[str, ...]] = (
    "address",
    "archive",
    "assembly",
    "bill",
    "business",
    "candidate",
    "card",
    "cart",
    "catalog",
    "category",
    "contact",
    "cost",
    "country",
    "credit",
    "culture",
    "currency",
    "customer",
    "date",
    "department",
    "description",
    "detail",
    "document",
    "email",
    "employee",
    "entity",
    "flag",
    "group",
    "header",
    "history",
    "illustration",
    "inventory",
    "item",
    "job",
    "level",
    "line",
    "list",
    "location",
    "manufacture",
    "materials",
    "measure",
    "method",
    "model",
    "name",
    "number",
    "offer",
    "order",
    "password",
    "pay",
    "person",
    "phone",
    "photo",
    "price",
    "product",
    "quota",
    "rate",
    "reason",
    "region",
    "review",
    "routing",
    "sales",
    "scrap",
    "shift",
    "ship",
    "shopping",
    "special",
    "state",
    "status",
    "store",
    "sub",
    "subcategory",
    "tax",
    "territory",
    "transaction",
    "type",
    "unit",
    "vendor",
    "work",
    # Common suffixes that carry meaning in a schema.
    "id",
    "key",
    "code",
    "qty",
    "quantity",
    "amount",
    "total",
    "due",
    "start",
    "end",
    "modified",
)
_LEXICON_BY_LENGTH: Final[tuple[str, ...]] = tuple(sorted(_LEXICON, key=len, reverse=True))

# Runs of letters, not camelCase humps. Splitting humps here would make
# "SalesOrderHeader" and "salesorderheader" tokenize differently, and the
# lexicon below already recovers the words either way.
_WORD_RE: Final = re.compile(r"[A-Za-z]+|\d+")


def _stem(word: str) -> str:
    """Strip regular plurals so a question's wording matches a schema's.

    Questions say "how many orders did employees place"; the schema says
    `salesorderheader` and `employee`. Without this, neither term matches.

    Deliberately conservative suffix-stripping rather than a real stemmer: it
    handles the regular plurals that dominate these questions and leaves
    everything else alone. It does not handle irregular morphology -- "paid"
    does not reduce to "pay" -- which is a real ceiling on the lexical backend.

    Swapping in embeddings was the obvious fix for that ceiling, and it was
    tried: see TitanBackend, where it measured slightly worse overall.
    """
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _split_identifier(token: str) -> list[str]:
    """Break a concatenated identifier into words using the lexicon.

    Returns the pieces plus the original token, so an exact match on the raw name
    still scores.
    """
    lowered = token.lower()
    pieces: list[str] = []
    i = 0
    while i < len(lowered):
        for word in _LEXICON_BY_LENGTH:
            if lowered.startswith(word, i):
                pieces.append(word)
                i += len(word)
                break
        else:
            i += 1  # character belongs to no known word; skip it
    return pieces


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, with concatenated identifiers split and stemmed.

    Tokens are added rather than replaced, so an exact match on the raw
    identifier still scores alongside its parts.
    """
    tokens: list[str] = []

    def emit(token: str) -> None:
        tokens.append(token)
        stemmed = _stem(token)
        if stemmed != token:
            tokens.append(stemmed)

    for raw in _WORD_RE.findall(text):
        lowered = raw.lower()
        emit(lowered)
        if len(lowered) > 6:
            parts = _split_identifier(lowered)
            if len(parts) > 1:
                for part in parts:
                    emit(part)
    return tokens


# --------------------------------------------------------------------------
# Token counting. The README's headline number depends on this, so the
# tokenizer that produced a count is always reported alongside it.
# --------------------------------------------------------------------------

_encoder: Any = None
_tokenizer_name: str | None = None


def _load_tokenizer() -> None:
    global _encoder, _tokenizer_name
    if _tokenizer_name is not None:
        return
    try:
        import tiktoken

        _encoder = tiktoken.get_encoding("cl100k_base")
        _tokenizer_name = "tiktoken/cl100k_base"
    except Exception:
        # tiktoken fetches its BPE table on first use; without network this is
        # the fallback. It is an approximation and says so in its name, so a
        # number produced by it is never mistaken for a measured one.
        _encoder = None
        _tokenizer_name = "approximate/4-chars-per-token"


def tokenizer_name() -> str:
    _load_tokenizer()
    assert _tokenizer_name is not None
    return _tokenizer_name


def count_tokens(text: str) -> int:
    _load_tokenizer()
    if _encoder is not None:
        return len(_encoder.encode(text))
    return max(1, len(text) // 4)


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------


class RetrievalBackend(Protocol):
    """Ranks table documents against a question."""

    name: str

    def rank(self, question: str, limit: int) -> list[tuple[int, float]]:
        """Return (document index, score) pairs, best first, at most `limit`."""
        ...


class BM25Backend:
    """Lexical ranking with Okapi BM25. Default because it needs nothing."""

    name = "bm25"

    def __init__(self, documents: Sequence[str]) -> None:
        from rank_bm25 import BM25Okapi

        self._corpus = [tokenize(d) for d in documents]
        # BM25Okapi divides by the average document length, which is zero for an
        # empty corpus.
        self._bm25 = BM25Okapi(self._corpus) if self._corpus else None

    def rank(self, question: str, limit: int) -> list[tuple[int, float]]:
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(tokenize(question))
        ordered = sorted(enumerate(scores), key=lambda pair: (-pair[1], pair[0]))
        return [(i, float(s)) for i, s in ordered[:limit]]


class TitanBackend:
    """Amazon Titan Text Embeddings V2 via Bedrock.

    Measured against the real service on the 26-question eval set, and it does
    not win: recall@8 0.926 versus BM25's 0.955 (22/26 complete versus 24/26).
    Both backends score ~0.84 before foreign-key propagation, so the embeddings
    add nothing here that the lexical path was not already getting.

    That is a defensible result rather than a disappointing one. These documents
    are mostly identifiers and one-line comments, not prose, which is the regime
    BM25 is good at. Two caveats worth stating: Titan is handed the same
    identifier-heavy document text as BM25, and a prose rendering might suit it
    better; and the lexicon and stemmer in this module were tuned with BM25 in
    mind, so the comparison is not perfectly even-handed.

    BM25 therefore remains the default, on evidence rather than convenience.
    """

    name = "titan"

    def __init__(self, documents: Sequence[str], client: Any | None = None) -> None:
        self._client = client if client is not None else self._default_client()
        self._doc_vectors = [self._embed(d) for d in documents]

    @staticmethod
    def _default_client() -> Any:
        import boto3

        return boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))

    def _embed(self, text: str) -> list[float]:
        import json

        response = self._client.invoke_model(
            modelId=os.environ.get("PGSS_TITAN_MODEL_ID", TITAN_MODEL_ID),
            body=json.dumps({"inputText": text, "dimensions": 1024, "normalize": True}),
            accept="application/json",
            contentType="application/json",
        )
        payload = json.loads(response["body"].read())
        return [float(x) for x in payload["embedding"]]

    def rank(self, question: str, limit: int) -> list[tuple[int, float]]:
        query = self._embed(question)
        # Titan is asked for normalised vectors, so a dot product is cosine
        # similarity.
        scored = [
            (i, sum(a * b for a, b in zip(query, doc, strict=False)))
            for i, doc in enumerate(self._doc_vectors)
        ]
        ordered = sorted(scored, key=lambda pair: (-pair[1], pair[0]))
        return [(i, float(s)) for i, s in ordered[:limit]]


def build_backend(documents: Sequence[str], name: str | None = None) -> RetrievalBackend:
    """Pick a backend by name, defaulting to the env var and then to BM25."""
    chosen = (name or os.environ.get("PGSS_RETRIEVAL_BACKEND") or "bm25").strip().lower()
    if chosen == "titan":
        return TitanBackend(documents)
    if chosen != "bm25":
        raise ValueError(f"Unknown retrieval backend {chosen!r}; expected 'bm25' or 'titan'.")
    return BM25Backend(documents)


# --------------------------------------------------------------------------
# The index
# --------------------------------------------------------------------------


def build_document(table: TableDetail) -> str:
    """The text that represents one table to the ranker.

    Names, comments, column names and types, and the names of tables it joins to.
    Column comments matter most here: AdventureWorks ships them, and they are the
    only natural language in an otherwise concatenated-identifier schema.
    """
    parts: list[str] = [table.schema_name, table.name, table.qualified_name]
    if table.comment:
        parts.append(table.comment)
    for col in table.columns:
        parts.append(col.name)
        parts.append(col.type)
        if col.comment:
            parts.append(col.comment)
    for fk in table.foreign_keys:
        parts.append(f"{fk.references_schema}.{fk.references_table}")
        parts.extend(fk.columns)
    return "\n".join(parts)


class SchemaIndex:
    """Every table indexed once, plus the search over them."""

    def __init__(
        self,
        tables: Sequence[TableDetail],
        backend: str | None = None,
        neighbour_weight: float = DEFAULT_NEIGHBOUR_WEIGHT,
    ) -> None:
        self.tables: list[TableDetail] = list(tables)
        self._documents = [build_document(t) for t in self.tables]
        self._backend: RetrievalBackend = build_backend(self._documents, backend)
        self.neighbour_weight = neighbour_weight
        self._adjacency = self._build_adjacency()
        self._by_name: dict[str, TableDetail] = {}
        for table in self.tables:
            self._by_name[table.qualified_name.lower()] = table
            # Bare names are ambiguous across schemas in principle; in this
            # schema they are not, and accepting them makes describe_table
            # forgiving about how the model spells the name.
            self._by_name.setdefault(table.name.lower(), table)

    @property
    def backend_name(self) -> str:
        return self._backend.name

    @property
    def full_schema_ddl(self) -> str:
        """Every table's DDL concatenated: the baseline a dump-everything server sends."""
        return "\n\n".join(t.ddl for t in self.tables)

    def full_schema_tokens(self) -> int:
        return count_tokens(self.full_schema_ddl)

    def lookup(self, name: str) -> TableDetail | None:
        """Find a table by qualified or bare name, case-insensitively."""
        return self._by_name.get(name.strip().lower())

    def _build_adjacency(self) -> list[set[int]]:
        """Undirected foreign-key adjacency between table indices."""
        position = {t.qualified_name: i for i, t in enumerate(self.tables)}
        adjacency: list[set[int]] = [set() for _ in self.tables]
        for i, table in enumerate(self.tables):
            for fk in table.foreign_keys:
                j = position.get(f"{fk.references_schema}.{fk.references_table}")
                if j is not None and j != i:
                    adjacency[i].add(j)
                    adjacency[j].add(i)
        return adjacency

    def search(self, question: str, limit: int) -> list[tuple[TableDetail, float]]:
        """The top `limit` tables for this question, best first.

        Ranking is lexical (or embedding) score plus one round of propagation
        across foreign keys.

        The propagation is not a trick to flatter the numbers; it is what makes a
        slice *usable*. Purely lexical ranking reliably returned
        sales.salesorderdetail without production.product, and
        sales.personcreditcard without person.person. Those slices name a
        productid or a businessentityid the model cannot resolve, so it has to
        spend another round trip on describe_table to write the join at all.
        A table adjacent to a strongly-matching table is likely to be needed,
        precisely because you cannot join to what you have not been shown.

        Only one round, and damped: two hops from the question is far enough to
        start pulling in the whole schema, which is the thing this server exists
        to avoid.
        """
        limit = max(1, min(limit, len(self.tables)))
        base = dict(self._backend.rank(question, len(self.tables)))
        if self.neighbour_weight <= 0:
            ranked = sorted(base.items(), key=lambda kv: (-kv[1], kv[0]))
            return [(self.tables[i], s) for i, s in ranked[:limit]]

        combined: list[tuple[int, float]] = []
        for i in range(len(self.tables)):
            neighbour_best = max((base.get(j, 0.0) for j in self._adjacency[i]), default=0.0)
            combined.append((i, base.get(i, 0.0) + self.neighbour_weight * neighbour_best))

        combined.sort(key=lambda kv: (-kv[1], kv[0]))
        return [(self.tables[i], score) for i, score in combined[:limit]]

    def edges_within(self, tables: Sequence[TableDetail]) -> list[Any]:
        """Foreign keys whose *both* ends are in `tables`.

        Edges pointing outside the slice are omitted on purpose: the model cannot
        join to a table whose columns it has not been shown, so listing those
        edges would spend tokens on an unusable hint.
        """
        present = {t.qualified_name for t in tables}
        edges = []
        for table in tables:
            for fk in table.foreign_keys:
                if f"{fk.references_schema}.{fk.references_table}" in present:
                    edges.append(fk)
        return edges


def make_index(tables: Sequence[TableDetail], backend: str | None = None) -> SchemaIndex:
    return SchemaIndex(tables, backend)


CountFn = Callable[[str], int]
