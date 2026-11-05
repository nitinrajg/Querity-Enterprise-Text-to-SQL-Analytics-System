"""
Latency & Performance Profiling Runner for Querity.
Measures granular execution stages across 50 benchmark queries:
1. Schema Introspection Latency (ms)
2. LLM Inference Latency (ms)
3. SQL Guard Regex Validation Latency (ms)
4. PostgreSQL Database Execution Latency (ms)
5. End-to-End Total Pipeline Latency (ms)

Calculates:
- Median (p50)
- 95th Percentile (p95)
- Maximum Latency (max)
- Token usage (or reports NOT MEASURED if provider does not expose tokens)

Outputs:
- eval/results/perf_results.csv
- eval/results/perf_summary.json
"""

import os
import sys
import csv
import json
import time
import asyncio
import numpy as np
from pathlib import Path
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
from nvidia_client import generate_sql_from_question, get_active_provider_info
from sql_guard import validate_sql_safety
import asyncpg


async def run_perf_benchmark(sleep_sec: float = 1.0):
    results_dir = eval_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    eval_db_url = get_eval_db_url()
    pool = await asyncpg.create_pool(eval_db_url, min_size=1, max_size=5)

    questions_file = eval_root / "accuracy" / "questions.csv"
    questions = []
    with open(questions_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            questions.append(r)

    print("=" * 70, flush=True)
    print("QUERITY PERFORMANCE & LATENCY PROFILING (50 QUERIES)", flush=True)
    print(f"Target Database: text2sql_eval", flush=True)
    print("=" * 70, flush=True)

    perf_records = []

    for i, q in enumerate(questions, 1):
        qid = q["id"]
        question_text = q["question"]

        print(f"[{i:02d}/50] Profiling {qid}...", end="", flush=True)

        # Stage 1: Schema Introspection
        t0 = time.perf_counter()
        schema_text, _ = await introspect_database_schema(pool)
        t_schema = (time.perf_counter() - t0) * 1000

        # Stage 2: LLM Inference
        t1 = time.perf_counter()
        try:
            gen_sql = await generate_sql_from_question(question_text, schema_text)
            t_llm = (time.perf_counter() - t1) * 1000
            llm_ok = True
        except Exception as e:
            t_llm = (time.perf_counter() - t1) * 1000
            llm_ok = False
            gen_sql = ""

        # Stage 3: SQL Guard
        t2 = time.perf_counter()
        guard_ok = False
        if gen_sql:
            try:
                safe_sql = validate_sql_safety(gen_sql)
                guard_ok = True
            except Exception:
                safe_sql = ""
        else:
            safe_sql = ""
        t_guard = (time.perf_counter() - t2) * 1000

        # Stage 4: Database Execution
        t3 = time.perf_counter()
        db_ok = False
        row_count = 0
        if guard_ok and safe_sql:
            try:
                async with pool.acquire() as conn:
                    async with conn.transaction(readonly=True):
                        await conn.execute("SET LOCAL statement_timeout = 5000")
                        rows = await conn.fetch(safe_sql)
                        row_count = len(rows)
                        db_ok = True
            except Exception:
                db_ok = False
        t_db = (time.perf_counter() - t3) * 1000

        t_total = t_schema + t_llm + t_guard + t_db
        print(f" Total: {t_total:.1f}ms (Schema: {t_schema:.1f}ms, LLM: {t_llm:.1f}ms, Guard: {t_guard:.2f}ms, DB: {t_db:.1f}ms)", flush=True)

        perf_records.append({
            "id": qid,
            "question": question_text,
            "schema_ms": round(t_schema, 2),
            "llm_ms": round(t_llm, 2),
            "guard_ms": round(t_guard, 4),
            "db_ms": round(t_db, 2),
            "total_ms": round(t_total, 2),
            "row_count": row_count,
            "success": db_ok
        })

        if sleep_sec > 0 and i < len(questions):
            await asyncio.sleep(sleep_sec)

    await pool.close()

    # Save CSV
    out_csv = results_dir / "perf_results.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["id", "question", "schema_ms", "llm_ms", "guard_ms", "db_ms", "total_ms", "row_count", "success"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in perf_records:
            writer.writerow(r)

    # Calculate Percentiles
    def get_stats(arr):
        return {
            "p50": round(float(np.percentile(arr, 50)), 2),
            "p95": round(float(np.percentile(arr, 95)), 2),
            "max": round(float(np.max(arr)), 2),
            "avg": round(float(np.mean(arr)), 2)
        }

    totals = [r["total_ms"] for r in perf_records]
    llms = [r["llm_ms"] for r in perf_records]
    schemas = [r["schema_ms"] for r in perf_records]
    guards = [r["guard_ms"] for r in perf_records]
    dbs = [r["db_ms"] for r in perf_records]

    summary = {
        "total_queries_profiled": len(perf_records),
        "end_to_end_latency_ms": get_stats(totals),
        "llm_inference_ms": get_stats(llms),
        "schema_introspection_ms": get_stats(schemas),
        "sql_guard_ms": get_stats(guards),
        "database_execution_ms": get_stats(dbs),
        "token_usage_cost": "NOT MEASURED (google-genai SDK response object does not return token counters in standard payload)",
    }

    print("\n" + "=" * 70, flush=True)
    print("PERFORMANCE & LATENCY SUMMARY METRICS", flush=True)
    print("=" * 70, flush=True)
    print(f"End-to-End Pipeline Latency:  p50 = {summary['end_to_end_latency_ms']['p50']}ms | p95 = {summary['end_to_end_latency_ms']['p95']}ms | max = {summary['end_to_end_latency_ms']['max']}ms", flush=True)
    print(f"LLM Generation Phase:         p50 = {summary['llm_inference_ms']['p50']}ms | p95 = {summary['llm_inference_ms']['p95']}ms | max = {summary['llm_inference_ms']['max']}ms", flush=True)
    print(f"Schema Introspection Phase:   p50 = {summary['schema_introspection_ms']['p50']}ms | p95 = {summary['schema_introspection_ms']['p95']}ms | max = {summary['schema_introspection_ms']['max']}ms", flush=True)
    print(f"SQL Guard Validation Phase:   p50 = {summary['sql_guard_ms']['p50']}ms | p95 = {summary['sql_guard_ms']['p95']}ms | max = {summary['sql_guard_ms']['max']}ms", flush=True)
    print(f"PostgreSQL Execution Phase:   p50 = {summary['database_execution_ms']['p50']}ms | p95 = {summary['database_execution_ms']['p95']}ms | max = {summary['database_execution_ms']['max']}ms", flush=True)
    print(f"Token & Cost Tracking:        {summary['token_usage_cost']}", flush=True)

    summary_file = results_dir / "perf_summary.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"[perf] Summary saved to: {summary_file}", flush=True)


if __name__ == "__main__":
    import nvidia_client
    env_f = Path(nvidia_client.__file__).parent / ".env"
    if env_f.exists():
        nvidia_client._last_env_mtime = env_f.stat().st_mtime
    os.environ["GEMINI_MODEL"] = "gemini-2.5-flash"
    os.environ["GOOGLE_MODEL"] = "gemini-2.5-flash"

    asyncio.run(run_perf_benchmark())
