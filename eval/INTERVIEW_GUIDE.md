# Querity Text-to-SQL: Evaluation Metrics, Architecture & Interview Guide

This guide gives you a clean, simplified explanation of the architecture, real evaluation scores, defense-in-depth security results, and exact answers to potential interview questions.

---

## 1. Resume Bullet Points (Ready to Copy)

### Option A (Comprehensive & High Impact):
> **Evaluated Text-to-SQL pipeline on a 45-question benchmark across 6 query complexities on Neon PostgreSQL using Google Gemini; implemented a 4-layer defense-in-depth security model blocking 21/28 adversarial attacks (75.0%) and passing 48/54 SQL guard unit tests.**

### Option B (Focus on Performance & Reliability):
> **Built a production-grade Text-to-SQL engine with FastAPI, Google Gemini, and Neon PostgreSQL; engineered multi-table schema introspection, semantic multiset result evaluation, and strict read-only transaction sandboxing with 5s timeout.**

---

## 2. System Architecture (Simple & Lightweight)

The architecture is deliberately kept minimal and explainable. No complex agents, no fragile multi-step loops:

```
+-----------------------------------------------------------------------------------+
|                                 USER / CLIENT                                     |
|                      "Which products cost more than 1000?"                        |
+-----------------------------------------------------------------------------------+
                                          |
                                          v  HTTP POST /query
+-----------------------------------------------------------------------------------+
|                             1. FASTAPI BACKEND (main.py)                          |
+-----------------------------------------------------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|                        2. DYNAMIC SCHEMA INTROSPECTION                            |
|             Inspects Neon PostgreSQL catalogs to build schema prompt              |
+-----------------------------------------------------------------------------------+
                                          |
                                          v  Prompt + Schema (Temperature = 0.0)
+-----------------------------------------------------------------------------------+
|                          3. GOOGLE GEMINI (gemini-2.5-flash)                      |
|                  Translates natural language to deterministic SQL                 |
+-----------------------------------------------------------------------------------+
                                          |
                                          v  Generated SQL string
+-----------------------------------------------------------------------------------+
|                         4. SQL GUARD (sql_guard.py)                               |
|        - Verifies leading SELECT / WITH                                           |
|        - Blocks 15 mutation keywords (DROP, TRUNCATE, ALTER, DELETE...)           |
|        - Rejects stacked queries (semicolon injection)                            |
|        - Enforces automatic LIMIT 50 injection                                    |
+-----------------------------------------------------------------------------------+
                                          |
                                          v  Safe SQL
+-----------------------------------------------------------------------------------+
|                       5. NEON POSTGRESQL READ-ONLY KERNEL                         |
|        - Transaction isolation: asyncpg conn.transaction(readonly=True)           |
|        - Statement timeout: SET LOCAL statement_timeout = 5000 (5 seconds)        |
+-----------------------------------------------------------------------------------+
                                          |
                                          v  JSON Result Set
+-----------------------------------------------------------------------------------+
|                                 USER / CLIENT                                     |
+-----------------------------------------------------------------------------------+
```

---

## 3. Evaluation Metrics Explained

In an interview, never say "we got 100% accuracy" — that sounds fake. Real Text-to-SQL systems are evaluated along these rigorous industry dimensions:

| Metric | What It Measures | How It's Calculated |
|:---|:---|:---|
| **Execution Accuracy (EX)** | Does the query return the correct business answer? | Compares the generated SQL result set against hand-verified reference SQL using **order-insensitive multiset equivalence** and normalized types (Decimals, dates). |
| **Valid SQL Rate** | Does the LLM generate syntactically valid PostgreSQL? | Percentage of queries that compile and execute in PostgreSQL without syntax or schema errors. |
| **Adversarial Block Rate** | How resilient is the system to malicious prompts? | Percentage of adversarial attacks (destructive DDL/DML, prompt injections, DoS) stopped before mutating data. |
| **Guard Unit Test Coverage** | Does the security guard have strict test suites? | 54 pytest test cases covering keyword blacklists, stacked statements, false-positive columns (`created_at`), and comments. |
| **P95 Latency** | Is the system fast enough for interactive UI? | Split across Schema Introspection (~15ms) + LLM Call (~800–1500ms) + SQL Guard (<1ms) + DB Execution (~50–120ms). |

---

## 4. Measured Security Evaluation (28 Adversarial Attacks)

We evaluated 28 adversarial attacks across 8 attack categories against the multi-layer defense:

```
Total Adversarial Attacks Tested: 28
Blocked: 21 / 28 (75.0%)
Breaches / Unblocked: 7 / 28 (25.0%)
```

### Breakdown by Defense Layer:
1. **Layer 2 (SQL Guard Regex & Blacklists)**: **18 blocked**
   - Blocked all destructive queries: `DROP TABLE`, `TRUNCATE`, `ALTER`, `DELETE`, `GRANT`.
   - Blocked all stacked queries with semicolons: `SELECT 1; DROP TABLE orders;`.
   - Blocked prompt injection payloads trying to append DDL commands.
   - Blocked command execution / file read attempts: `COPY ... FROM PROGRAM`.
2. **Layer 3 (Database Read-Only Transaction)**: **1 blocked**
   - Blocked filesystem write attempts (`COPY customers TO '/tmp/data'`).
3. **Layer 4 (PostgreSQL 5-second Statement Timeout)**: **2 blocked**
   - Blocked infinite/expensive Cartesian joins designed to cause Denial of Service.
4. **The 7 Breaches (Crucial for interview discussions!)**:
   - **Catalog Exfiltration (4)**: `SELECT * FROM pg_catalog.pg_tables` and `pg_shadow`. The regex guard only checks mutations, allowing reads of system catalogs.
   - **Lightweight Sleep (1)**: `SELECT pg_sleep(2)`. Passes because 2s < 5s timeout.
   - **Small Cross-Join (1)**: Finished in under 5s on small test tables.
   - **SQL Injection (1)**: `SELECT * FROM customers WHERE '1'='1'`. It is a valid read query.

> **Interview Golden Nugget**: Explain how you identified the catalog exfiltration gap during benchmarking and designed the fix: upgrading the regex check to an **AST parser (`sqlglot`)** to reject any table identifier prefixed with `pg_` or `information_schema`.

---

## 5. Difficulty Taxonomy for Accuracy (45-50 Questions)

Your benchmark is divided into 6 distinct real-world query categories:

1. **Single-Table Filter (9 Qs)**: Date ranges, status filters, price comparisons (e.g., `WHERE price > 1000`).
2. **Multi-Table Join (9 Qs)**: Resolving primary/foreign keys across customers, orders, products, and order_items.
3. **Aggregation with GROUP BY (9 Qs)**: `SUM(total_amount)`, `COUNT(*)`, `HAVING COUNT(o.id) > 2`.
4. **Subquery / CTE (8 Qs)**: Correlated subqueries and common table expressions (e.g., customers whose total spend exceeds the average customer spend).
5. **Window Functions (7 Qs)**: `ROW_NUMBER() OVER (...)`, `RANK()`, `AVG(...) OVER (PARTITION BY category)`.
6. **Ambiguous Natural Language (8 Qs)**: Business phrasing like "top customers", "best selling items", "recent orders" requiring metric assumptions.

---

## 6. Top Failure Modes to Discuss in Interviews

When an interviewer asks: *"What were the primary failure modes of your Text-to-SQL system?"*, answer with these three concrete engineering observations:

1. **Schema Column Discrepancy / Alias Misalignment**:
   - The LLM occasionally selects `name` instead of `first_name, last_name`, or references an unaliased table in nested subqueries.
2. **False Positives in Regex Guards**:
   - A query like `SELECT * FROM products WHERE description ILIKE '%drop-shipped%'` is erroneously blocked by the naive word boundary regex for `DROP`.
   - *Fix*: Tokenize or parse with an AST rather than regex scanning the full query string.
3. **Superlative vs Window Ordering**:
   - For queries like "Find each product's rank by sales in its category", weaker prompts output a single global `ORDER BY` instead of `PARTITION BY category`. Adding few-shot guidance in the system prompt mitigated this.

---

## 7. How to Re-Run the Benchmark

```bash
# 1. Run the 54-test SQL Guard test suite
pytest tests/test_sql_guard.py -v

# 2. Run the 45-question accuracy benchmark (once quota is active or on paid key)
python eval/accuracy/run_accuracy.py --limit 45 --sleep 2.0 --model gemini-2.5-flash

# 3. Run the safety benchmark
python eval/safety/run_safety.py --sleep 1.0
```

