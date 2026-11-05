"""
Robustness & Determinism Evaluation Runner.
Evaluates:
1. Paraphrase Consistency: Rephrases 10 canonical questions 5 different ways each.
   Tests whether semantic intent yields the same database result set.
2. Identical Repeat Stability: Runs each of the 10 original questions 5 times at temp=0.0.
   Measures lexical variance (exact SQL text identity) vs semantic variance (result set identity).
   Provides the empirical answer to whether the LLM Text-to-SQL pipeline is truly "deterministic".

Outputs:
- eval/results/robustness_results.json
"""

import os
import sys
import json
import time
import asyncio
from pathlib import Path
from collections import Counter
from typing import List, Dict, Any

eval_root = Path(__file__).resolve().parent.parent
project_root = eval_root.parent
backend_path = project_root / "backend"

if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))
if str(eval_root) not in sys.path:
    sys.path.insert(0, str(eval_root))

from setup_eval_db import get_eval_db_url
from schema_service import introspect_database_schema
from nvidia_client import generate_sql_from_question
from sql_guard import validate_sql_safety, SQLSafetyError
from accuracy.run_accuracy import compare_result_sets
import asyncpg


PARAPHRASE_DATASET = [
    {
        "id": "ROB-01",
        "original": "List all customers who live in India.",
        "paraphrases": [
            "Show me every customer located in India.",
            "Which customers are based in India?",
            "Get all customer records from India.",
            "Give me the list of Indian customers.",
            "Display all clients living in India."
        ]
    },
    {
        "id": "ROB-02",
        "original": "Find all products in the Electronics category.",
        "paraphrases": [
            "Which items belong to the Electronics department?",
            "Show me every product categorized under Electronics.",
            "List products where category is Electronics.",
            "Give me all electronics items in catalog.",
            "What products are sold as Electronics?"
        ]
    },
    {
        "id": "ROB-03",
        "original": "Show all orders that have been delivered.",
        "paraphrases": [
            "List every order with status delivered.",
            "Which orders have successfully reached delivery?",
            "Give me all delivered customer orders.",
            "Display orders where delivery is completed.",
            "Show orders whose state is delivered."
        ]
    },
    {
        "id": "ROB-04",
        "original": "Show the customer name and order date for every order.",
        "paraphrases": [
            "List all orders with their date and who bought them.",
            "Give me customer names along with the order placement dates.",
            "For each order, display the name of the customer and the date.",
            "Show who placed each order and on what date.",
            "Output order dates mapped to customer names."
        ]
    },
    {
        "id": "ROB-05",
        "original": "Show all orders placed by customers from Germany.",
        "paraphrases": [
            "Which orders came from clients in Germany?",
            "List orders made by German customers.",
            "Show orders where the customer resides in Germany.",
            "Find all purchases made by users from Germany.",
            "Get order history for customers located in Germany."
        ]
    },
    {
        "id": "ROB-06",
        "original": "How many customers are there in each country?",
        "paraphrases": [
            "Count the number of customers grouped by country.",
            "What is the customer distribution across countries?",
            "Show total customer count per nation.",
            "Give me customer numbers broken down by country.",
            "Display count of customers by country."
        ]
    },
    {
        "id": "ROB-07",
        "original": "How many orders have been placed in each status?",
        "paraphrases": [
            "Count total orders grouped by their status.",
            "Give me order volume per status.",
            "Show the distribution of order statuses.",
            "What is the number of orders in delivered, pending, shipped, cancelled?",
            "Group orders by status and show counts."
        ]
    },
    {
        "id": "ROB-08",
        "original": "Calculate the total spend for each customer.",
        "paraphrases": [
            "How much money has each customer spent in total?",
            "Find cumulative order spend for every customer.",
            "Give me total expenditure grouped by customer name.",
            "What is the lifetime spending of each customer?",
            "Sum the total order item values per customer."
        ]
    },
    {
        "id": "ROB-09",
        "original": "Which products have a price strictly greater than the average product price?",
        "paraphrases": [
            "Find products that cost more than the average item price.",
            "List items priced above the catalog average.",
            "Which products exceed mean product price?",
            "Show products whose unit cost is higher than average.",
            "Filter products having price above overall average price."
        ]
    },
    {
        "id": "ROB-10",
        "original": "Who are our top customers?",
        "paraphrases": [
            "Show me the highest spending customers.",
            "Which customers have generated the most revenue?",
            "List the best customers by total sales.",
            "Who are the top spenders among all clients?",
            "Display the customers with maximum total purchases."
        ]
    }
]


async def execute_query(conn, sql: str):
    """Executes a SQL query in a read-only transaction with 5s timeout."""
    async with conn.transaction(readonly=True):
        await conn.execute("SET LOCAL statement_timeout = 5000")
        return await conn.fetch(sql)


async def run_robustness_evaluation(sleep_sec: float = 1.0):
    results_dir = eval_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    eval_db_url = get_eval_db_url()
    pool = await asyncpg.create_pool(eval_db_url, min_size=1, max_size=5)

    schema_text, _ = await introspect_database_schema(pool)

    print("=" * 70, flush=True)
    print("QUERITY ROBUSTNESS & DETERMINISM BENCHMARK", flush=True)
    print(f"Testing 10 Questions x 5 Paraphrases + 5 Repeat Identical Runs", flush=True)
    print("=" * 70, flush=True)

    paraphrase_matches = 0
    total_paraphrase_tests = 0
    repeat_lexical_matches = 0
    repeat_semantic_matches = 0
    total_repeat_runs = 0

    detailed_results = []

    for item in PARAPHRASE_DATASET:
        qid = item["id"]
        original_q = item["original"]
        paraphrases = item["paraphrases"]

        print(f"\n[{qid}] Original: \"{original_q}\"", flush=True)

        # Baseline execution of original question
        try:
            base_sql = await generate_sql_from_question(original_q, schema_text)
            safe_base_sql = validate_sql_safety(base_sql)
            async with pool.acquire() as conn:
                base_rows = await execute_query(conn, safe_base_sql)
        except Exception as e:
            print(f"  [ERROR] Baseline query failed: {e}", flush=True)
            base_sql = ""
            base_rows = []

        # ----------------------------------------------------------------------
        # Test 1: Repeat identical runs (Determinism Check)
        # ----------------------------------------------------------------------
        repeat_sqls = []
        repeat_same_text = 0
        repeat_same_data = 0

        for r_idx in range(5):
            await asyncio.sleep(sleep_sec)
            try:
                rep_sql = await generate_sql_from_question(original_q, schema_text)
                safe_rep_sql = validate_sql_safety(rep_sql)
                repeat_sqls.append(safe_rep_sql)
                
                # Check lexical identity
                if safe_rep_sql.strip() == safe_base_sql.strip():
                    repeat_same_text += 1

                # Check semantic identity
                async with pool.acquire() as conn:
                    rep_rows = await execute_query(conn, safe_rep_sql)
                if compare_result_sets(rep_rows, base_rows, safe_base_sql):
                    repeat_same_data += 1
            except Exception as e:
                repeat_sqls.append(f"ERROR: {e}")

        total_repeat_runs += 5
        repeat_lexical_matches += repeat_same_text
        repeat_semantic_matches += repeat_same_data
        print(f"  → Repeat Identical Runs: {repeat_same_text}/5 identical SQL text, {repeat_same_data}/5 identical data", flush=True)

        # ----------------------------------------------------------------------
        # Test 2: Paraphrase consistency
        # ----------------------------------------------------------------------
        para_results = []
        for p_idx, para in enumerate(paraphrases, 1):
            await asyncio.sleep(sleep_sec)
            para_matched = False
            para_sql = ""
            try:
                p_sql = await generate_sql_from_question(para, schema_text)
                safe_p_sql = validate_sql_safety(p_sql)
                para_sql = safe_p_sql
                async with pool.acquire() as conn:
                    p_rows = await execute_query(conn, safe_p_sql)
                para_matched = compare_result_sets(p_rows, base_rows, safe_base_sql)
            except Exception as e:
                para_sql = f"ERROR: {e}"
                para_matched = False

            if para_matched:
                paraphrase_matches += 1
            total_paraphrase_tests += 1

            para_results.append({
                "paraphrase": para,
                "generated_sql": para_sql,
                "matches_baseline_data": para_matched
            })

        matched_count = sum(1 for p in para_results if p["matches_baseline_data"])
        print(f"  → Paraphrase Consistency: {matched_count}/5 matched baseline result set", flush=True)

        detailed_results.append({
            "id": qid,
            "original_question": original_q,
            "baseline_sql": safe_base_sql,
            "repeat_runs": {
                "identical_text_count": repeat_same_text,
                "identical_data_count": repeat_same_data,
                "total_runs": 5
            },
            "paraphrase_tests": para_results
        })

    await pool.close()

    # Compute Final Percentages
    para_consistency_pct = (paraphrase_matches / total_paraphrase_tests * 100) if total_paraphrase_tests > 0 else 0
    lexical_det_pct = (repeat_lexical_matches / total_repeat_runs * 100) if total_repeat_runs > 0 else 0
    semantic_det_pct = (repeat_semantic_matches / total_repeat_runs * 100) if total_repeat_runs > 0 else 0

    print("\n" + "=" * 70, flush=True)
    print("ROBUSTNESS & DETERMINISM SUMMARY", flush=True)
    print("=" * 70, flush=True)
    print(f"Paraphrase Result Consistency: {paraphrase_matches}/{total_paraphrase_tests} ({para_consistency_pct:.1f}%)", flush=True)
    print(f"Lexical Determinism (Repeat):  {repeat_lexical_matches}/{total_repeat_runs} ({lexical_det_pct:.1f}%)", flush=True)
    print(f"Semantic Determinism (Repeat): {repeat_semantic_matches}/{total_repeat_runs} ({semantic_det_pct:.1f}%)", flush=True)

    summary_file = results_dir / "robustness_results.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump({
            "paraphrase_consistency_pct": round(para_consistency_pct, 2),
            "paraphrase_matches": paraphrase_matches,
            "total_paraphrases": total_paraphrase_tests,
            "lexical_determinism_pct": round(lexical_det_pct, 2),
            "semantic_determinism_pct": round(semantic_det_pct, 2),
            "total_repeat_runs": total_repeat_runs,
            "details": detailed_results
        }, f, indent=2)

    print(f"[robustness] Detailed results saved to: {summary_file}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Robustness Evaluation")
    parser.add_argument("--sleep", type=float, default=1.0, help="Seconds to sleep between calls")
    args = parser.parse_args()

    import nvidia_client
    env_f = Path(nvidia_client.__file__).parent / ".env"
    if env_f.exists():
        nvidia_client._last_env_mtime = env_f.stat().st_mtime
    os.environ["GEMINI_MODEL"] = "gemini-2.5-flash"
    os.environ["GOOGLE_MODEL"] = "gemini-2.5-flash"

    asyncio.run(run_robustness_evaluation(sleep_sec=args.sleep))
