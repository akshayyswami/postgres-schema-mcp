"""Shared pytest fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from postgres_schema_mcp.core.types import TableDetail

CATALOG_PATH = Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "catalog.json"


@pytest.fixture(scope="session")
def catalog() -> list[TableDetail]:
    """The seeded AdventureWorks catalog (68 tables), frozen by evals/capture_catalog.py."""
    return [TableDetail.model_validate(item) for item in json.loads(CATALOG_PATH.read_text())]
