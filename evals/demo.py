"""Terminal demo for the README GIF: one retrieval and two guard decisions.

Offline, like measure.py: it reads the frozen catalog in fixtures/catalog.json and
needs no database. The figures it prints come from the same code the evals use.

    uv run python evals/demo.py
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from postgres_schema_mcp.core.guard import BlockedQueryError, check_read_only
from postgres_schema_mcp.core.retrieval import SchemaIndex, count_tokens
from postgres_schema_mcp.core.types import TableDetail

EVALS = Path(__file__).resolve().parent
QUESTION_ID = "employee-department-moves"
K = 8


def main() -> None:
    raw = json.loads((EVALS / "fixtures" / "catalog.json").read_text())
    index = SchemaIndex([TableDetail.model_validate(item) for item in raw])
    questions = yaml.safe_load((EVALS / "questions.yaml").read_text())["questions"]
    question = next(q for q in questions if q["id"] == QUESTION_ID)
    needed = {name.lower() for name in question["tables"]}

    found = index.search(question["question"], K)
    slice_tokens = count_tokens("\n\n".join(t.ddl for t, _ in found))
    print(f"Question: {question['question']}")
    dump_tokens = index.full_schema_tokens()
    print(f"Schema:   {len(index.tables)} tables, {dump_tokens:,} tokens as one dump")
    print(f"Slice:    {len(found)} tables, {slice_tokens:,} tokens\n")
    for table, _ in found:
        mark = "needed" if table.qualified_name.lower() in needed else ""
        print(f"  {table.qualified_name:<46} {mark}")

    faults = yaml.safe_load((EVALS / "faults.yaml").read_text())
    unsafe = next(f for f in faults["must_block"] if f["id"] == "dml-in-cte-delete")
    safe = faults["must_allow"][0]
    print("\nread-only guard")
    for label, sql in (("unsafe", unsafe["sql"]), ("safe", safe["sql"].strip())):
        try:
            check_read_only(sql)
            verdict = "allowed"
        except BlockedQueryError:
            verdict = "BLOCKED"
        print(f"  {verdict:<8} {label}: {' '.join(sql.split())[:70]}")


if __name__ == "__main__":
    main()
