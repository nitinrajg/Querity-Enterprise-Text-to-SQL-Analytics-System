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


@pytest.mark.parametrize(
    "dialect,sql",
    [
        ("postgres", "SELECT pg_read_binary_file('/etc/passwd');"),
        ("postgres", "SELECT pg_sleep(5);"),
        ("postgres", "SELECT set_config('log_statement', 'all', false);"),
        ("postgres", "SELECT query_to_xml('SELECT 1', false, true, '');"),
        ("postgres", "SELECT dblink('dbname=postgres', 'SELECT 1');"),
        ("postgres", "SELECT pg_terminate_backend(1);"),
        ("postgres", "SELECT current_setting('data_directory');"),
        ("postgres", "SELECT version();"),
        ("postgres", "SELECT pg_catalog.pg_read_binary_file('/etc/passwd');"),
        ("duckdb", "SELECT * FROM (SELECT pg_sleep(1)) AS sub;"),
    ],
)
def test_disallowed_functions_blocked_by_allowlist(dialect: str, sql: str):
    """Any function outside ALLOWED_FUNCTIONS (including dangerous Postgres functions) must fail."""
    with pytest.raises(SQLSafetyError):
        validate_sql_safety(sql, dialect=dialect)


@pytest.mark.parametrize(
    "dialect,sql",
    [
        (
            "postgres",
            "SELECT c.name, COUNT(o.id) AS cnt, COALESCE(SUM(oi.quantity * oi.unit_price), 0) AS spend "
            "FROM customers c LEFT JOIN orders o ON c.id = o.customer_id "
            "LEFT JOIN order_items oi ON o.id = oi.order_id "
            "WHERE o.status = 'delivered' AND (c.country = 'India' OR c.country = 'Canada') "
            "GROUP BY c.name ORDER BY spend DESC",
        ),
        (
            "postgres",
            "SELECT DATE_TRUNC('month', order_date) AS m, EXTRACT(YEAR FROM order_date) AS y, "
            "CASE WHEN COUNT(*) > 1 THEN 'multi' ELSE 'single' END AS bucket "
            "FROM orders GROUP BY 1, 2",
        ),
        (
            "duckdb",
            "SELECT category, LOWER(name) AS lname, ROUND(AVG(price), 2) AS avg_p, "
            "ROW_NUMBER() OVER (PARTITION BY category ORDER BY price DESC) AS rn "
            "FROM products GROUP BY category, name, price",
        ),
    ],
)
def test_approved_analytical_functions_allowed_both_dialects(dialect: str, sql: str):
    """Logical operators and analytical SQL functions must pass under the allowlist in both dialects."""
    validated = validate_sql_safety(sql, dialect=dialect)
    assert validated.endswith(";")


def test_postgres_dialect_preserves_to_char_format_and_main_uses_postgres():
    """Passing dialect='postgres' must preserve TO_CHAR format string instead of rewriting via DuckDB."""
    formatted = validate_sql_safety(
        "SELECT TO_CHAR(order_date, 'YYYY-MM') AS ym FROM orders",
        dialect="postgres",
    )
    assert "'YYYY-MM'" in formatted
    assert "TO_CHAR" in formatted.upper()

    main_src = (Path(__file__).parent / "main.py").read_text(encoding="utf-8")
    assert 'allowed_tables=approved_tables' in main_src
    assert 'dialect="postgres"' in main_src

    with pytest.raises(SQLSafetyError, match="STRFTIME"):
        validate_sql_safety("SELECT TO_CHAR(order_date, 'YYYY-MM') AS ym FROM orders", dialect="duckdb")


def test_cte_scoping_prevents_outer_system_table_bypass():
    """Inner CTE naming a system table must NEVER whitelist an outer reference to that system table."""
    bypass_attempt = (
        "SELECT * FROM pg_shadow "
        "WHERE EXISTS (WITH pg_shadow AS (SELECT 1) SELECT * FROM pg_shadow)"
    )
    with pytest.raises(SQLSafetyError):
        validate_sql_safety(bypass_attempt, dialect="postgres")

    with pytest.raises(SQLSafetyError):
        validate_sql_safety(
            "SELECT * FROM pg_shadow WHERE id IN (WITH pg_shadow AS (SELECT 1 AS id) SELECT id FROM pg_shadow)",
            dialect="postgres",
            allowed_tables={"customers", "orders"},
        )


@pytest.mark.parametrize(
    "dialect,sql",
    [
        ("duckdb", "SELECT c.name FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id)"),
        ("postgres", "SELECT c.name FROM customers c WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id)"),
        ("duckdb", "SELECT DATE(order_date) AS d FROM orders"),
        ("postgres", "SELECT DATE(order_date) AS d FROM orders"),
        ("duckdb", "SELECT ARRAY[1, 2, 3] AS arr"),
        ("postgres", "SELECT ARRAY[1, 2, 3] AS arr"),
        ("postgres", "WITH pg_shadow_cte AS (SELECT 1 AS id) SELECT * FROM pg_shadow_cte"),
    ],
)
def test_common_sql_constructs_and_ctes_allowed(dialect: str, sql: str):
    """EXISTS, DATE(), ARRAY[], and harmless CTEs must be accepted across dialects."""
    validated = validate_sql_safety(sql, dialect=dialect)
    assert validated.endswith(";")


@pytest.mark.parametrize(
    "dialect,sql",
    [
        ("postgres", "SELECT * FROM pg_shadow;"),
        ("postgres", "SELECT * FROM pg_catalog.pg_roles;"),
        ("postgres", "SELECT * FROM information_schema.tables;"),
        ("duckdb", "SELECT * FROM duckdb_settings();"),
        ("postgres", "SELECT generate_series(1, 1000000);"),
    ],
)
def test_system_catalogs_and_unbounded_series_blocked(dialect: str, sql: str):
    """System tables/schemas and unbounded generate_series must be rejected."""
    with pytest.raises(SQLSafetyError):
        validate_sql_safety(sql, dialect=dialect)


def test_allowed_tables_parameter_restricts_tables():
    """Optional allowed_tables parameter blocks unapproved tables while allowing CTEs over approved tables."""
    ok = validate_sql_safety(
        "WITH c AS (SELECT id FROM orders) SELECT * FROM c",
        dialect="postgres",
        allowed_tables={"orders"},
    )
    assert ok.endswith(";")
    with pytest.raises(SQLSafetyError):
        validate_sql_safety("SELECT * FROM customers", dialect="postgres", allowed_tables={"orders"})
