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

# Strict allowlist of safe analytical, aggregate, window, math, date, and string functions.
# Deny-by-default blocks pg_read_binary_file, pg_sleep, set_config, query_to_xml, dblink,
# pg_terminate_backend, current_setting, read_text, read_csv, glob, version, etc.
ALLOWED_FUNCTIONS = {
    # AST logical/conditional operators that subclass exp.Func in sqlglot
    "and",
    "or",
    "xor",
    "case",
    "if",
    "cast",
    "try_cast",
    "collate",
    "nullif",
    "coalesce",
    "ifnull",
    "greatest",
    "least",
    # Aggregates & statistical functions
    "count",
    "count_if",
    "sum",
    "avg",
    "min",
    "max",
    "stddev",
    "stddev_pop",
    "stddev_samp",
    "variance",
    "var_pop",
    "var_samp",
    "median",
    "mode",
    "percentile_cont",
    "percentile_disc",
    "approx_count_distinct",
    "bool_and",
    "bool_or",
    "any_value",
    "array_agg",
    "group_concat",
    "string_agg",
    "list",
    "corr",
    # Window functions
    "row_number",
    "rank",
    "dense_rank",
    "ntile",
    "lag",
    "lead",
    "first_value",
    "last_value",
    "nth_value",
    "percent_rank",
    "cume_dist",
    # Math functions
    "abs",
    "round",
    "ceil",
    "ceiling",
    "floor",
    "trunc",
    "mod",
    "power",
    "pow",
    "sqrt",
    "sign",
    "exp",
    "ln",
    "log",
    "log10",
    "log2",
    # Read-only date/time functions
    "date_trunc",
    "timestamp_trunc",
    "date_part",
    "extract",
    "current_date",
    "current_timestamp",
    "current_time",
    "now",
    "age",
    "to_char",
    "to_date",
    "to_timestamp",
    "time_to_str",
    "date_add",
    "date_sub",
    "date_diff",
    "datediff",
    "dateadd",
    "make_date",
    "strftime",
    "strptime",
    "epoch",
    # String & formatting functions
    "lower",
    "upper",
    "initcap",
    "trim",
    "btrim",
    "ltrim",
    "rtrim",
    "length",
    "char_length",
    "octet_length",
    "substring",
    "substr",
    "concat",
    "concat_ws",
    "replace",
    "split_part",
    "left",
    "right",
    "reverse",
    "lpad",
    "rpad",
    "strpos",
    "position",
    "regexp_like",
    "regexp_ilike",
    "regexp_full_match",
    "regexp_replace",
    "regexp_matches",
    "translate",
    # Read-only type & JSON extraction helpers
    "typeof",
    "json_extract",
    "json_extract_scalar",
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


def _check_allowed_functions(stmt: exp.Expression) -> None:
    """Walk AST to enforce strict function allowlist and block schema-qualified calls."""
    for node in stmt.walk():
        if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func):
            raise SQLSafetyError(f"Schema-qualified function calls are not allowed: {node.sql()}")
        if isinstance(node, exp.Anonymous):
            func_name = (node.name or "").lower()
            if func_name not in ALLOWED_FUNCTIONS:
                raise SQLSafetyError(f"Function '{node.name}' is not allowed.")
        elif isinstance(node, exp.Func):
            func_name = (node.sql_name() or "").lower()
            if func_name and func_name not in ALLOWED_FUNCTIONS:
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

    _check_allowed_functions(stmt)

    if is_query and stmt.args.get("limit") is None:
        stmt = stmt.limit(default_limit)

    return f"{stmt.sql(dialect=dialect)};"
