# ⚡ Querity — Enterprise Text-to-SQL & Analytical Engine

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115.0+-009688.svg?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Google Gemini](https://img.shields.io/badge/Google%20Gemini-3.5%20Flash--Lite%20|%202.5%20Flash-4285F4.svg?style=flat&logo=google&logoColor=white)](https://ai.google.dev)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Neon%20Serverless%20DB-336791.svg?style=flat&logo=postgresql&logoColor=white)](https://neon.tech)
[![DuckDB](https://img.shields.io/badge/DuckDB-1.1.0+-FFF000.svg?style=flat&logo=duckdb&logoColor=black)](https://duckdb.org)
[![Python](https://img.shields.io/badge/Python-3.10%20|%203.11%20|%203.12%20|%203.14-3776AB.svg?style=flat&logo=python&logoColor=white)](https://python.org)
[![Pytest](https://img.shields.io/badge/Pytest-54%20Unit%20Tests-0A9EDC.svg?style=flat&logo=pytest&logoColor=white)](https://pytest.org)
[![License](https://img.shields.io/badge/License-MIT-blue.svg?style=flat)](LICENSE)

> **Querity** is a lightweight, production-grade Natural Language to SQL (Text-to-SQL) engine and analytical data platform. It converts conversational business questions into deterministic, read-only SQL using **Google Gemini**, enforces a multi-tiered defense-in-depth safety engine, executes queries against **Neon Serverless PostgreSQL**, and embeds an in-memory **DuckDB OLAP engine** backed by a streaming data ingestion pipeline.

---

## 📊 Measured Benchmark Results (Interview & Resume Ready)

The pipeline was evaluated across 45 hand-crafted benchmark queries and 28 adversarial attack vectors on an e-commerce schema hosted on Neon PostgreSQL:

| Metric | Measured Score | What It Verifies |
|:---|:---:|:---|
| **Valid SQL Generation Rate** | **100.0% (45 / 45)** | Every single generated query compiled and executed cleanly in PostgreSQL without syntax or schema errors. |
| **Strict Execution Accuracy** | **82.2% (37 / 45)** | Exact semantic multiset equivalence evaluated against hand-verified reference SQL. |
| **Adversarial Safety Block Rate** | **75.0% (21 / 28)** | Multi-layer defense stopped 21/28 attacks across SQL Guard regex, DB read-only transactions, and 5s timeouts. |
| **SQL Guard Unit Tests** | **48 Passed / 6 xfailed** | 54 comprehensive pytest unit tests covering keyword blacklists, stacked queries, and case insensitivity. |
| **SQL Guard False Positives** | **0.0% (0 / 45)** | Zero benign business queries were falsely blocked. |

### Accuracy by Query Complexity (The 6 Buckets)
* **Single-Table Filters**: **9 / 9 (100.0%)** — Date ranges, case-insensitive ILIKE string searches, numerical thresholds.
* **Multi-Table Joins**: **9 / 9 (100.0%)** — Foreign-key resolutions across customers, orders, products, and order items.
* **Aggregation with GROUP BY**: **8 / 9 (88.9%)** — Multi-table aggregations (`SUM`, `AVG`, `COUNT`) and `HAVING` filters.
* **Window Functions**: **6 / 7 (85.7%)** — `DENSE_RANK()`, `ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...)`.
* **Subqueries & CTEs**: **5 / 8 (62.5%)** — Correlated subqueries and common table expressions.
* **Ambiguous Business Phrasing**: **0 / 3 (0.0%)** — Natural drop demonstrating honest evaluation on undefined business metrics (e.g. "top customers").

---

## 🏛️ System Architecture

The architecture is kept minimal, modular, and explainable:

```
[ User Question ]
       │
       ▼
1. FastAPI Backend (backend/main.py)
       │
       ▼
2. Dynamic Schema Introspection (backend/schema_service.py)
   • In-memory TTL cache (<1ms response)
   • Pulls active tables, column types, and foreign-key join paths from Neon DB
       │
       ▼
3. Google Gemini Engine (backend/nvidia_client.py)
   • Injects live schema context + projection rules (Temperature = 0.0)
   • Automatic exponential backoff on transient 503 high-demand spikes
       │
       ▼
4. SQL Guard (backend/sql_guard.py)
   • Validates query starts with SELECT / WITH
   • Blocks 15 mutation keywords (DROP, TRUNCATE, ALTER, DELETE, GRANT...)
   • Blocks stacked statements (semicolon injection)
   • Enforces automatic LIMIT 50 injection
       │
       ▼
5. Neon PostgreSQL Sandbox
   • Read-only transaction kernel: conn.transaction(readonly=True)
   • Hard statement timeout: SET LOCAL statement_timeout = 5000 (5s max)
       │
       ▼
[ JSON Result Set ──> Browser UI Table & SQL Preview ]
```

---

## 🚀 Quick Start Guide

### 1. Prerequisites
* Python 3.10+ (tested up to Python 3.14)
* A free [Google AI Studio API Key](https://aistudio.google.com/)
* A free [Neon PostgreSQL Database](https://neon.tech/)

### 2. Configuration (`backend/.env`)
Create `backend/.env` with your database URL and Google Gemini API key:
```env
# Neon PostgreSQL Connection
DATABASE_URL=postgresql://user:password@ep-your-host.neon.tech/neondb?sslmode=require

# Google Gemini Configuration
GOOGLE_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.5-flash-lite
LLM_PROVIDER=google
```

### 3. Launch the Application (One Command)
Run the application launcher from the project root:
```bash
python run_app.py
```
This automatically:
1. Starts the FastAPI backend on `http://127.0.0.1:8000`.
2. Connects dynamically to your Neon PostgreSQL database.
3. Opens the Home Screen in your default browser.

---

## 🧪 Evaluation & Benchmark Suite

Querity includes an automated evaluation suite to audit and reproduce all performance numbers:

### Run All Benchmarks in One Command:
```bash
python eval/run_all.py
```

### Run Benchmarks Individually:
* **Accuracy Evaluation (45 questions)**:
  ```bash
  python eval/accuracy/run_accuracy.py --limit 45 --sleep 2.5
  ```
  *Logs per-question outputs to `eval/results/accuracy_results.csv` and summary to `eval/results/accuracy_summary.json`.*

* **Adversarial Safety Evaluation (28 attacks)**:
  ```bash
  python eval/safety/run_safety.py --sleep 1.0
  ```
  *Tests multi-layer defense against destructive DDL/DML, prompt injections, and Cartesian DoS attacks.*

* **SQL Guard Security Unit Tests (54 cases)**:
  ```bash
  pytest tests/test_sql_guard.py -v
  ```

---

## 🛡️ Defense-in-Depth Security Model

Querity protects against malicious inputs using a 4-layer defense model:

| Defense Layer | Mechanism | Attacks Blocked |
|:---|:---|:---:|
| **Layer 1: Prompt Guidelines** | Injects strict read-only guidelines and few-shot canonical examples | Rejects conversational manipulation |
| **Layer 2: SQL Guard** | Regex keyword blacklist, single-statement verification, semicolon rejection, LIMIT injection | **18 / 28 blocked** (All DDL/DML, stacked queries, injections) |
| **Layer 3: Read-Only DB Txn** | PostgreSQL transaction isolation: `conn.transaction(readonly=True)` | **1 / 28 blocked** (Filesystem write attempts like `COPY TO`) |
| **Layer 4: Statement Timeout** | Hardware execution timeout: `SET LOCAL statement_timeout = 5000` | **2 / 28 blocked** (Unindexed Cartesian cross-join DoS) |

---

## 📁 Repository Structure

```
Querity/
├── backend/
│   ├── main.py              # FastAPI application & REST endpoints
│   ├── nvidia_client.py     # Clean Google Gemini LLM text-to-SQL client
│   ├── sql_guard.py         # Defense-in-depth SQL safety validator
│   ├── schema_service.py    # Schema introspection & in-memory cache
│   ├── duckdb_service.py    # In-memory DuckDB analytical engine
│   ├── ingestion.py         # Multi-format streaming data ingestion engine
│   └── .env                 # Environment variables (Neon DB URL, Gemini API Key)
├── frontend/
│   ├── index.html           # Main AI Chat interface (Home screen)
│   ├── app.js               # Frontend application logic
│   ├── playground.html      # DuckDB analytical playground interface
│   ├── playground.js        # Playground interaction logic
│   └── styles.css           # Glassmorphism dark cyberpunk UI theme
├── eval/
│   ├── run_all.py           # Master evaluation runner
│   ├── RESULTS.md           # Official verified benchmark report
│   ├── INTERVIEW_GUIDE.md   # Complete interview talking points & FAQ
│   ├── FINDINGS.md          # Architectural gaps & vulnerability disclosure log
│   ├── accuracy/
│   │   ├── questions.csv    # 50 verified test questions across 6 categories
│   │   └── run_accuracy.py  # Accuracy evaluation runner
│   └── safety/
│       ├── attacks.csv      # 28 adversarial attack prompts
│       └── run_safety.py    # Safety evaluation runner
├── tests/
│   └── test_sql_guard.py    # 54 pytest unit tests for SQL guard
├── run_app.py               # One-click desktop application launcher
└── requirements.txt         # Core dependencies
```

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
