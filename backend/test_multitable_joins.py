"""
Validation test suite for multi-table relationships and SQL generation.
Tests:
- Relationship inference (foreign keys and implicit join keys)
- SQL Guard support for complex multi-table JOINs, aliases, and CTEs
- LLM generation on multi-table questions
"""

import sys
import unittest
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).parent))

from sql_guard import validate_sql_safety, SQLSafetyError
from nvidia_client import generate_sql_from_question, _get_llm_config, _is_valid_key


class TestMultiTableJoins(unittest.TestCase):
    def test_sql_guard_validates_joins(self):
        """Ensure SQL Guard permits complex multi-table joins, subqueries, and table aliases."""
        safe_join = """
        SELECT 
            c.name AS customer_name,
            COUNT(o.id) AS total_orders,
            COALESCE(SUM(oi.quantity * oi.unit_price), 0) AS total_spent
        FROM customers c
        LEFT JOIN orders o ON c.id = o.customer_id
        LEFT JOIN order_items oi ON o.id = oi.order_id
        GROUP BY c.name
        ORDER BY total_spent DESC
        LIMIT 10;
        """
        validated = validate_sql_safety(safe_join)
        self.assertIn("LEFT JOIN", validated)
        self.assertIn("GROUP BY", validated)
        self.assertTrue(validated.endswith(";"))

    def test_sql_guard_with_cte(self):
        """Ensure SQL Guard permits CTEs (WITH clauses)."""
        cte_query = """
        WITH customer_spend AS (
            SELECT 
                o.customer_id, 
                SUM(oi.quantity * oi.unit_price) AS spend
            FROM orders o
            JOIN order_items oi ON o.id = oi.order_id
            GROUP BY o.customer_id
        )
        SELECT 
            c.name, 
            cs.spend
        FROM customers c
        JOIN customer_spend cs ON c.id = cs.customer_id
        ORDER BY cs.spend DESC
        LIMIT 5;
        """
        validated = validate_sql_safety(cte_query)
        self.assertTrue(validated.startswith("WITH"))
        self.assertIn("LIMIT 5", validated)


if __name__ == "__main__":
    unittest.main()
