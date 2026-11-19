"""Retrieval backend selection: BM25 from the core, or Titan embeddings via Bedrock.

Titan makes network calls, so it lives here and not in core/retrieval.py.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any, Final

from ..core.retrieval import (
    DEFAULT_NEIGHBOUR_WEIGHT,
    BM25Backend,
    RetrievalBackend,
    SchemaIndex,
)
from ..core.types import TableDetail

__all__ = ["TITAN_MODEL_ID", "TitanBackend", "build_backend", "make_index"]

TITAN_MODEL_ID: Final = "amazon.titan-embed-text-v2:0"


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

        return boto3.client(
            "bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )

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


def make_index(
    tables: Sequence[TableDetail],
    backend: str | None = None,
    neighbour_weight: float = DEFAULT_NEIGHBOUR_WEIGHT,
) -> SchemaIndex:
    """Build the index with the backend named by `backend` or PGSS_RETRIEVAL_BACKEND."""
    return SchemaIndex(
        tables,
        backend_factory=lambda documents: build_backend(documents, backend),
        neighbour_weight=neighbour_weight,
    )
