"""Print ONE JSON object holding every number the README quotes.

Offline and deterministic: the schema comes from evals/fixtures/catalog.json (frozen
from the seeded database by capture_catalog.py), the questions and planted faults
from the YAML files beside it, and the BM25 ranking from this repo's own code. No
database, no network, no model call, no timestamps. The one recorded external
measurement, Titan embeddings on Bedrock, is read from evals/baseline/ and carries
its model id and date.

Exit code is non-zero if a planted fault is missed, a clean query is wrongly
blocked, a retrieval figure falls below its floor, or the token counts did not come
from the real tokenizer. The JSON is still printed so the failure can be inspected.

Usage:
    uv run python evals/measure.py > evals/results/latest.json     # or: make evals
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from postgres_schema_mcp.core.guard import BlockedQueryError, check_read_only
from postgres_schema_mcp.core.retrieval import (
    DEFAULT_NEIGHBOUR_WEIGHT,
    SchemaIndex,
    count_tokens,
    tokenizer_name,
)
from postgres_schema_mcp.core.types import TableDetail

EVALS = Path(__file__).resolve().parent
K = 8
SWEEP_WEIGHTS = (0.0, 0.25, DEFAULT_NEIGHBOUR_WEIGHT, 0.5)
REAL_TOKENIZER = "tiktoken/cl100k_base"
DIGITS = 4  # rounding keeps last-digit float noise from failing the diff on another platform

# Floors, set just below the measured figures so a real drop trips CI and ordinary
# scoring noise does not. They are floors, not targets: raise them when the numbers
# improve. The fault matrix has no floor, it must be perfect.
FLOORS = {
    "retrieval.recall_at_k": 0.92,
    "retrieval.precision_at_k": 0.24,
    "retrieval.complete_fraction": 0.85,
}


def _load_yaml(name: str) -> Any:
    return yaml.safe_load((EVALS / name).read_text())


def _load_catalog() -> list[TableDetail]:
    raw = json.loads((EVALS / "fixtures" / "catalog.json").read_text())
    return [TableDetail.model_validate(item) for item in raw]


def _score(
    index: SchemaIndex, questions: list[dict[str, Any]], k: int
) -> tuple[dict[str, Any], list[str]]:
    """Recall, precision and slice size over every question, plus any bad eval data."""
    known = {t.qualified_name.lower() for t in index.tables}
    recalls: list[float] = []
    precisions: list[float] = []
    slice_tokens: list[int] = []
    incomplete: list[str] = []
    missed: Counter[str] = Counter()
    problems: list[str] = []
    for q in questions:
        expected = {t.lower() for t in q["tables"]}
        if expected - known:
            problems.append(f"questions/{q['id']} names tables that do not exist")
            continue
        found = index.search(q["question"], k)
        got = {t.qualified_name.lower() for t, _ in found}
        hits = expected & got
        recalls.append(len(hits) / len(expected))
        precisions.append(len(hits) / k)
        slice_tokens.append(count_tokens("\n\n".join(t.ddl for t, _ in found)))
        if hits != expected:
            incomplete.append(q["id"])
            missed.update(expected - hits)
    n = len(recalls)
    result = {
        "questions": n,
        "recall_at_k": round(sum(recalls) / n, DIGITS),
        "precision_at_k": round(sum(precisions) / n, DIGITS),
        "complete": n - len(incomplete),
        "complete_fraction": round((n - len(incomplete)) / n, DIGITS),
        "incomplete": len(incomplete),
        "incomplete_questions": sorted(incomplete),
        # Most-missed tables first, ties broken by name so the output never depends on set order.
        "missed": [
            {"table": t, "count": c}
            for t, c in sorted(missed.items(), key=lambda item: (-item[1], item[0]))[:3]
        ],
        "slice_tokens_mean": round(statistics.mean(slice_tokens), DIGITS),
        "slice_tokens_median": statistics.median(slice_tokens),
        "slice_tokens_min": min(slice_tokens),
        "slice_tokens_max": max(slice_tokens),
    }
    return result, problems


def _fault_matrix() -> tuple[dict[str, int], list[str]]:
    faults = _load_yaml("faults.yaml")
    problems: list[str] = []
    blocked = 0
    for case in faults["must_block"]:
        try:
            check_read_only(case["sql"])
        except BlockedQueryError:
            blocked += 1
        else:
            problems.append(f"must_block/{case['id']} was not blocked")
    allowed = 0
    for case in faults["must_allow"]:
        try:
            check_read_only(case["sql"])
        except BlockedQueryError as exc:
            problems.append(f"must_allow/{case['id']} was wrongly blocked: {exc}")
        else:
            allowed += 1
    counts = {
        "must_block_total": len(faults["must_block"]),
        "must_block_caught": blocked,
        "must_allow_total": len(faults["must_allow"]),
        "must_allow_passed": allowed,
    }
    return counts, problems


def measure() -> tuple[dict[str, Any], list[str]]:
    """Return every figure the README quotes, and a message for each thing that is wrong."""
    problems: list[str] = []
    tokenizer = tokenizer_name()
    if tokenizer != REAL_TOKENIZER:
        problems.append(
            f"token counts came from {tokenizer!r}, an approximation, not {REAL_TOKENIZER}"
        )

    tables = _load_catalog()
    questions = _load_yaml("questions.yaml")["questions"]
    index = SchemaIndex(tables)

    retrieval, bad_questions = _score(index, questions, K)
    problems += bad_questions

    full_tokens = index.full_schema_tokens()
    mean_slice = retrieval["slice_tokens_mean"]
    context = {
        "tokenizer": tokenizer,
        "backend": index.backend_name,
        "tables": len(tables),
        "schemas": len({t.schema_name for t in tables}),
        "questions": len(questions),
        "k": K,
        "full_schema_tokens": full_tokens,
        "reduction_x": round(full_tokens / mean_slice, DIGITS),
        "fewer_tokens": round(1 - mean_slice / full_tokens, DIGITS),
    }

    sweep = []
    for weight in SWEEP_WEIGHTS:
        swept, _ = _score(SchemaIndex(tables, neighbour_weight=weight), questions, K)
        sweep.append(
            {
                "neighbour_weight": weight,
                "recall_at_k": swept["recall_at_k"],
                "precision_at_k": swept["precision_at_k"],
                "complete": swept["complete"],
                "incomplete": swept["incomplete"],
                "missed": swept["missed"],
                "slice_tokens_mean": swept["slice_tokens_mean"],
                "reduction_x": round(full_tokens / swept["slice_tokens_mean"], DIGITS),
            }
        )

    faults, fault_problems = _fault_matrix()
    problems += fault_problems

    results: dict[str, Any] = {
        "context": context,
        "retrieval": retrieval,
        "fk_sweep": sweep,
        "faults": faults,
        "baseline": {
            "titan": json.loads((EVALS / "baseline" / "titan-bedrock.json").read_text()),
            "conformance": json.loads((EVALS / "baseline" / "conformance.json").read_text()),
        },
    }
    for key, floor in FLOORS.items():
        node: Any = results
        for part in key.split("."):
            node = node[part]
        if node < floor:
            problems.append(f"{key}={node} is below its floor {floor}")
    return results, problems


def main() -> int:
    results, problems = measure()
    for problem in problems:
        print(f"evals: {problem}", file=sys.stderr)
    print(json.dumps(results, indent=2, sort_keys=True))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
