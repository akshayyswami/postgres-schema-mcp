"""Layer 2 of the safety model: an AST policy over the submitted SQL.

The policy is deliberately an allowlist of *shapes* rather than a denylist of
strings. A single statement is accepted only when it is a SELECT (or a set
operation over SELECTs, or a WITH whose body is one of those) and when no node
anywhere in the tree is DML, DDL, a lock, or a call to a denylisted function.

Why not a regex. The query that defeats a regex allowlist is:

    WITH gone AS (DELETE FROM customer RETURNING *) SELECT * FROM gone;

It begins with WITH, it contains SELECT, and it empties the table. sqlglot parses
it to a top-level Select whose tree contains a Delete node, so walking the AST
catches it and a prefix match does not. The same argument runs in the other
direction: a text denylist rejects

    SELECT 'DROP TABLE customer' AS note FROM person.person;

which is an ordinary read-only query. Both cases are in the fault matrix.

This layer is not the last line of defence. It sits above a database role that
holds only SELECT, because a parser bug here should not be a write there.
"""

from __future__ import annotations

import re
from typing import Final

import sqlglot
from sqlglot import exp

__all__ = ["DENYLISTED_FUNCTIONS", "DENYLISTED_PREFIXES", "BlockedQueryError", "check_read_only"]

_DIALECT: Final = "postgres"

_REMEDY: Final = "This server is read-only. Rewrite the request as a plain SELECT."


class BlockedQueryError(Exception):
    """Raised when a statement fails the read-only policy.

    The string form is written to be read by a model: it names the construct
    that was rejected and says what to do instead. server.py turns this into a
    tool execution error (isError: true), not a protocol error.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Blocked: {reason} {_REMEDY}")


# Every node type below is either a write, a schema change, a privilege change,
# or a lock. Mapped to the wording used in the message back to the model.
_FORBIDDEN_NODES: Final[dict[type[exp.Expr], str]] = {
    exp.Insert: "INSERT",
    exp.Update: "UPDATE",
    exp.Delete: "DELETE",
    exp.Merge: "MERGE",
    exp.Drop: "DROP",
    exp.Create: "CREATE",
    exp.Alter: "ALTER",
    exp.TruncateTable: "TRUNCATE",
    exp.Grant: "GRANT",
    exp.Revoke: "REVOKE",
    exp.Copy: "COPY",
    # SELECT ... INTO creates a new table in Postgres, so it is DDL wearing a
    # SELECT costume.
    exp.Into: "SELECT ... INTO",
    # FOR UPDATE / FOR SHARE take row locks, which is a write to shared state
    # even though no column value changes.
    exp.Lock: "row-locking clause (FOR UPDATE / FOR SHARE)",
    # sqlglot represents statements it has no grammar for as a Command
    # passthrough. Anything the parser cannot describe cannot be vouched for, so
    # it is refused rather than forwarded.
    exp.Command: "statement the parser could not classify",
    exp.Set: "SET",
    exp.Transaction: "transaction control",
    exp.Commit: "COMMIT",
    exp.Rollback: "ROLLBACK",
}

# Checked against every function node in the tree, never against the raw text.
# Names are compared case-folded, and a schema qualifier such as
# pg_catalog.pg_read_file still resolves to the bare name here.
DENYLISTED_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {
        # Filesystem access
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "pg_ls_logdir",
        "pg_ls_waldir",
        "pg_ls_tmpdir",
        "pg_ls_archive_statusdir",
        # Large objects, which are a second path to the filesystem
        "lo_import",
        "lo_export",
        "lo_get",
        "lo_put",
        "lo_from_bytea",
        "lo_unlink",
        # Network egress
        "dblink",
        "dblink_exec",
        "dblink_connect",
        "dblink_connect_u",
        "dblink_open",
        "dblink_send_query",
        "http",
        "http_get",
        "http_post",
        "http_put",
        "http_delete",
        # Executes an arbitrary SQL string, so it is a way around this whole
        # policy if left reachable.
        "query_to_xml",
        "query_to_xmlschema",
        "query_to_xml_and_xmlschema",
        # Denial of service
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        # Server administration
        "pg_reload_conf",
        "pg_rotate_logfile",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_create_restore_point",
        "pg_switch_wal",
        "pg_promote",
        "pg_backup_start",
        "pg_backup_stop",
        "set_config",
        "pg_logical_emit_message",
        "pg_notify",
        "pg_import_system_collations",
        "pg_start_backup",
        "pg_stop_backup",
        # Change database state although they sit inside a SELECT
        "nextval",
        "setval",
        "txid_current",
        "pg_current_xact_id",
        "loread",
        "lowrite",
        # Execute an SQL string passed as an argument, outside this policy
        "cursor_to_xml",
        "cursor_to_xmlschema",
        "connectby",
        "ts_stat",
        "xpath_table",
    }
)

# Whole families of functions. A name that starts with one of these is refused, so a
# function added in a later PostgreSQL release, or one this list missed, is still caught.
# The cost is that a user function with such a prefix is refused too.
DENYLISTED_PREFIXES: Final[tuple[str, ...]] = (
    "lo_",  # large objects: filesystem access and writes
    "dblink",  # network egress and remote execution
    "http_",  # network egress
    "crosstab",  # tablefunc: runs an SQL string passed as an argument
    "pg_sleep",  # denial of service
    "pg_advisory_",  # session-level locks outlive the statement on a pooled connection
    "pg_try_advisory_",
    "pg_read_",  # filesystem
    "pg_ls_",
    "pg_file_",
    "pg_replication_",  # replication slots and origins
    "pg_create_",
    "pg_drop_",
    "pg_copy_",
    "pg_stat_reset",  # clears statistics
    "pg_set_",
    "pg_clear_",
    "pg_wal_replay",
    "pg_log_",  # writes to the server log
    "pg_logical_",
)

# A function name that is not a plain identifier cannot be matched against a list of
# plain names, so it is refused. This covers U&"..." escapes, where the name PostgreSQL
# calls is not the text that was written.
_PLAIN_NAME: Final = re.compile(r"[a-z_][a-z0-9_$]*")

# A statement is acceptable only if its root is one of these.
_ALLOWED_ROOTS: Final[tuple[type[exp.Expr], ...]] = (
    exp.Select,
    exp.Union,
    exp.Except,
    exp.Intersect,
    exp.Subquery,
)


def _function_names(node: exp.Expr) -> list[str]:
    """Every name by which this node might be a denylisted function call."""
    names: list[str] = []
    if isinstance(node, exp.Anonymous):
        # Unknown-to-sqlglot function. `name` is the text without quotes, so
        # "pg_sleep"(1) and pg_sleep(1) give the same name. `str(node.this)` is not
        # that: for a quoted name it is an Identifier node and never matches.
        names.append(node.name)
    elif isinstance(node, exp.Func):
        # Known function: compare on sqlglot's canonical name.
        names.append(node.sql_name())
    return [n.casefold() for n in names if n]


def check_read_only(sql: str) -> None:
    """Raise BlockedQueryError unless `sql` is a single read-only statement.

    Returns None on success. Fails closed: anything that cannot be parsed, or
    that parses to something other than exactly one SELECT-shaped statement, is
    blocked.
    """
    try:
        statements = sqlglot.parse(sql, dialect=_DIALECT, error_level=sqlglot.ErrorLevel.RAISE)
    except Exception as exc:  # sqlglot raises several types; all mean "unverifiable"
        raise BlockedQueryError(
            f"the statement could not be parsed as PostgreSQL ({type(exc).__name__}), "
            "so it cannot be checked."
        ) from exc

    # sqlglot yields None for an empty segment and an exp.Semicolon placeholder
    # for a segment that is only a comment, e.g. the tail of "SELECT 1; -- note".
    # Neither is a statement. Note this does not weaken the stacking check:
    # "SELECT 1; /* c */ DROP TABLE t;" still parses to two real statements.
    real = [s for s in statements if s is not None and not isinstance(s, exp.Semicolon)]
    if not real:
        raise BlockedQueryError(
            "no SQL statement was found (the input was empty or only comments)."
        )
    if len(real) > 1:
        raise BlockedQueryError(
            f"the input contains {len(real)} statements; statement stacking is not allowed. "
            "Send exactly one statement."
        )

    root = real[0]
    if not isinstance(root, _ALLOWED_ROOTS):
        label = _FORBIDDEN_NODES.get(type(root), type(root).__name__.upper())
        raise BlockedQueryError(f"the statement is a {label}, and only SELECT is permitted.")

    for node in root.walk():
        for forbidden, label in _FORBIDDEN_NODES.items():
            if isinstance(node, forbidden):
                if node is root:
                    raise BlockedQueryError(
                        f"the statement is a {label}, and only SELECT is permitted."
                    )
                raise BlockedQueryError(
                    f"the statement contains a {label} nested inside the query "
                    "(in a CTE or subquery), which a prefix check would miss."
                )

        for name in _function_names(node):
            if _PLAIN_NAME.fullmatch(name) is None:
                raise BlockedQueryError(
                    "the statement calls a function whose name is not a plain identifier "
                    '(for example one written with U&"..." escapes), so it cannot be checked.'
                )
            if name in DENYLISTED_FUNCTIONS or name.startswith(DENYLISTED_PREFIXES):
                raise BlockedQueryError(
                    f"the statement calls {name}(), which is not permitted "
                    "(filesystem, network, administrative, locking or denial-of-service function)."
                )
