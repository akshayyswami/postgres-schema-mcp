"""Freeze the seeded database's catalog into evals/fixtures/catalog.json.

Every number in the README comes from the schema of the seeded AdventureWorks
database: table and column names, comments, keys, and the DDL rendered from them.
That is metadata only, no rows. Freezing it lets `make check` measure those numbers
without a running Postgres, and lets CI compare the frozen copy against a freshly
seeded database, so the fixture cannot drift from what the Quickstart builds.

Two things are normalised so the file is stable across machines:

* tables are sorted by qualified name in Python, not by the database's collation;
* `row_estimate` is dropped, because it depends on when the planner last analysed.

Usage:
    uv run python evals/capture_catalog.py            # rewrite the fixture
    uv run python evals/capture_catalog.py --check    # exit 1 if it differs from the database
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import sys
from pathlib import Path

from postgres_schema_mcp.adapters.db import Database

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "catalog.json"


async def capture() -> str:
    async with Database() as db:
        tables = await db.introspect()
    tables.sort(key=lambda t: t.qualified_name)
    payload = []
    for table in tables:
        table.row_estimate = None
        payload.append(table.model_dump(mode="json"))
    return json.dumps(payload, indent=1, ensure_ascii=False) + "\n"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="compare, do not write")
    args = parser.parse_args()

    fresh = await capture()
    if not args.check:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(fresh)
        print(f"wrote {FIXTURE} ({len(json.loads(fresh))} tables)")
        return 0

    committed = FIXTURE.read_text() if FIXTURE.exists() else ""
    if committed == fresh:
        print(f"catalog fixture matches the database ({len(json.loads(fresh))} tables)")
        return 0
    diff = difflib.unified_diff(
        committed.splitlines(), fresh.splitlines(), "committed", "database", lineterm="", n=1
    )
    print("catalog fixture differs from the database:", file=sys.stderr)
    for line in list(diff)[:40]:
        print(line, file=sys.stderr)
    print("run `make fixtures` and review the diff", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
