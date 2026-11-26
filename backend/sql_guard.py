"""
Guards against unsafe generated SQL before it ever touches the database.

Uses sqlglot AST parsing so string literals containing blocked words,
semicolons, or comment markers are not false-flagged, while stacked queries,
non-SELECT/UNION root statements, and unsafe AST nodes are strictly blocked.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

MAX_QUERY_LENGTH = 2000

_UNSAFE_NODE_NAMES = (
    "Insert",
    "Update",
    "Delete",
    "Drop",
    "Create",
    "Alter",
    "TruncateTable",
    "Command",
    "Copy",
    "Pragma",
    "Grant",
    "Revoke",
    "Set",
    "Use",
    "Attach",
    "Detach",
    "Transaction",
    "Commit",
    "Rollback",
)
UNSAFE_AST_NODES = tuple(
    cls for name in _UNSAFE_NODE_NAMES if (cls := getattr(exp, name, None)) is not None
)


class SQLSafetyError(Exception):
    """Raised when a generated query fails a safety check."""


def validate_sql_safety(sql: str, default_limit: int = 50, dialect: str = "duckdb") -> str:
    """
    Validates via sqlglot AST that the SQL is a single, safe, read-only query.
    Defensively attaches a LIMIT clause if the query omitted one.
    Returns the validated SQL query string.
    """
    if not sql or not sql.strip():
        raise SQLSafetyError("Generated SQL was empty.")

    raw = sql.strip()
    if len(raw) > MAX_QUERY_LENGTH:
        raise SQLSafetyError("Generated SQL exceeds the maximum allowed length.")

    try:
        statements = [s for s in sqlglot.parse(raw, read=dialect) if s is not None]
    except ParseError as exc:
        raise SQLSafetyError(f"SQL syntax parse error: {exc}") from exc

    if not statements:
        raise SQLSafetyError("Generated SQL was empty.")

    if len(statements) != 1:
        raise SQLSafetyError("Multiple SQL statements are not allowed.")

    stmt = statements[0]
    if not isinstance(stmt, (exp.Select, exp.Union)):
        raise SQLSafetyError("Only SELECT queries are allowed.")

    for node in stmt.walk():
        if isinstance(node, UNSAFE_AST_NODES):
            raise SQLSafetyError(f"Query contains a disallowed SQL operation: {type(node).__name__}")

    if stmt.args.get("limit") is None:
        stmt = stmt.limit(default_limit)

    return f"{stmt.sql(dialect=dialect)};"
