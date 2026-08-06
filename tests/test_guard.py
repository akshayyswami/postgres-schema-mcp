"""Guard policy tests.

Written before guard.py exists. The fault matrix in evals/faults.yaml is the
contract; everything below it covers behaviour the matrix does not pin down,
mostly around failing closed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from pg_schema_scout.guard import BlockedQuery, check_read_only

FAULTS = yaml.safe_load((Path(__file__).resolve().parents[1] / "evals" / "faults.yaml").read_text())


def _cases(section: str) -> list[Any]:
    return [pytest.param(c["sql"], id=c["id"]) for c in FAULTS[section]]


# --------------------------------------------------------------------------
# The fault matrix itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize("sql", _cases("must_block"))
def test_must_block(sql: str) -> None:
    with pytest.raises(BlockedQuery):
        check_read_only(sql)


@pytest.mark.parametrize("sql", _cases("must_allow"))
def test_must_allow(sql: str) -> None:
    check_read_only(sql)


# --------------------------------------------------------------------------
# The message is part of the contract: it is read by a model, which has to be
# able to act on it.
# --------------------------------------------------------------------------


def test_blocked_message_names_the_offending_construct() -> None:
    sql = "WITH gone AS (DELETE FROM customer RETURNING *) SELECT * FROM gone;"
    with pytest.raises(BlockedQuery) as exc:
        check_read_only(sql)
    msg = str(exc.value)
    assert msg.startswith("Blocked:")
    assert "DELETE" in msg
    assert "read-only" in msg.lower()
    # It must tell the model what to do next, not merely that it failed.
    assert "rewrite" in msg.lower() or "select" in msg.lower()


def test_blocked_message_for_denylisted_function_names_the_function() -> None:
    with pytest.raises(BlockedQuery) as exc:
        check_read_only("SELECT pg_read_file('/etc/passwd');")
    assert "pg_read_file" in str(exc.value)


# --------------------------------------------------------------------------
# Fail closed. Anything the parser cannot vouch for is refused.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("", id="empty"),
        pytest.param("   \n\t  ", id="whitespace-only"),
        pytest.param("-- just a comment", id="comment-only"),
        pytest.param("/* nothing here */", id="block-comment-only"),
        pytest.param("SELECT FROM WHERE ((((", id="unparseable"),
        pytest.param("SELECT 1 FROM t WHERE ) bogus (", id="unparseable-2"),
    ],
)
def test_unparseable_or_empty_is_blocked(sql: str) -> None:
    with pytest.raises(BlockedQuery):
        check_read_only(sql)


def test_two_selects_is_still_blocked() -> None:
    """Only a single statement is allowed, even when both halves are harmless."""
    with pytest.raises(BlockedQuery) as exc:
        check_read_only("SELECT 1; SELECT 2;")
    assert "one statement" in str(exc.value).lower() or "single" in str(exc.value).lower()


def test_trailing_semicolon_is_fine() -> None:
    check_read_only("SELECT 1;")
    check_read_only("SELECT 1")
    check_read_only("SELECT 1;   \n  ")


# --------------------------------------------------------------------------
# Cases the matrix implies but does not spell out
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("dRoP tAbLe customer;", id="mixed-case-ddl"),
        pytest.param("SELECT * INTO evil FROM person.person;", id="select-into-creates-a-table"),
        pytest.param("TRUNCATE TABLE customer;", id="truncate"),
        pytest.param("ALTER TABLE customer ADD COLUMN x int;", id="alter"),
        pytest.param("CREATE INDEX ix ON customer (id);", id="create-index"),
        pytest.param("REVOKE SELECT ON customer FROM scout_ro;", id="revoke"),
        pytest.param("SELECT * FROM customer FOR NO KEY UPDATE;", id="for-no-key-update"),
        pytest.param("SELECT * FROM customer FOR SHARE;", id="for-share"),
        pytest.param("MERGE INTO a USING b ON a.id=b.id WHEN MATCHED THEN DELETE;", id="merge"),
        pytest.param(
            "WITH a AS (SELECT 1), b AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM a;",
            id="dml-in-second-cte",
        ),
        pytest.param(
            "WITH outer_cte AS (WITH inner_cte AS (DELETE FROM t RETURNING *) "
            "SELECT * FROM inner_cte) SELECT * FROM outer_cte;",
            id="dml-in-nested-cte",
        ),
        pytest.param(
            "SELECT * FROM person.person WHERE businessentityid IN "
            "(SELECT businessentityid FROM pg_ls_dir('/'));",
            id="denylisted-fn-in-subquery",
        ),
        pytest.param("SELECT pg_catalog.pg_read_file('/etc/passwd');", id="schema-qualified-denylisted-fn"),
        pytest.param("SELECT PG_READ_FILE('/etc/passwd');", id="uppercase-denylisted-fn"),
        pytest.param("SELECT lo_import('/etc/passwd');", id="large-object-import"),
        pytest.param("SELECT pg_sleep(30);", id="pg-sleep-denial-of-service"),
        pytest.param("COPY customer FROM '/etc/passwd';", id="copy-from-file"),
        pytest.param("SELECT 1; /* c */ DROP TABLE t;", id="stacking-separated-by-comment"),
    ],
)
def test_additional_blocked_shapes(sql: str) -> None:
    with pytest.raises(BlockedQuery):
        check_read_only(sql)


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("SELECT count(*) FROM person.person", id="aggregate"),
        pytest.param(
            "SELECT p.firstname FROM person.person p "
            "WHERE EXISTS (SELECT 1 FROM sales.customer c WHERE c.personid = p.businessentityid)",
            id="exists-subquery",
        ),
        pytest.param(
            "SELECT CASE WHEN listprice > 100 THEN 'high' ELSE 'low' END AS band, count(*) "
            "FROM production.product GROUP BY 1",
            id="case-expression",
        ),
        pytest.param(
            "SELECT name, listprice, "
            "lag(listprice) OVER (PARTITION BY productsubcategoryid ORDER BY name) "
            "FROM production.product",
            id="window-with-partition",
        ),
        pytest.param("SELECT now()::date AS today", id="cast-and-builtin-fn"),
        pytest.param(
            "SELECT string_agg(DISTINCT firstname, ', ' ORDER BY firstname) FROM person.person",
            id="string-agg-ordered",
        ),
    ],
)
def test_additional_allowed_shapes(sql: str) -> None:
    check_read_only(sql)


def test_guard_does_not_use_string_matching() -> None:
    """A denylisted name inside a string literal is data, not a call.

    This is the regex failure mode running in the opposite direction: a text
    denylist rejects this perfectly ordinary query.
    """
    check_read_only("SELECT 'pg_read_file' AS fn_name, 'DELETE FROM x' AS note FROM person.person LIMIT 1")


def test_trailing_comment_is_not_a_second_statement() -> None:
    """A comment after the semicolon is not statement stacking."""
    check_read_only("SELECT * FROM person.person LIMIT 1; -- trailing note")
    check_read_only("SELECT 1; -- x\n-- y")
