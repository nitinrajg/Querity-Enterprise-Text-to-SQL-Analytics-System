# Querity Text-to-SQL: Official Verified Benchmark Report

**Evaluation Date**: November 2026  
**Stack**: Google Gemini (`gemini-3.5-flash-lite`) + Neon PostgreSQL (`text2sql_eval`)  
**Methodology**: Natural Language Question $\to$ Dynamic Schema Introspection $\to$ Google Gemini $\to$ SQL Guard Regex Sandbox $\to$ Neon Read-Only Transaction ($5\text{s}$ Timeout) $\to$ Semantic Multiset Equivalence Evaluation.

---

## 1. Resume Claim Line (Ready to Quote)

> **"Evaluated Text-to-SQL pipeline on a 45-question benchmark across 6 query complexities using Google Gemini on Neon PostgreSQL, achieving an 82.2% execution accuracy (100% on joins & filters, 88.9% on aggregations, 85.7% on window functions) with a 100% valid SQL generation rate, and blocking 21/28 adversarial attacks (75.0%) across a 4-layer defense model."**

---

## 2. Core Benchmark Results

| Metric | Measured Score | Details & Verification |
|:---|:---:|:---|
| **Execution Accuracy** | **82.2% (37 / 45)** | Evaluated strictly against hand-verified reference queries using order-insensitive multiset equivalence. |
| **Valid SQL Rate** | **100.0% (45 / 45)** | Every single generated query compiled and executed cleanly in Neon PostgreSQL without syntax or schema errors. |
| **Guard False Positives** | **0.0% (0 / 45)** | Zero benign business queries were blocked by the guard. |
| **Adversarial Safety Block Rate** | **75.0% (21 / 28)** | Multi-tier defense blocked destructive DDL/DML, stacked queries, injections, and DoS attacks. |
| **SQL Guard Unit Tests** | **48 Passed / 6 xfailed** | 54 comprehensive pytest unit tests covering keywords, comments, and stacked statements. |

---

## 3. Accuracy by Difficulty Category (The 6 Buckets)

| Query Category | Evaluated | Correct | Accuracy | Performance Insight |
|:---|:---:|:---:|:---:|:---|
| **Single-Table Filter** | 9 | 9 | **100.0%** | Flawless date ranges, case-insensitive ILIKE patterns, and numerical thresholds. |
| **Multi-Table Joins** | 9 | 9 | **100.0%** | Perfect resolution of foreign keys across customers, orders, products, and order_items. |
| **Aggregation with GROUP BY** | 9 | 8 | **88.9%** | Exceptional grouping, aggregate calculations (`SUM`, `AVG`, `COUNT`), and `HAVING` filters. |
| **Window Functions** | 7 | 6 | **85.7%** | Successfully executed `DENSE_RANK()`, `ROW_NUMBER() OVER (...)`, and `PARTITION BY`. |
| **Subqueries & CTEs** | 8 | 5 | **62.5%** | Successfully executed complex multi-table CTEs and nested subqueries. |
| **Ambiguous Business Phrasing** | 3 | 0 | **0.0%** | Expected drop: subjective phrases like "top customers" or "best selling" lack explicit business rules. |

---

## 4. Failure Analysis (The 8 Remaining Mismatches)

* **Valid Compilable SQL**: $45 / 45$ ($100.0\%$)
* **Syntax / Relation Errors**: $0 / 45$ ($0.0\%$)
* **Ambiguous Phrasing (3)**: In queries like *"Who are our top customers?"* or *"What's selling best?"*, Gemini sorted by `total_spent` with `LIMIT 5`, whereas the reference query used `LIMIT 3` or different metrics. This highlights the inherent subjectivity of natural language in business intelligence.
* **Complex Subqueries (3)**: In `SUB-02` (customers with exactly 1 order) and `SUB-05` (orders > 2000), Gemini returned matching rows but selected an extra column (e.g. `c.name` vs `order_id`).
* **Window Calculation (1)**: `WIN-06` inverted the subtraction (`p.price - MAX(...)` vs `MAX(...) - p.price`).
* **Filter Threshold (1)**: `SUB-04` used a slightly different grouping threshold.

---

## 5. Security & Safety Evaluation (28 Attacks)

```
Total Adversarial Attacks Tested: 28
Blocked: 21 / 28 (75.0%)
Breaches / Unblocked: 7 / 28 (25.0%)
```

* **Layer 2 (SQL Guard Regex)**: **18 blocked**
  * Blocked `DROP TABLE`, `TRUNCATE`, `ALTER`, `DELETE`, `GRANT`, `REVOKE`
  * Blocked stacked queries with semicolons (`SELECT 1; DROP TABLE orders;`)
  * Blocked prompt injection payloads
  * Blocked command execution: `COPY ... FROM PROGRAM`
* **Layer 3 (Database Read-Only Transaction)**: **1 blocked**
  * Blocked filesystem writes (`COPY customers TO '/tmp/data'`)
* **Layer 4 (PostgreSQL 5-second Statement Timeout)**: **2 blocked**
  * Blocked heavy Cartesian cross-joins designed for Denial of Service
* **Known Gaps (7 allowed through)**:
  * Catalog reads (`SELECT * FROM pg_catalog.pg_tables`, `pg_shadow`) and short sleeps (`SELECT pg_sleep(2)`).
  * *Documented in `eval/FINDINGS.md` as SEC-01 and SEC-02 for future AST parser enhancements.*
