"""
Evaluation Runner for Text-to-SQL Accuracy.
Exclusively uses Google Gemini + Neon PostgreSQL.

Pipeline:
1. Dynamic Schema Introspection (schema_service.py)
2. Google Gemini Generation (nvidia_client.py)
3. SQL Guard Validation (sql_guard.py)
4. PostgreSQL Read-Only Transaction Execution (5s timeout)
5. Semantic Multiset Equivalence Evaluation

Outputs:
- eval/results/accuracy_results.csv
- eval/results/accuracy_summary.json
"""

import os
import sys
import csv
import json
import time
import asyncio
import argparse
from pathlib import Path
from collections import Counter
from decimal import Decimal
import datetime
from typing import Any, List, Tuple

# Path configuration
eval_root = Path(__file__).resolve().parent.parent
project_root = eval_root.parent
backend_path = project_root / "backend"

if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))
if str(eval_root) not in sys.path:
    sys.path.insert(0, str(eval_root))

from setup_eval_db import get_eval_db_url
from schema_service import introspect_database_schema
from nvidia_client import generate_sql_from_question, NVIDIAGenerationError, NVIDIARateLimitError, get_active_provider_info
from sql_guard import validate_sql_safety, SQLSafetyError
import asyncpg


# ------------------------------------------------------------------------------
# Result Normalization & Semantic Multiset Equivalence
# ------------------------------------------------------------------------------
def normalize_cell(val: Any) -> Any:
    """Normalizes cell types for order/type-insensitive semantic equivalence."""
    if val is None:
        return None
    if isinstance(val, (Decimal, float)):
        return round(float(val), 2)
    if isinstance(val, int):
        return int(val)
    if isinstance(val, (datetime.date, datetime.datetime)):
        return val.strftime("%Y-%m-%d")
    if isinstance(val, str):
        return val.strip().lower()
    return str(val).strip().lower()


def row_to_tuple(row: asyncpg.Record) -> Tuple[Any, ...]:
    """Converts asyncpg Record to a tuple of normalized values, ignoring column names/aliases."""
    return tuple(normalize_cell(val) for val in row.values())


def compare_result_sets(gen_rows: List[asyncpg.Record], ref_rows: List[asyncpg.Record], ref_sql: str) -> bool:
    """
    Compares two result sets:
    1. If ref_sql contains ORDER BY ... LIMIT: evaluates strict row ordering.
    2. Otherwise: evaluates multiset equivalence (Counter), ignoring row order.
    """
    if len(gen_rows) != len(ref_rows):
        return False

    if not gen_rows and not ref_rows:
        return True

    # Check column count
    if len(list(gen_rows[0].keys())) != len(list(ref_rows[0].keys())):
        return False

    gen_tuples = [row_to_tuple(r) for r in gen_rows]
    ref_tuples = [row_to_tuple(r) for r in ref_rows]

    is_strict_order = "ORDER BY" in ref_sql.upper() and "LIMIT" in ref_sql.upper()

    if is_strict_order:
        return gen_tuples == ref_tuples
    else:
        return Counter(gen_tuples) == Counter(ref_tuples)


def classify_failure(
    guard_passed: bool,
    guard_err: str,
    exec_passed: bool,
    exec_err: str,
    matches_ref: bool,
    gen_sql: str,
    gen_rows_count: int,
    ref_rows_count: int
) -> str:
    """Programmatically classifies failure reasons into standardized taxonomy."""
    if not guard_passed:
        return "blocked_by_guard"

    if not exec_passed:
        err_lower = exec_err.lower()
        if "timeout" in err_lower or "canceling statement" in err_lower:
            return "timeout"
        if "syntax error" in err_lower:
            return "syntax_error"
        if any(w in err_lower for w in ["column", "relation", "table", "does not exist", "missing from-clause"]):
            return "wrong_table_or_column"
        if any(w in err_lower for w in ["group by", "aggregate", "having"]):
            return "wrong_aggregation_or_filter"
        return "syntax_error"

    if not matches_ref:
        if gen_rows_count == 0 and ref_rows_count > 0:
            if "JOIN" in gen_sql.upper():
                return "wrong_join"
            return "wrong_aggregation_or_filter"
        return "wrong_result_valid_sql"

    return "none"


# ------------------------------------------------------------------------------
# Accuracy Evaluation Execution Loop
# ------------------------------------------------------------------------------
async def run_accuracy_evaluation(limit: int = None, sleep_sec: float = 2.0):
    results_dir = eval_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_csv = results_dir / "accuracy_results.csv"

    eval_db_url = get_eval_db_url()
    pool = await asyncpg.create_pool(eval_db_url, min_size=1, max_size=5)

    provider_info = get_active_provider_info()
    print("=" * 70, flush=True)
    print("QUERITY TEXT-TO-SQL ACCURACY BENCHMARK (GEMINI + NEON)", flush=True)
    print(f"Active Provider: {provider_info['provider_name']} ({provider_info['model_name']})", flush=True)
    print(f"Target Database: text2sql_eval", flush=True)
    print("=" * 70, flush=True)

    # 1. Fetch live introspected schema once for this evaluation run
    schema_text, _ = await introspect_database_schema(pool)
    print(f"[accuracy] Introspected live schema ({len(schema_text)} chars)", flush=True)

    # 2. Read benchmark questions
    questions_file = eval_root / "accuracy" / "questions.csv"
    questions = []
    with open(questions_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            questions.append(r)

    if limit and limit > 0:
        questions = questions[:limit]
        print(f"[accuracy] Limited evaluation to first {limit} questions.", flush=True)

    total_questions = len(questions)
    results = []
    llm_calls_count = 0

    print(f"\nStarting benchmark on {total_questions} questions...", flush=True)

    for i, q in enumerate(questions, 1):
        qid = q["id"]
        question_text = q["question"]
        category = q["difficulty_category"]
        ref_sql = q["reference_sql"]

        print(f"\n[{i}/{total_questions}] {qid} ({category}): \"{question_text}\"", flush=True)

        gen_sql = ""
        guard_passed = False
        guard_error = ""
        exec_passed = False
        exec_error = ""
        matches_ref = False
        gen_count = 0
        ref_count = 0
        latency_ms = 0
        failure_reason = "none"

        # Step A: Run LLM generation with rate-limit resiliency
        start_time = time.perf_counter()
        for attempt in range(2):
            try:
                llm_calls_count += 1
                gen_sql = await generate_sql_from_question(question_text, schema_text)
                print(f"  → LLM Generated: {gen_sql.replace(chr(10), ' ')}", flush=True)
                break
            except (NVIDIARateLimitError, Exception) as exc:
                err_s = str(exc).lower()
                if ("quota" in err_s or "429" in err_s or "rate" in err_s) and attempt == 0:
                    print(f"  [RateLimit Pause] Quota hit on {qid}. Sleeping 15s before retry...", flush=True)
                    await asyncio.sleep(15.0)
                    continue
                print(f"  → LLM Error: {exc}", flush=True)
                failure_reason = "llm_error"
                exec_error = str(exc)
                break

        # Step B: Run SQL Guard validation
        if gen_sql and failure_reason == "none":
            try:
                validated_sql = validate_sql_safety(gen_sql)
                guard_passed = True
                gen_sql = validated_sql
            except SQLSafetyError as s_err:
                guard_passed = False
                guard_error = str(s_err)
                print(f"  → Guard Blocked: {guard_error}", flush=True)

        # Step C: Execute against eval database
        if guard_passed and failure_reason == "none":
            # 1. Execute Reference SQL
            try:
                async with pool.acquire() as conn:
                    async with conn.transaction(readonly=True):
                        await conn.execute("SET LOCAL statement_timeout = 5000")
                        ref_rows = await conn.fetch(ref_sql)
                        ref_count = len(ref_rows)
            except Exception as r_exc:
                print(f"  [CRITICAL] Reference SQL failed on {qid}: {r_exc}", flush=True)
                ref_rows = []

            # 2. Execute Generated SQL
            try:
                async with pool.acquire() as conn:
                    async with conn.transaction(readonly=True):
                        await conn.execute("SET LOCAL statement_timeout = 5000")
                        gen_rows = await conn.fetch(gen_sql)
                        gen_count = len(gen_rows)
                        exec_passed = True
            except asyncpg.QueryCanceledError:
                exec_passed = False
                exec_error = "Query timed out after 5000ms"
                print(f"  → DB Execution Timeout", flush=True)
            except Exception as d_exc:
                exec_passed = False
                exec_error = str(d_exc)
                print(f"  → DB Execution Error: {exec_error}", flush=True)

            # Step D: Compare Result Sets
            if exec_passed:
                matches_ref = compare_result_sets(gen_rows, ref_rows, ref_sql)
                print(f"  → Outcome: {'CORRECT (Match)' if matches_ref else 'INCORRECT (Result mismatch)'} [Gen rows: {gen_count}, Ref rows: {ref_count}]", flush=True)

        latency_ms = int((time.perf_counter() - start_time) * 1000)

        # Failure classification
        if failure_reason == "none":
            failure_reason = classify_failure(
                guard_passed=guard_passed,
                guard_err=guard_error,
                exec_passed=exec_passed,
                exec_err=exec_error,
                matches_ref=matches_ref,
                gen_sql=gen_sql,
                gen_rows_count=gen_count,
                ref_rows_count=ref_count
            )

        results.append({
            "id": qid,
            "category": category,
            "question": question_text,
            "generated_sql": gen_sql,
            "reference_sql": ref_sql,
            "guard_passed": guard_passed,
            "guard_error": guard_error,
            "exec_passed": exec_passed,
            "exec_error": exec_error,
            "matches_ref": matches_ref,
            "gen_rows_count": gen_count,
            "ref_rows_count": ref_count,
            "latency_ms": latency_ms,
            "failure_reason": failure_reason
        })

        if sleep_sec > 0:
            await asyncio.sleep(sleep_sec)

    await pool.close()

    # --------------------------------------------------------------------------
    # Output Detailed CSV
    # --------------------------------------------------------------------------
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "id", "category", "question", "guard_passed", "exec_passed",
            "matches_ref", "failure_reason", "latency_ms", "gen_rows_count",
            "ref_rows_count", "generated_sql", "reference_sql", "exec_error", "guard_error"
        ])
        writer.writeheader()
        writer.writerows(results)

    print(f"\n[accuracy] Exported detailed results to: {out_csv}", flush=True)

    # --------------------------------------------------------------------------
    # Summary Metrics Calculation
    # --------------------------------------------------------------------------
    total = len(results)
    correct_count = sum(1 for r in results if r["matches_ref"])
    valid_sql_count = sum(1 for r in results if r["exec_passed"])
    
    # Accurate Guard blocked count: only queries where SQL was generated but rejected by guard
    guard_blocked_count = sum(1 for r in results if r["generated_sql"] and not r["guard_passed"])
    llm_error_count = sum(1 for r in results if r["failure_reason"] == "llm_error")
    completed_generations = total - llm_error_count

    overall_acc = (correct_count / total * 100) if total > 0 else 0
    valid_rate = (valid_sql_count / completed_generations * 100) if completed_generations > 0 else 0
    guard_block_rate = (guard_blocked_count / completed_generations * 100) if completed_generations > 0 else 0

    print("\n" + "=" * 70, flush=True)
    print("EVALUATION SUMMARY RESULTS", flush=True)
    print("=" * 70, flush=True)
    print(f"Total Evaluated Questions: {total}", flush=True)
    print(f"Total LLM Calls Made:    {llm_calls_count}", flush=True)
    print(f"Execution Accuracy:      {correct_count}/{total} ({overall_acc:.1f}%)", flush=True)
    print(f"Valid SQL Rate:          {valid_sql_count}/{completed_generations if completed_generations else total} ({valid_rate:.1f}%)", flush=True)
    print(f"Guard Blocked Rate:      {guard_blocked_count}/{completed_generations if completed_generations else total} ({guard_block_rate:.1f}%)", flush=True)
    if llm_error_count > 0:
        print(f"Upstream API Errors:     {llm_error_count}/{total} ({llm_error_count/total*100:.1f}%)", flush=True)

    print("\n--- Accuracy Per Difficulty Category ---", flush=True)
    categories = sorted(list(set(r["category"] for r in results)))
    cat_summary = {}
    for cat in categories:
        cat_items = [r for r in results if r["category"] == cat]
        c_tot = len(cat_items)
        c_cor = sum(1 for r in cat_items if r["matches_ref"])
        pct = (c_cor / c_tot * 100) if c_tot > 0 else 0
        cat_summary[cat] = {"total": c_tot, "correct": c_cor, "accuracy": pct}
        print(f"  • {cat:22s}: {c_cor}/{c_tot} ({pct:.1f}%)", flush=True)

    print("\n--- Failure Reason Breakdown ---", flush=True)
    failure_counts = Counter(r["failure_reason"] for r in results if not r["matches_ref"])
    for reason, count in failure_counts.most_common():
        pct = (count / total * 100) if total > 0 else 0
        print(f"  • {reason:25s}: {count} ({pct:.1f}%)", flush=True)

    # Save summary JSON for master report
    summary_path = results_dir / "accuracy_summary.json"
    summary_data = {
        "provider": provider_info,
        "total_questions": total,
        "llm_calls_made": llm_calls_count,
        "execution_accuracy_pct": round(overall_acc, 2),
        "correct_count": correct_count,
        "valid_sql_rate_pct": round(valid_rate, 2),
        "guard_blocked_pct": round(guard_block_rate, 2),
        "api_error_count": llm_error_count,
        "category_accuracy": cat_summary,
        "failure_breakdown": dict(failure_counts),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)
    print(f"\n[accuracy] Summary metrics saved to: {summary_path}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Text-to-SQL Accuracy Evaluation")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of questions to evaluate")
    parser.add_argument("--sleep", type=float, default=2.0, help="Seconds to sleep between LLM calls")
    parser.add_argument("--model", type=str, default=None, help="Model name override")
    args = parser.parse_args()

    if args.model:
        os.environ["GEMINI_MODEL"] = args.model

    asyncio.run(run_accuracy_evaluation(limit=args.limit, sleep_sec=args.sleep))
