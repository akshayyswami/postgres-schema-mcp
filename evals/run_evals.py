#!/usr/bin/env python
"""Regression gate: the fault matrix and retrieval quality.

Exits non-zero on any regression, so CI fails rather than warns.

The fault matrix is two-sided. Blocking everything would pass the must_block
half and fail the must_allow half, and every must_allow case is *executed*
against the seeded database, not merely parsed: "allowed" has to mean the query
actually runs.

Usage:
    python evals/run_evals.py              # everything (needs the database)
    python evals/run_evals.py --no-db      # guard + retrieval only, no execution
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from postgres_schema_mcp.adapters.backends import make_index
from postgres_schema_mcp.adapters.db import Database
from postgres_schema_mcp.core.guard import BlockedQueryError, check_read_only

EVALS = Path(__file__).resolve().parent

# Thresholds. The fault matrix must be perfect: a single miss in either
# direction is a regression, not a degradation.
#
# The retrieval floors are set just below the figures measured on the seeded
# database, so a real drop trips CI while ordinary scoring noise does not. They
# are floors, not targets; raise them when the measured numbers improve.
MIN_RECALL_AT_K = 0.92
MIN_PRECISION_AT_K = 0.24
MIN_FULL_RECALL_FRACTION = 0.85
DEFAULT_K = 8


def load(name: str) -> Any:
    return yaml.safe_load((EVALS / name).read_text())


# --------------------------------------------------------------------------
# Fault matrix
# --------------------------------------------------------------------------


async def run_fault_matrix(db: Database | None) -> tuple[bool, list[str]]:
    faults = load("faults.yaml")
    failures: list[str] = []

    blocked = 0
    for case in faults["must_block"]:
        try:
            check_read_only(case["sql"])
        except BlockedQueryError:
            blocked += 1
        else:
            failures.append(f"must_block/{case['id']}: NOT blocked ({case.get('reason', '')})")

    allowed = 0
    executed = 0
    for case in faults["must_allow"]:
        try:
            check_read_only(case["sql"])
            allowed += 1
        except BlockedQueryError as exc:
            failures.append(f"must_allow/{case['id']}: wrongly blocked -- {exc}")
            continue

        if db is None:
            continue
        try:
            await db.fetch_rows(case["sql"], max_rows=5)
            executed += 1
        except Exception as exc:
            failures.append(f"must_allow/{case['id']}: allowed but failed to execute -- {exc}")

    total_block = len(faults["must_block"])
    total_allow = len(faults["must_allow"])
    print("Fault matrix")
    print(f"  must_block : {blocked}/{total_block} blocked")
    print(f"  must_allow : {allowed}/{total_allow} allowed", end="")
    print(
        f", {executed}/{total_allow} executed against the database"
        if db
        else " (execution skipped)"
    )

    return not failures, failures


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------


async def run_retrieval(db: Database, k: int) -> tuple[bool, list[str]]:
    index = make_index(await db.introspect())
    questions = load("questions.yaml")["questions"]
    failures: list[str] = []

    recalls: list[float] = []
    precisions: list[float] = []
    complete = 0
    misses: list[str] = []

    for q in questions:
        expected = {t.lower() for t in q["tables"]}
        unknown = expected - {t.qualified_name.lower() for t in index.tables}
        if unknown:
            failures.append(
                f"questions/{q['id']}: names tables that do not exist: {sorted(unknown)}"
            )
            continue

        got = [t.qualified_name.lower() for t, _ in index.search(q["question"], k)]
        hits = expected & set(got)
        recalls.append(len(hits) / len(expected))
        precisions.append(len(hits) / k)
        if hits == expected:
            complete += 1
        else:
            misses.append(f"    {q['id']}: missed {sorted(expected - hits)}")

    n = len(recalls)
    mean_recall = sum(recalls) / n if n else 0.0
    mean_precision = sum(precisions) / n if n else 0.0
    full_fraction = complete / n if n else 0.0

    print(f"\nRetrieval ({index.backend_name}, k={k}, {n} questions)")
    print(f"  recall@{k}          : {mean_recall:.3f}   (floor {MIN_RECALL_AT_K})")
    print(f"  precision@{k}       : {mean_precision:.3f}   (floor {MIN_PRECISION_AT_K})")
    print(
        f"  full-recall queries : {complete}/{n} = {full_fraction:.3f}   "
        f"(floor {MIN_FULL_RECALL_FRACTION})"
    )
    print(
        f"  note: precision@{k} is structurally capped, since a question needs "
        f"1-4 tables but k={k} slots are returned."
    )
    if misses:
        print("  questions missing at least one expected table:")
        for line in misses:
            print(line)

    if mean_recall < MIN_RECALL_AT_K:
        failures.append(f"recall@{k} {mean_recall:.3f} below floor {MIN_RECALL_AT_K}")
    if mean_precision < MIN_PRECISION_AT_K:
        failures.append(f"precision@{k} {mean_precision:.3f} below floor {MIN_PRECISION_AT_K}")
    if full_fraction < MIN_FULL_RECALL_FRACTION:
        failures.append(
            f"full-recall fraction {full_fraction:.3f} below floor {MIN_FULL_RECALL_FRACTION}"
        )
    return not failures, failures


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-db", action="store_true", help="skip anything needing Postgres")
    parser.add_argument("-k", type=int, default=DEFAULT_K, help="retrieval cutoff")
    args = parser.parse_args()

    failures: list[str] = []

    if args.no_db:
        _, f = await run_fault_matrix(None)
        failures += f
        print("\nRetrieval: skipped (--no-db)")
    else:
        async with Database() as db:
            _, f = await run_fault_matrix(db)
            failures += f
            _, f = await run_retrieval(db, args.k)
            failures += f

    print()
    if failures:
        print(f"FAIL: {len(failures)} regression(s)")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("PASS: fault matrix clean, retrieval above every floor")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
