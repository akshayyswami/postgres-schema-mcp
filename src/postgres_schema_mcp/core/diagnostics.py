"""Plain-language explanations for set-up problems. No I/O."""

from __future__ import annotations

from collections.abc import Collection, Sequence

__all__ = ["empty_index_message", "missing_schemas_warning"]

_EXAMPLE_SCHEMAS = 3


def empty_index_message(configured: Sequence[str], readable: Sequence[str]) -> str:
    """Why the index is empty, and what to set.

    `configured` is what PGSS_SCHEMAS (or its default) asked for. `readable` is every other
    schema on the database that holds a table this role can read, so the message can name
    a value that would work.
    """
    where = ", ".join(configured) or "none"
    if readable:
        example = ",".join(readable[:_EXAMPLE_SCHEMAS])
        return (
            f"No tables were found in the indexed schemas ({where}). "
            f"Schemas on this database with tables this role can read: {', '.join(readable)}. "
            "Set PGSS_SCHEMAS to a comma-separated list of the schemas to index, for example "
            f"PGSS_SCHEMAS={example}, then restart the server."
        )
    return (
        f"No tables were found in the indexed schemas ({where}), and this role can read no "
        "tables in any other schema. Grant it USAGE on the schema and SELECT on its tables, "
        "then set PGSS_SCHEMAS to the schemas that hold them."
    )


def missing_schemas_warning(configured: Sequence[str], found: Collection[str]) -> str | None:
    """A warning when some configured schemas held no readable tables, else None."""
    missing = [s for s in configured if s not in found]
    if not missing:
        return None
    return (
        f"PGSS_SCHEMAS names schemas with no readable tables: {', '.join(missing)}. "
        "Check the spelling and the role's grants."
    )
