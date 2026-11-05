"""
Comprehensive Unit Tests for SQL Guard (backend/sql_guard.py)
Tests 40+ scenarios covering:
- Valid SELECT and WITH CTE syntax
- Defensive LIMIT injection and preservation
- Stacked queries (semicolon injection vectors)
- Blocked mutation keywords with case variation
- False positive checks on column names (created_at, execute_date)
- String literal keyword false positives (xfail)
- Comment handling (xfail)
- Security gaps: system catalogs and pg_sleep (xfail)
"""

import sys
from pathlib import Path
import pytest

# Ensure backend is on sys.path
backend_path = Path(__file__).resolve().parent.parent / "backend"
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

from sql_guard import validate_sql_safety, SQLSafetyError, BLOCKED_KEYWORDS


# ==============================================================================
# 1. Valid Query Acceptance & Structure
# ==============================================================================
class TestValidQueries:
    def test_simple_select(self):
        sql = "SELECT id, name FROM customers"
        res = validate_sql_safety(sql)
        assert res.startswith("SELECT id, name FROM customers")
        assert res.endswith(";")

    def test_select_all_columns(self):
        sql = "SELECT * FROM orders"
        res = validate_sql_safety(sql)
        assert "SELECT * FROM orders" in res

    def test_select_with_inner_join(self):
        sql = "SELECT c.name, o.id FROM customers c JOIN orders o ON c.id = o.customer_id"
        res = validate_sql_safety(sql)
        assert "JOIN" in res

    def test_select_with_left_join(self):
        sql = "SELECT c.name, COUNT(o.id) FROM customers c LEFT JOIN orders o ON c.id = o.customer_id GROUP BY c.name"
        res = validate_sql_safety(sql)
        assert "LEFT JOIN" in res

    def test_select_with_multi_table_joins(self):
        sql = """
        SELECT c.name, p.name, oi.quantity
        FROM customers c
        JOIN orders o ON c.id = o.customer_id
        JOIN order_items oi ON o.id = oi.order_id
        JOIN products p ON oi.product_id = p.id
        """
        res = validate_sql_safety(sql)
        assert "order_items" in res

    def test_select_with_aggregate_functions(self):
        sql = "SELECT category, AVG(price), MAX(price), MIN(price), COUNT(*) FROM products GROUP BY category"
        res = validate_sql_safety(sql)
        assert "AVG(price)" in res

    def test_select_with_subquery(self):
        sql = "SELECT name, price FROM products WHERE price > (SELECT AVG(price) FROM products)"
        res = validate_sql_safety(sql)
        assert "SELECT AVG(price)" in res

    def test_cte_single_statement(self):
        sql = """
        WITH high_spenders AS (
            SELECT customer_id, SUM(quantity * unit_price) AS total
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.id
            GROUP BY customer_id
        )
        SELECT * FROM high_spenders WHERE total > 1000
        """
        res = validate_sql_safety(sql)
        assert res.strip().startswith("WITH high_spenders")

    def test_cte_multiple_blocks(self):
        sql = """
        WITH cte1 AS (SELECT id FROM customers),
             cte2 AS (SELECT id FROM products)
        SELECT * FROM cte1 JOIN cte2 ON cte1.id = cte2.id
        """
        res = validate_sql_safety(sql)
        assert "cte2" in res

    def test_window_functions(self):
        sql = "SELECT name, price, RANK() OVER (PARTITION BY category ORDER BY price DESC) FROM products"
        res = validate_sql_safety(sql)
        assert "RANK() OVER" in res


# ==============================================================================
# 2. LIMIT Enforcement & Preservation
# ==============================================================================
class TestLimitHandling:
    def test_limit_appended_when_missing(self):
        sql = "SELECT * FROM customers"
        res = validate_sql_safety(sql)
        assert "LIMIT 50;" in res

    def test_custom_default_limit(self):
        sql = "SELECT * FROM customers"
        res = validate_sql_safety(sql, default_limit=10)
        assert "LIMIT 10;" in res

    def test_existing_limit_preserved(self):
        sql = "SELECT * FROM customers LIMIT 5"
        res = validate_sql_safety(sql)
        assert "LIMIT 5;" in res
        assert "LIMIT 50" not in res

    def test_existing_limit_lowercase(self):
        sql = "SELECT * FROM customers limit 20;"
        res = validate_sql_safety(sql)
        assert "limit 20;" in res
        assert "LIMIT 50" not in res

    def test_existing_limit_with_offset(self):
        sql = "SELECT * FROM customers LIMIT 10 OFFSET 20"
        res = validate_sql_safety(sql)
        assert "LIMIT 10 OFFSET 20;" in res


# ==============================================================================
# 3. Stacked Statements & Semicolon Injections
# ==============================================================================
class TestStackedStatements:
    def test_multiple_select_statements_blocked(self):
        sql = "SELECT 1; SELECT 2"
        with pytest.raises(SQLSafetyError, match="Multiple SQL statements"):
            validate_sql_safety(sql)

    def test_stacked_drop_blocked(self):
        sql = "SELECT * FROM customers; DROP TABLE orders"
        with pytest.raises(SQLSafetyError):
            validate_sql_safety(sql)

    def test_trailing_semicolon_permitted(self):
        # A single trailing semicolon should be cleaned and accepted
        sql = "SELECT * FROM customers;"
        res = validate_sql_safety(sql)
        assert res.endswith(";")
        assert res.count(";") == 1

    def test_double_trailing_semicolons_collapsed(self):
        # rstrip(";") strips all trailing semicolons, producing a valid single statement
        sql = "SELECT * FROM customers;;"
        res = validate_sql_safety(sql)
        assert res.count(";") == 1

    def test_semicolon_with_whitespace_between_blocked(self):
        sql = "SELECT * FROM customers;  ; SELECT 1"
        with pytest.raises(SQLSafetyError, match="Multiple SQL statements"):
            validate_sql_safety(sql)


# ==============================================================================
# 4. Keyword Blacklist Verification (Each disallowed keyword)
# ==============================================================================
class TestBlockedKeywords:
    @pytest.mark.parametrize("kw", [
        "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE",
        "CREATE", "GRANT", "REVOKE", "EXEC", "EXECUTE", "CALL",
        "COPY", "VACUUM", "REINDEX"
    ])
    def test_blocked_keyword_in_statement(self, kw):
        # Even inside a query, if it appears as a distinct word
        sql = f"SELECT * FROM customers WHERE {kw} = 1"
        with pytest.raises(SQLSafetyError, match="disallowed keyword"):
            validate_sql_safety(sql)

    def test_blocked_keyword_lowercase(self):
        sql = "drop table customers"
        with pytest.raises(SQLSafetyError):
            validate_sql_safety(sql)

    def test_blocked_keyword_mixed_case(self):
        sql = "dRoP TABLE customers"
        with pytest.raises(SQLSafetyError):
            validate_sql_safety(sql)

    def test_delete_statement_rejected(self):
        sql = "DELETE FROM customers WHERE id = 1"
        with pytest.raises(SQLSafetyError):
            validate_sql_safety(sql)

    def test_insert_statement_rejected(self):
        sql = "INSERT INTO customers (name) VALUES ('Test')"
        with pytest.raises(SQLSafetyError):
            validate_sql_safety(sql)

    def test_truncate_statement_rejected(self):
        sql = "TRUNCATE TABLE orders"
        with pytest.raises(SQLSafetyError):
            validate_sql_safety(sql)


# ==============================================================================
# 5. Non-SELECT Leading Statements
# ==============================================================================
class TestLeadingStatements:
    def test_starts_with_update_rejected(self):
        sql = "UPDATE customers SET name = 'X'"
        with pytest.raises(SQLSafetyError, match="Only SELECT"):
            validate_sql_safety(sql)

    def test_empty_query_rejected(self):
        with pytest.raises(SQLSafetyError, match="empty"):
            validate_sql_safety("")

    def test_whitespace_only_rejected(self):
        with pytest.raises(SQLSafetyError, match="empty"):
            validate_sql_safety("    \n\t  ")

    def test_query_exceeding_max_length(self):
        sql = "SELECT " + ("a" * 2005)
        with pytest.raises(SQLSafetyError, match="maximum allowed length"):
            validate_sql_safety(sql)


# ==============================================================================
# 6. False Positive Checks (Legitimate column names containing keywords)
# ==============================================================================
class TestFalsePositiveColumns:
    def test_created_at_column_allowed(self):
        # 'created_at' contains 'CREATE' as a substring, but should not trigger \bCREATE\b
        sql = "SELECT id, created_at FROM customers WHERE created_at > '2024-01-01'"
        res = validate_sql_safety(sql)
        assert "created_at" in res

    def test_updated_at_column_allowed(self):
        sql = "SELECT id, updated_at FROM orders"
        res = validate_sql_safety(sql)
        assert "updated_at" in res

    def test_execute_flag_column_allowed(self):
        sql = "SELECT is_executed FROM tasks"
        res = validate_sql_safety(sql)
        assert "is_executed" in res

    def test_description_column_allowed(self):
        # contains 'script'
        sql = "SELECT description FROM products"
        res = validate_sql_safety(sql)
        assert "description" in res


# ==============================================================================
# 7. GAPS / Edge Cases (Documented in eval/FINDINGS.md)
# ==============================================================================
class TestKnownGapsAndVulnerabilities:
    @pytest.mark.xfail(reason="BUG-01: Guard uses naive regex and blocks keywords inside string literals")
    def test_keyword_inside_string_literal_false_positive(self):
        """
        Legitimate query filtering for an item whose name/status is 'DROP' or 'DELETE'.
        Currently fails because regex matches \\bDROP\\b anywhere in the string.
        """
        sql = "SELECT * FROM orders WHERE status = 'DROP'"
        # Expected: should be safe because 'DROP' is a data literal, not a statement
        res = validate_sql_safety(sql)
        assert "status = 'DROP'" in res

    @pytest.mark.xfail(reason="BUG-02: Guard regex rejects queries with leading comments")
    def test_leading_comment_false_positive(self):
        """
        Queries with leading comments fail the `^\\s*(SELECT|WITH)\\s` regex.
        """
        sql = "-- Fetch active customers\nSELECT * FROM customers"
        res = validate_sql_safety(sql)
        assert "customers" in res

    @pytest.mark.xfail(reason="BUG-02: Block comment prefix fails regex")
    def test_block_comment_prefix_false_positive(self):
        sql = "/* query_id: 42 */ SELECT * FROM customers"
        res = validate_sql_safety(sql)
        assert "customers" in res

    @pytest.mark.xfail(reason="BUG-03: Parenthesized SELECT query fails prefix regex")
    def test_parenthesized_select_false_positive(self):
        sql = "(SELECT name FROM customers)"
        res = validate_sql_safety(sql)
        assert "customers" in res

    @pytest.mark.xfail(reason="SEC-01: Guard does not block system catalog inspection")
    def test_pg_shadow_system_catalog_blocked(self):
        """
        Security vulnerability: Reading pg_shadow passes guard.
        Should ideally be blocked before touching the database.
        """
        sql = "SELECT usename, passwd FROM pg_shadow"
        # Test expects SQLSafetyError if guard were complete
        with pytest.raises(SQLSafetyError):
            validate_sql_safety(sql)

    @pytest.mark.xfail(reason="SEC-02: Guard does not block pg_sleep DoS functions")
    def test_pg_sleep_blocked(self):
        """
        Security vulnerability: pg_sleep passes guard.
        """
        sql = "SELECT pg_sleep(10)"
        with pytest.raises(SQLSafetyError):
            validate_sql_safety(sql)
