"""
Guards against unsafe generated SQL before it ever touches the database.

This is a defense-in-depth layer. The primary safety mechanism is running
queries through a read-only DB transaction/role — this module is the first
line of defense so we fail fast with a clear error instead of relying on
the database to reject a destructive statement.
"""

import re

# Statements that should never appear in a text-to-SQL demo — this is a
# read-only query tool, so anything beyond SELECT is out of scope.
BLOCKED_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE",
    "CREATE", "GRANT", "REVOKE", "EXEC", "EXECUTE", "CALL",
    "COPY", "VACUUM", "REINDEX",
]

MAX_QUERY_LENGTH = 2000


class SQLSafetyError(Exception):
    """Raised when a generated query fails a safety check."""


def validate_sql_safety(sql: str, default_limit: int = 50) -> str:
    """
    Validates that the SQL is a safe, single, read-only statement.
    Defensively attaches a LIMIT clause if the LLM omitted one.
    Returns the validated SQL query string.
    """
    if not sql or not sql.strip():
        raise SQLSafetyError("Generated SQL was empty.")

    cleaned = sql.strip().rstrip(";")

    if len(cleaned) > MAX_QUERY_LENGTH:
        raise SQLSafetyError("Generated SQL exceeds the maximum allowed length.")

    # Reject multiple statements (stacked queries) — a classic injection vector
    if ";" in cleaned:
        raise SQLSafetyError("Multiple SQL statements are not allowed.")

    if not re.match(r"^\s*(SELECT|WITH)\s", cleaned, re.IGNORECASE):
        raise SQLSafetyError("Only SELECT queries are allowed.")

    upper_sql = cleaned.upper()
    for keyword in BLOCKED_KEYWORDS:
        # Match as a whole word to avoid false positives (e.g. "created_at" containing "CREATE")
        if re.search(rf"\b{keyword}\b", upper_sql):
            raise SQLSafetyError(f"Query contains a disallowed keyword: {keyword}")

    # If LIMIT was omitted by the model, defensively append it
    if not re.search(r"\bLIMIT\b", cleaned, re.IGNORECASE):
        cleaned = f"{cleaned} LIMIT {default_limit}"

    return f"{cleaned};"
