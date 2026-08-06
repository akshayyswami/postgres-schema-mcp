#!/usr/bin/env python
"""Measure what retrieval actually saves. This produces the README's headline number.

Compares two things:

  * the token cost of dumping the whole schema's DDL in one blob, which is what
    a list_tables/get_schema server sends;
  * the mean token cost of the slice search_schema returns, across every
    question in questions.yaml.

Nothing here is estimated or assumed. The counts come from a real index built
against the seeded database, and the output names the tokenizer that produced
them, because a token count without a tokenizer is not a measurement.

Usage:
    python evals/measure_context.py [-k 8]
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pg_schema_scout.db import Database
from pg_schema_scout.retrieval import SchemaIndex, count_tokens, tokenizer_name

EVALS = Path(__file__).resolve().parent


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-k", type=int, default=8, help="tables per slice (search_schema limit)")
    args = parser.parse_args()

    async with Database() as db:
        index = SchemaIndex(await db.introspect())

    questions = yaml.safe_load((EVALS / "questions.yaml").read_text())["questions"]

    full_tokens = index.full_schema_tokens()
    slice_tokens = [
        count_tokens("\n\n".join(t.ddl for t, _ in index.search(q["question"], args.k))) for q in questions
    ]

    mean_slice = statistics.mean(slice_tokens)
    median_slice = statistics.median(slice_tokens)

    print("Schema context measurement")
    print(f"  tokenizer            : {tokenizer_name()}")
    print(f"  retrieval backend    : {index.backend_name}")
    print(f"  tables in schema     : {len(index.tables)}")
    print(f"  questions measured   : {len(questions)}")
    print(f"  slice size (k)       : {args.k}")
    print()
    print(f"  full schema DDL      : {full_tokens:,} tokens")
    print(f"  retrieved slice, mean: {mean_slice:,.0f} tokens")
    print(f"  retrieved slice, med : {median_slice:,.0f} tokens")
    print(f"  min / max slice      : {min(slice_tokens):,} / {max(slice_tokens):,} tokens")
    print()
    print(
        f"  reduction            : {full_tokens / mean_slice:.1f}x  "
        f"({100 * (1 - mean_slice / full_tokens):.0f}% fewer tokens)"
    )
    print()
    print("  Note: this measures schema context only -- the DDL a model is given before")
    print("  it writes SQL. It excludes tool descriptions and the rows a query returns.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
