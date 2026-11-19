"""Planted-fault checks that need the seeded Postgres.

measure.py proves the guard blocks every bad query and allows every clean one, offline.
This adds the half that cannot be offline: every query in `must_allow` is also
EXECUTED against the database, so "allowed" means "allowed and actually runs", not
merely "parsed". Together with capture_catalog.py --check, which proves the frozen
catalog still matches what the Quickstart builds, it is `make evals-db`.

Exits non-zero on any failure.

Usage:
    uv run python evals/check_live.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import yaml

from postgres_schema_mcp.adapters.db import Database

EVALS = Path(__file__).resolve().parent


async def main() -> int:
    faults = yaml.safe_load((EVALS / "faults.yaml").read_text())
    failures: list[str] = []
    executed = 0
    async with Database() as db:
        for case in faults["must_allow"]:
            try:
                await db.fetch_rows(case["sql"], max_rows=5)
            except Exception as exc:
                failures.append(f"must_allow/{case['id']}: allowed but failed to execute: {exc}")
            else:
                executed += 1

    total = len(faults["must_allow"])
    print(f"must_allow: {executed}/{total} executed against the database")
    for line in failures:
        print(f"FAIL {line}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
