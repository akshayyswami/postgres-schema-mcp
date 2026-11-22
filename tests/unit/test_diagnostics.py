"""The plain-language set-up messages. Pure functions, so no database and no server."""

from __future__ import annotations

from postgres_schema_mcp.core.diagnostics import empty_index_message, missing_schemas_warning


def test_empty_index_names_the_configured_schemas_and_a_value_that_would_work() -> None:
    message = empty_index_message(["person", "sales"], ["billing", "public"])
    assert "(person, sales)" in message
    assert "billing, public" in message
    assert "PGSS_SCHEMAS=billing,public" in message
    assert "restart" in message


def test_the_example_value_is_capped_at_three_schemas() -> None:
    message = empty_index_message(["person"], ["a", "b", "c", "d", "e"])
    assert "PGSS_SCHEMAS=a,b,c," in message
    assert "a, b, c, d, e" in message  # the full list is still shown in words


def test_empty_index_with_nothing_readable_says_to_grant_access() -> None:
    message = empty_index_message(["public"], [])
    assert "can read no tables in any other schema" in message
    assert "USAGE" in message
    assert "PGSS_SCHEMAS=" not in message  # there is no value to suggest


def test_no_configured_schemas_is_still_a_readable_sentence() -> None:
    assert "(none)" in empty_index_message([], ["public"])


def test_a_warning_lists_only_the_schemas_that_held_nothing() -> None:
    warning = missing_schemas_warning(["public", "billing", "slaes"], {"public", "billing"})
    assert warning is not None
    assert "slaes" in warning
    assert "public" not in warning
    assert "billing" not in warning


def test_no_warning_when_every_configured_schema_has_tables() -> None:
    assert missing_schemas_warning(["public", "billing"], {"public", "billing", "extra"}) is None
