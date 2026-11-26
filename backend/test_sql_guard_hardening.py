"""Regression tests for AST-based sql_guard.py."""

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from sql_guard import SQLSafetyError, validate_sql_safety


@pytest.mark.parametrize(
    "sql",
    [
        "/* report */ SELECT id, name FROM customers",
        "SELECT name, ROW_NUMBER() OVER (PARTITION BY category ORDER BY price DESC) AS rn FROM products",
        "WITH c AS (SELECT id FROM orders) SELECT * FROM c",
        "SELECT id, name FROM customers WHERE email = 'please delete; /* ok */'",
        "SELECT id, name FROM customers WHERE city = 'update; drop table orders'",
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
    ],
)
def test_adversarial_queries_blocked(sql: str):
    with pytest.raises(SQLSafetyError):
        validate_sql_safety(sql)
