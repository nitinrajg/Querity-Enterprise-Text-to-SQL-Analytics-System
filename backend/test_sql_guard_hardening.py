"""Regression tests for AST-based sql_guard.py and DuckDB external access."""

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from sql_guard import SQLSafetyError, validate_sql_safety
from duckdb_service import execute_duckdb_sql


@pytest.mark.parametrize(
    "sql",
    [
        "/* report */ SELECT id, name FROM customers",
        "SELECT name, ROW_NUMBER() OVER (PARTITION BY category ORDER BY price DESC) AS rn FROM products",
        "WITH c AS (SELECT id FROM orders) SELECT * FROM c",
        "SELECT id, name FROM customers WHERE email = 'please delete; /* ok */'",
        "SELECT id, name FROM customers WHERE city = 'update; drop table orders'",
        "SELECT id FROM customers INTERSECT SELECT customer_id FROM orders",
        "SELECT id FROM customers EXCEPT SELECT customer_id FROM orders",
    ],
)
def test_valid_queries_allowed(sql: str):
    validated = validate_sql_safety(sql)
    assert validated.endswith(";")
    assert "LIMIT" in validated.upper()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM customers; DROP TABLE orders;",
        "/* bypass */ DELETE FROM orders;",
        "COPY customers TO '/tmp/out.csv';",
        "ATTACH '/tmp/evil.db' AS evil;",
        "EXPORT DATABASE '/tmp/dump';",
        "PRAGMA table_info('customers');",
        "CREATE TEMP TABLE t AS SELECT * FROM customers;",
        "INSERT INTO products (name, category, price, stock_qty) VALUES ('X', 'Y', 1, 1);",
        "SELECT content FROM read_text('.env');",
        "SELECT * FROM read_csv('/etc/passwd');",
        "SELECT * FROM glob('*');",
    ],
)
def test_adversarial_queries_blocked(sql: str):
    with pytest.raises(SQLSafetyError):
        validate_sql_safety(sql)


def test_playground_meta_allows_single_show_but_blocks_stacked_and_pragma():
    assert validate_sql_safety("SHOW TABLES;", allow_meta=True).upper().startswith("SHOW")
    with pytest.raises(SQLSafetyError):
        validate_sql_safety("SHOW TABLES; DROP TABLE orders;", allow_meta=True)
    with pytest.raises(SQLSafetyError):
        validate_sql_safety("PRAGMA version;", allow_meta=True)


def test_duckdb_external_access_disabled():
    with pytest.raises(Exception):
        execute_duckdb_sql("SELECT content FROM read_text('.env')")


# --- EXPLAIN / EXPLAIN ANALYZE regression tests (Fix 1) ---

@pytest.mark.parametrize(
    "sql",
    [
        "EXPLAIN ANALYZE DELETE FROM products;",
        "EXPLAIN ANALYZE DROP TABLE customers;",
        "EXPLAIN DELETE FROM products;",
        "EXPLAIN DROP TABLE customers;",
        "EXPLAIN INSERT INTO products VALUES (99,'x','y',1,1);",
        "SUMMARIZE SELECT * FROM products;",
    ],
)
def test_explain_analyze_always_blocked(sql: str):
    """EXPLAIN ANALYZE executes the wrapped DML in DuckDB — must always be rejected."""
    with pytest.raises(SQLSafetyError):
        validate_sql_safety(sql, allow_meta=True)


def test_show_tables_still_allowed():
    """SHOW TABLES must remain valid after the EXPLAIN lockdown."""
    result = validate_sql_safety("SHOW TABLES;", allow_meta=True)
    assert result.upper().startswith("SHOW")


def test_describe_still_allowed():
    """DESCRIBE must remain valid after the EXPLAIN lockdown."""
    result = validate_sql_safety("DESCRIBE products;", allow_meta=True)
    assert result.upper().startswith("DESCRIBE")
