"""
Defines, verifies, and exports the 50 benchmark questions for the accuracy evaluation.
Executes every reference SQL against the isolated text2sql_eval database to guarantee:
1. Valid PostgreSQL syntax.
2. Successful execution without errors.
3. Every reference query returns non-empty results on the seed dataset.
"""

import os
import sys
import csv
import asyncio
from pathlib import Path

# Add backend to path
backend_path = Path(__file__).resolve().parent.parent.parent / "backend"
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

eval_path = Path(__file__).resolve().parent.parent
if str(eval_path) not in sys.path:
    sys.path.insert(0, str(eval_path))

from setup_eval_db import get_eval_db_url
import asyncpg

QUESTIONS_DATA = [
    # --------------------------------------------------------------------------
    # 1. Single-table Filter (9 questions)
    # --------------------------------------------------------------------------
    {
        "id": "STF-01",
        "question": "List all customers who live in India.",
        "difficulty_category": "single_table_filter",
        "reference_sql": "SELECT id, name, email, city, country, signed_up_at FROM customers WHERE country = 'India' ORDER BY id;"
    },
    {
        "id": "STF-02",
        "question": "Which products cost more than 1000? Show all product details.",
        "difficulty_category": "single_table_filter",
        "reference_sql": "SELECT id, name, category, price, stock_qty FROM products WHERE price > 1000 ORDER BY price DESC;"
    },
    {
        "id": "STF-03",
        "question": "Find all products in the Electronics category.",
        "difficulty_category": "single_table_filter",
        "reference_sql": "SELECT id, name, category, price, stock_qty FROM products WHERE category = 'Electronics' ORDER BY id;"
    },
    {
        "id": "STF-04",
        "question": "Show all orders that have been delivered.",
        "difficulty_category": "single_table_filter",
        "reference_sql": "SELECT id, customer_id, status, order_date FROM orders WHERE status = 'delivered' ORDER BY id;"
    },
    {
        "id": "STF-05",
        "question": "Which customers signed up before March 2024?",
        "difficulty_category": "single_table_filter",
        "reference_sql": "SELECT id, name, signed_up_at FROM customers WHERE signed_up_at < '2024-03-01' ORDER BY signed_up_at;"
    },
    {
        "id": "STF-06",
        "question": "Find products with stock quantity less than 50 units.",
        "difficulty_category": "single_table_filter",
        "reference_sql": "SELECT id, name, category, price, stock_qty FROM products WHERE stock_qty < 50 ORDER BY stock_qty;"
    },
    {
        "id": "STF-07",
        "question": "Show all orders placed in the month of July 2024.",
        "difficulty_category": "single_table_filter",
        "reference_sql": "SELECT id, customer_id, status, order_date FROM orders WHERE order_date >= '2024-07-01' AND order_date <= '2024-07-31' ORDER BY order_date;"
    },
    {
        "id": "STF-08",
        "question": "List all customers whose name contains the letter 'a' (case-insensitive).",
        "difficulty_category": "single_table_filter",
        "reference_sql": "SELECT id, name FROM customers WHERE name ILIKE '%a%' ORDER BY id;"
    },
    {
        "id": "STF-09",
        "question": "Find the top 3 most expensive products.",
        "difficulty_category": "single_table_filter",
        "reference_sql": "SELECT id, name, price FROM products ORDER BY price DESC LIMIT 3;"
    },

    # --------------------------------------------------------------------------
    # 2. Multi-Table Join (9 questions)
    # --------------------------------------------------------------------------
    {
        "id": "JN-01",
        "question": "Show the customer name and order date for every order.",
        "difficulty_category": "join",
        "reference_sql": "SELECT c.name, o.order_date, o.status FROM customers c JOIN orders o ON c.id = o.customer_id ORDER BY o.id;"
    },
    {
        "id": "JN-02",
        "question": "List all order items along with their product names.",
        "difficulty_category": "join",
        "reference_sql": "SELECT oi.id, p.name, oi.quantity, oi.unit_price FROM order_items oi JOIN products p ON oi.product_id = p.id ORDER BY oi.id;"
    },
    {
        "id": "JN-03",
        "question": "Show all orders placed by customers from Germany.",
        "difficulty_category": "join",
        "reference_sql": "SELECT o.id, c.name, c.country, o.status, o.order_date FROM orders o JOIN customers c ON o.customer_id = c.id WHERE c.country = 'Germany';"
    },
    {
        "id": "JN-04",
        "question": "List customer names and the names of all products they ordered.",
        "difficulty_category": "join",
        "reference_sql": "SELECT DISTINCT c.name AS customer_name, p.name AS product_name FROM customers c JOIN orders o ON c.id = o.customer_id JOIN order_items oi ON o.id = oi.order_id JOIN products p ON oi.product_id = p.id ORDER BY c.name, p.name;"
    },
    {
        "id": "JN-05",
        "question": "Show all delivered orders with the customer's email and order id.",
        "difficulty_category": "join",
        "reference_sql": "SELECT o.id AS order_id, c.email, o.status FROM orders o JOIN customers c ON o.customer_id = c.id WHERE o.status = 'delivered' ORDER BY o.id;"
    },
    {
        "id": "JN-06",
        "question": "Find all products ordered by customer Ananya Rao.",
        "difficulty_category": "join",
        "reference_sql": "SELECT DISTINCT p.name, oi.unit_price FROM customers c JOIN orders o ON c.id = o.customer_id JOIN order_items oi ON o.id = oi.order_id JOIN products p ON oi.product_id = p.id WHERE c.name = 'Ananya Rao';"
    },
    {
        "id": "JN-07",
        "question": "Show customer names alongside their order IDs, including customers with no orders.",
        "difficulty_category": "join",
        "reference_sql": "SELECT c.name, o.id AS order_id FROM customers c LEFT JOIN orders o ON c.id = o.customer_id ORDER BY c.id, o.id;"
    },
    {
        "id": "JN-08",
        "question": "Which customers ordered a Wireless Mouse?",
        "difficulty_category": "join",
        "reference_sql": "SELECT DISTINCT c.name, c.email FROM customers c JOIN orders o ON c.id = o.customer_id JOIN order_items oi ON o.id = oi.order_id JOIN products p ON oi.product_id = p.id WHERE p.name = 'Wireless Mouse' ORDER BY c.name;"
    },
    {
        "id": "JN-09",
        "question": "List each order with customer city and total item quantity in that order.",
        "difficulty_category": "join",
        "reference_sql": "SELECT o.id AS order_id, c.city, SUM(oi.quantity) AS total_quantity FROM orders o JOIN customers c ON o.customer_id = c.id JOIN order_items oi ON o.id = oi.order_id GROUP BY o.id, c.city ORDER BY o.id;"
    },

    # --------------------------------------------------------------------------
    # 3. Aggregation with GROUP BY (9 questions)
    # --------------------------------------------------------------------------
    {
        "id": "AGG-01",
        "question": "How many customers are there in each country?",
        "difficulty_category": "aggregation_group_by",
        "reference_sql": "SELECT country, COUNT(*) AS customer_count FROM customers GROUP BY country ORDER BY customer_count DESC, country;"
    },
    {
        "id": "AGG-02",
        "question": "What is the average price of products in each category?",
        "difficulty_category": "aggregation_group_by",
        "reference_sql": "SELECT category, ROUND(AVG(price), 2) AS avg_price FROM products GROUP BY category ORDER BY avg_price DESC;"
    },
    {
        "id": "AGG-03",
        "question": "How many orders have been placed in each status?",
        "difficulty_category": "aggregation_group_by",
        "reference_sql": "SELECT status, COUNT(*) AS order_count FROM orders GROUP BY status ORDER BY order_count DESC;"
    },
    {
        "id": "AGG-04",
        "question": "What is the total revenue for each product category from all order items?",
        "difficulty_category": "aggregation_group_by",
        "reference_sql": "SELECT p.category, SUM(oi.quantity * oi.unit_price) AS total_revenue FROM order_items oi JOIN products p ON oi.product_id = p.id GROUP BY p.category ORDER BY total_revenue DESC;"
    },
    {
        "id": "AGG-05",
        "question": "Calculate the total spend for each customer.",
        "difficulty_category": "aggregation_group_by",
        "reference_sql": "SELECT c.id, c.name, SUM(oi.quantity * oi.unit_price) AS total_spend FROM customers c JOIN orders o ON c.id = o.customer_id JOIN order_items oi ON o.id = oi.order_id GROUP BY c.id, c.name ORDER BY total_spend DESC;"
    },
    {
        "id": "AGG-06",
        "question": "Find the total quantity sold for each product.",
        "difficulty_category": "aggregation_group_by",
        "reference_sql": "SELECT p.name, SUM(oi.quantity) AS total_units_sold FROM products p JOIN order_items oi ON p.id = oi.product_id GROUP BY p.name ORDER BY total_units_sold DESC;"
    },
    {
        "id": "AGG-07",
        "question": "Which customers have placed more than 1 order?",
        "difficulty_category": "aggregation_group_by",
        "reference_sql": "SELECT c.id, c.name, COUNT(o.id) AS order_count FROM customers c JOIN orders o ON c.id = o.customer_id GROUP BY c.id, c.name HAVING COUNT(o.id) > 1 ORDER BY order_count DESC;"
    },
    {
        "id": "AGG-08",
        "question": "What is the total value of stock available per category?",
        "difficulty_category": "aggregation_group_by",
        "reference_sql": "SELECT category, SUM(price * stock_qty) AS inventory_value FROM products GROUP BY category ORDER BY inventory_value DESC;"
    },
    {
        "id": "AGG-09",
        "question": "Calculate the total spending per order.",
        "difficulty_category": "aggregation_group_by",
        "reference_sql": "SELECT order_id, SUM(quantity * unit_price) AS order_total FROM order_items GROUP BY order_id ORDER BY order_id;"
    },

    # --------------------------------------------------------------------------
    # 4. Subquery / CTE (8 questions)
    # --------------------------------------------------------------------------
    {
        "id": "SUB-01",
        "question": "Which products have a price strictly greater than the average product price?",
        "difficulty_category": "subquery_cte",
        "reference_sql": "SELECT id, name, price FROM products WHERE price > (SELECT AVG(price) FROM products) ORDER BY price DESC;"
    },
    {
        "id": "SUB-02",
        "question": "Find customers who have placed exactly one order.",
        "difficulty_category": "subquery_cte",
        "reference_sql": "SELECT id, name, email FROM customers WHERE id IN (SELECT customer_id FROM orders GROUP BY customer_id HAVING COUNT(*) = 1) ORDER BY id;"
    },
    {
        "id": "SUB-03",
        "question": "Find the customer who has the highest total order spend.",
        "difficulty_category": "subquery_cte",
        "reference_sql": "WITH customer_spend AS (SELECT c.id, c.name, SUM(oi.quantity * oi.unit_price) AS total FROM customers c JOIN orders o ON c.id = o.customer_id JOIN order_items oi ON o.id = oi.order_id GROUP BY c.id, c.name) SELECT name, total FROM customer_spend ORDER BY total DESC LIMIT 1;"
    },
    {
        "id": "SUB-04",
        "question": "Which products have been ordered in more than one distinct order?",
        "difficulty_category": "subquery_cte",
        "reference_sql": "SELECT id, name, category, price FROM products WHERE id IN (SELECT product_id FROM order_items GROUP BY product_id HAVING COUNT(DISTINCT order_id) > 1) ORDER BY id;"
    },
    {
        "id": "SUB-05",
        "question": "List all orders whose total spend exceeds 2000.",
        "difficulty_category": "subquery_cte",
        "reference_sql": "SELECT order_id, total FROM (SELECT order_id, SUM(quantity * unit_price) AS total FROM order_items GROUP BY order_id) t WHERE total > 2000 ORDER BY total DESC;"
    },
    {
        "id": "SUB-06",
        "question": "Show customers whose total spend is above the average customer spend.",
        "difficulty_category": "subquery_cte",
        "reference_sql": "WITH customer_totals AS (SELECT c.id, c.name, SUM(oi.quantity * oi.unit_price) AS spend FROM customers c JOIN orders o ON c.id = o.customer_id JOIN order_items oi ON o.id = oi.order_id GROUP BY c.id, c.name) SELECT name, spend FROM customer_totals WHERE spend > (SELECT AVG(spend) FROM customer_totals) ORDER BY spend DESC;"
    },
    {
        "id": "SUB-07",
        "question": "Which categories have more than 2 products?",
        "difficulty_category": "subquery_cte",
        "reference_sql": "SELECT category, cnt FROM (SELECT category, COUNT(*) AS cnt FROM products GROUP BY category) sub WHERE cnt > 2 ORDER BY cnt DESC;"
    },
    {
        "id": "SUB-08",
        "question": "Find the details of the most recently placed order.",
        "difficulty_category": "subquery_cte",
        "reference_sql": "SELECT * FROM orders WHERE order_date = (SELECT MAX(order_date) FROM orders) ORDER BY id DESC LIMIT 1;"
    },

    # --------------------------------------------------------------------------
    # 5. Window Function (8 questions)
    # --------------------------------------------------------------------------
    {
        "id": "WIN-01",
        "question": "Rank all products by price within their category from highest to lowest.",
        "difficulty_category": "window_function",
        "reference_sql": "SELECT id, name, category, price, DENSE_RANK() OVER (PARTITION BY category ORDER BY price DESC) as price_rank FROM products ORDER BY category, price_rank;"
    },
    {
        "id": "WIN-02",
        "question": "Assign a row number to each customer ordered by their sign up date.",
        "difficulty_category": "window_function",
        "reference_sql": "SELECT id, name, signed_up_at, ROW_NUMBER() OVER (ORDER BY signed_up_at ASC) as signup_order FROM customers ORDER BY signup_order;"
    },
    {
        "id": "WIN-03",
        "question": "Show each product alongside the average price of products in its category.",
        "difficulty_category": "window_function",
        "reference_sql": "SELECT id, name, category, price, ROUND(AVG(price) OVER (PARTITION BY category), 2) AS category_avg_price FROM products ORDER BY category, id;"
    },
    {
        "id": "WIN-04",
        "question": "Rank orders chronologically for each customer.",
        "difficulty_category": "window_function",
        "reference_sql": "SELECT id, customer_id, order_date, status, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date ASC, id ASC) AS order_seq FROM orders ORDER BY customer_id, order_seq;"
    },
    {
        "id": "WIN-05",
        "question": "For each order item, show the running total of quantity ordered across all order items ordered by id.",
        "difficulty_category": "window_function",
        "reference_sql": "SELECT id, order_id, product_id, quantity, SUM(quantity) OVER (ORDER BY id ASC) as running_qty FROM order_items ORDER BY id;"
    },
    {
        "id": "WIN-06",
        "question": "Find the difference between each product's price and the most expensive product in its category.",
        "difficulty_category": "window_function",
        "reference_sql": "SELECT name, category, price, (MAX(price) OVER (PARTITION BY category) - price) AS diff_from_max FROM products ORDER BY category, price DESC;"
    },
    {
        "id": "WIN-07",
        "question": "Rank all customers based on their total spend.",
        "difficulty_category": "window_function",
        "reference_sql": "WITH customer_spend AS (SELECT c.name, SUM(oi.quantity * oi.unit_price) AS spend FROM customers c JOIN orders o ON c.id = o.customer_id JOIN order_items oi ON o.id = oi.order_id GROUP BY c.name) SELECT name, spend, RANK() OVER (ORDER BY spend DESC) AS spend_rank FROM customer_spend ORDER BY spend_rank;"
    },

    # --------------------------------------------------------------------------
    # 6. Ambiguous Wording (8 questions)
    # --------------------------------------------------------------------------
    {
        "id": "AMB-01",
        "question": "Who are our top customers?",
        "difficulty_category": "ambiguous_wording",
        "reference_sql": "SELECT c.name, SUM(oi.quantity * oi.unit_price) AS total_spend FROM customers c JOIN orders o ON c.id = o.customer_id JOIN order_items oi ON o.id = oi.order_id GROUP BY c.name ORDER BY total_spend DESC LIMIT 5;"
    },
    {
        "id": "AMB-02",
        "question": "What's selling best?",
        "difficulty_category": "ambiguous_wording",
        "reference_sql": "SELECT p.name, SUM(oi.quantity) AS total_sold FROM products p JOIN order_items oi ON p.id = oi.product_id GROUP BY p.name ORDER BY total_sold DESC LIMIT 3;"
    },
    {
        "id": "AMB-03",
        "question": "Are there any inventory issues?",
        "difficulty_category": "ambiguous_wording",
        "reference_sql": "SELECT id, name, category, stock_qty FROM products WHERE stock_qty < 30 ORDER BY stock_qty ASC;"
    },
    {
        "id": "AMB-04",
        "question": "Show our revenue numbers.",
        "difficulty_category": "ambiguous_wording",
        "reference_sql": "SELECT SUM(quantity * unit_price) AS total_revenue FROM order_items;"
    },
    {
        "id": "AMB-05",
        "question": "Give me a breakdown of customer geography.",
        "difficulty_category": "ambiguous_wording",
        "reference_sql": "SELECT country, COUNT(*) AS count FROM customers GROUP BY country ORDER BY count DESC;"
    },
    {
        "id": "AMB-06",
        "question": "Which orders went bad?",
        "difficulty_category": "ambiguous_wording",
        "reference_sql": "SELECT o.id, c.name, o.status, o.order_date FROM orders o JOIN customers c ON o.customer_id = c.id WHERE o.status = 'cancelled' ORDER BY o.id;"
    },
    {
        "id": "AMB-07",
        "question": "Who joined us most recently?",
        "difficulty_category": "ambiguous_wording",
        "reference_sql": "SELECT id, name, signed_up_at FROM customers ORDER BY signed_up_at DESC LIMIT 3;"
    },
    {
        "id": "AMB-08",
        "question": "What is our average order value?",
        "difficulty_category": "ambiguous_wording",
        "reference_sql": "WITH order_totals AS (SELECT order_id, SUM(quantity * unit_price) AS total FROM order_items GROUP BY order_id) SELECT ROUND(AVG(total), 2) AS aov FROM order_totals;"
    }
]


async def verify_and_export_questions():
    eval_db_url = get_eval_db_url()
    print(f"[generate_questions] Connecting to eval DB: text2sql_eval...", flush=True)
    conn = await asyncpg.connect(eval_db_url)
    
    verified_questions = []
    failed = []

    print(f"[generate_questions] Validating {len(QUESTIONS_DATA)} reference queries against text2sql_eval...", flush=True)

    for i, q in enumerate(QUESTIONS_DATA, 1):
        qid = q["id"]
        sql = q["reference_sql"]
        print(f"[{i}/{len(QUESTIONS_DATA)}] Checking {qid}...", flush=True)
        try:
            rows = await conn.fetch(sql)
            count = len(rows)
            if count == 0:
                print(f"[WARNING] Question {qid} returned 0 rows! Reference SQL: {sql}", flush=True)
                failed.append((qid, "Returned 0 rows"))
            else:
                verified_questions.append({
                    "id": qid,
                    "question": q["question"],
                    "difficulty_category": q["difficulty_category"],
                    "reference_sql": sql,
                    "row_count": count
                })
        except Exception as e:
            print(f"[ERROR] Question {qid} failed execution: {e}", flush=True)
            failed.append((qid, str(e)))

    await conn.close()

    if failed:
        print(f"\n[ERROR] {len(failed)} queries failed verification:", flush=True)
        for fid, reason in failed:
            print(f"  - {fid}: {reason}", flush=True)
        sys.exit(1)

    # Export to questions.csv
    out_dir = Path(__file__).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_file = out_dir / "questions.csv"

    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "question", "difficulty_category", "reference_sql"])
        for q in verified_questions:
            writer.writerow([q["id"], q["question"], q["difficulty_category"], q["reference_sql"]])

    print(f"\n[SUCCESS] All {len(verified_questions)} reference queries successfully verified!", flush=True)
    print(f"[SUCCESS] Exported benchmark questions to: {csv_file}", flush=True)

    # Print distribution
    cats = {}
    for q in verified_questions:
        cats[q["difficulty_category"]] = cats.get(q["difficulty_category"], 0) + 1
    print("\nCategory Distribution:", flush=True)
    for cat, count in cats.items():
        print(f"  • {cat}: {count} questions", flush=True)


if __name__ == "__main__":
    asyncio.run(verify_and_export_questions())
