"""
Guards against unsafe generated SQL before it ever touches the database.

Uses sqlglot AST parsing so string literals containing blocked words,
semicolons, or comment markers are not false-flagged, while stacked queries,
non-read-only root statements, file-reading functions, and unsafe AST nodes
are strictly blocked.
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

# Block file-system, network, and internal-secret inspection functions
BLOCKED_FUNCTIONS = {
    "read_text",
    "read_blob",
    "read_csv",
    "read_csv_auto",
    "read_parquet",
    "read_json",
    "read_json_auto",
    "read_ndjson",
    "read_ndjson_auto",
    "read_xlsx",
    "glob",
    "duckdb_secrets",
    "duckdb_settings",
    "pg_read_file",
    "pg_ls_dir",
    "lo_import",
    "lo_export",
    "iceberg_scan",
    "delta_scan",
    "sqlite_scan",
    "postgres_scan",
    "mysql_scan",
    "getenv",
}

ALLOWED_ROOT_NODES = tuple(
    cls
    for cls in (
        getattr(exp, "Select", None),
        getattr(exp, "Union", None),
        getattr(exp, "Intersect", None),
        getattr(exp, "Except", None),
    )
    if cls is not None
)

# Only SHOW and DESCRIBE are allowed as meta root nodes via the AST path.
# EXPLAIN and SUMMARIZE are intentionally excluded here — they are handled
# in the Command path below with stricter inner-query validation.
ALLOWED_META_ROOT_NODES = tuple(
    cls
    for cls in (
        getattr(exp, "Show", None),
        getattr(exp, "Describe", None),
    )
    if cls is not None
)


class SQLSafetyError(Exception):
    """Raised when a generated query fails a safety check."""


def _check_unsafe_functions(stmt: exp.Expression) -> None:
    """Walk AST to block file-reading and system-inspection functions."""
    for node in stmt.walk():
        if isinstance(node, exp.Anonymous) and node.name.lower() in BLOCKED_FUNCTIONS:
            raise SQLSafetyError(f"Function '{node.name}' is not allowed.")
        if isinstance(node, exp.Func):
            func_name = (node.sql_name() or "").lower()
            if func_name in BLOCKED_FUNCTIONS:
                raise SQLSafetyError(f"Function '{func_name}' is not allowed.")


def validate_sql_safety(
    sql: str,
    default_limit: int = 50,
    dialect: str = "duckdb",
    allow_meta: bool = False,
) -> str:
    """
    Validates via sqlglot AST that the SQL is a single, safe, read-only query.
    Defensively attaches a LIMIT clause to SELECT/set-op queries if omitted.
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
    is_query = isinstance(stmt, ALLOWED_ROOT_NODES)
    is_meta = allow_meta and isinstance(stmt, ALLOWED_META_ROOT_NODES)

    # Handle DuckDB SHOW/DESCRIBE/SUMMARIZE if sqlglot parses them as Command.
    # EXPLAIN and EXPLAIN ANALYZE are always rejected — EXPLAIN ANALYZE executes
    # the wrapped DML statement in DuckDB, which is a critical data-destruction risk.
    if allow_meta and isinstance(stmt, exp.Command):
        cmd_name = (stmt.name or "").upper()
        if cmd_name in {"EXPLAIN", "SUMMARIZE"}:
            raise SQLSafetyError(
                f"EXPLAIN and SUMMARIZE are not allowed in the playground. "
                f"Use SHOW or DESCRIBE instead."
            )
        if cmd_name in {"SHOW", "DESCRIBE"}:
            return f"{stmt.sql(dialect=dialect).rstrip(';')};"

    if not (is_query or is_meta):
        raise SQLSafetyError("Only SELECT queries are allowed.")

    for node in stmt.walk():
        if isinstance(node, UNSAFE_AST_NODES):
            raise SQLSafetyError(f"Query contains a disallowed SQL operation: {type(node).__name__}")

    _check_unsafe_functions(stmt)

    if is_query and stmt.args.get("limit") is None:
        stmt = stmt.limit(default_limit)

    return f"{stmt.sql(dialect=dialect)};"
