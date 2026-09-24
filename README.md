# ⚡ Queryable — Enterprise Text-to-SQL & Analytical Agent

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115.0+-009688.svg?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Google Gemini](https://img.shields.io/badge/Google%20Gemini-2.5%20Flash-4285F4.svg?style=flat&logo=google&logoColor=white)](https://ai.google.dev)
[![NVIDIA NIM](https://img.shields.io/badge/NVIDIA%20NIM-Llama--3.3--70B-76B900.svg?style=flat&logo=nvidia&logoColor=white)](https://build.nvidia.com)
[![Groq](https://img.shields.io/badge/Groq-LPU%20Inference-F55036.svg?style=flat&logo=groq&logoColor=white)](https://groq.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14+%20|%20Neon%20DB-336791.svg?style=flat&logo=postgresql&logoColor=white)](https://www.postgresql.org)
[![DuckDB](https://img.shields.io/badge/DuckDB-1.1.0+-FFF000.svg?style=flat&logo=duckdb&logoColor=black)](https://duckdb.org)
[![PyArrow](https://img.shields.io/badge/PyArrow-15.0+-D22128.svg?style=flat&logo=apache&logoColor=white)](https://arrow.apache.org)
[![Python](https://img.shields.io/badge/Python-3.10%20|%203.11%20|%203.12-3776AB.svg?style=flat&logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-blue.svg?style=flat)](LICENSE)

> **Queryable** is a production-grade, enterprise-ready Natural Language to SQL (Text-to-SQL) autonomous agent and analytical playground. It translates plain-English business queries into deterministic, read-only SQL across **Multi-LLM providers (Google Gemini, NVIDIA NIM, Groq, and Grok)**, enforces a zero-trust defense-in-depth safety engine, executes queries against live **PostgreSQL (local or Neon DB)**, and integrates an embedded **in-memory DuckDB OLAP engine** backed by a **50GB multi-format streaming ingestion pipeline**.

---

## 📑 Table of Contents

- [Key Features](#-key-features)
- [System Architecture](#-system-architecture)
  - [1. Natural Language to SQL Execution Flow](#1-natural-language-to-sql-execution-flow)
  - [2. 50GB Streaming Multi-Format Ingestion Pipeline](#2-50gb-streaming-multi-format-ingestion-pipeline)
  - [3. Dual-Engine Analytical Architecture (Postgres + DuckDB)](#3-dual-engine-analytical-architecture-postgres--duckdb)
- [Repository Structure](#-repository-structure)
- [Prerequisites](#-prerequisites)
- [Quick Start Guide](#-quick-start-guide)
  - [Step 1: Clone Repository](#step-1-clone-repository)
  - [Step 2: Database Initialization (PostgreSQL)](#step-2-database-initialization-postgresql)
  - [Step 3: Backend Setup](#step-3-backend-setup)
  - [Step 4: Frontend Launch](#step-4-frontend-launch)
- [Configuration Reference (.env)](#-configuration-reference-env)
- [REST API Reference](#-rest-api-reference)
  - [1. GET /api/health](#1-get-apihealth)
  - [2. GET /api/schema](#2-get-apischema)
  - [3. GET /api/tables](#3-get-apitables)
  - [4. GET /api/tables/{table_name}/sample](#4-get-apitablestable_namesample)
  - [5. DELETE /api/tables/{table_name}](#5-delete-apitablestable_name)
  - [6. POST /api/upload](#6-post-apiupload)
  - [7. POST /api/query](#7-post-apiquery)
  - [8. POST /api/playground/duckdb](#8-post-apiplaygroundduckdb)
  - [9. GET /api/playground/tables](#9-get-apiplaygroundtables)
  - [10. POST /api/playground](#10-post-apiplayground)
- [Deep Dive: Core Components](#-deep-dive-core-components)
  - [1. AI & LLM Integration (`nvidia_client.py`)](#1-ai--llm-integration-nvidia_clientpy)
  - [2. Defense-in-Depth SQL Guard (`sql_guard.py`)](#2-defense-in-depth-sql-guard-sql_guardpy)
  - [3. Dynamic Schema Introspection (`schema_service.py`)](#3-dynamic-schema-introspection-schema_servicepy)
  - [4. Multi-Format Streaming Ingestion Engine (`ingestion.py`)](#4-multi-format-streaming-ingestion-engine-ingestionpy)
  - [5. In-Memory DuckDB OLAP Engine (`duckdb_service.py`)](#5-in-memory-duckdb-olap-engine-duckdb_servicepy)
  - [6. High-Precision Frontend & Smart Autocomplete](#6-high-precision-frontend--smart-autocomplete)
- [Supported Ingestion Formats & Specifications](#-supported-ingestion-formats--specifications)
- [Security & Sandbox Model](#-security--sandbox-model)
- [Troubleshooting & FAQ](#-troubleshooting--faq)
- [License](#-license)

---

## ✨ Key Features

| Capability | Description |
| :--- | :--- |
| 🧠 **Autonomous Multi-LLM NL-to-SQL** | Converts conversational questions into deterministic, highly optimized PostgreSQL queries with automatic routing across **Google Gemini** (`gemini-2.5-flash`), **NVIDIA NIM** (`meta/llama-3.3-70b-instruct`), **Groq** (`llama-3.3-70b-versatile`), and **xAI Grok**. |
| 🛡️ **Zero-Trust SQL Guardrails** | Multi-tiered query validation enforcing AST keyword blacklists, single-statement verification, injection prevention, automatic `LIMIT` enforcement, and read-only transactions with timeouts on both PostgreSQL and DuckDB. |
| ⚡ **Dual Engine (Postgres + DuckDB)** | Combines relational persistence in PostgreSQL (local or cloud Neon DB) with blazing-fast columnar OLAP in an in-memory DuckDB engine with automatic table replication. |
| 🚀 **50GB Streaming Ingestion** | Zero-memory-spike disk-chunk streaming supporting Apache Parquet, CSV, TSV, SQL scripts, JSON, JSONL, Excel (.xlsx), and SQLite (.db). |
| 🔍 **Live Catalog Introspection** | Automatically scans PostgreSQL `information_schema`, tracks foreign keys, computes dynamic row counts, and injects updated context into AI system instructions. |
| 🎛️ **Interactive SQL Playground** | Dedicated developer workspace with instant execution, smart SQL autocomplete (keywords, tables, columns, functions), execution profiling, and CSV/JSON export. |
| 🎨 **Liquid Glassmorphism UI** | Modern cyberpunk-inspired dark aesthetic with Google Fonts (`Inter` + `JetBrains Mono`), capsule/pill form factors, dynamic gradient finishes, scanline overlays, and micro-interactions. |
| 🔄 **Zero-Downtime Hot Reloading** | Dynamically reloads `.env` configuration (API keys, models, base URLs) without requiring backend server restarts. |
| 🚦 **Adaptive Rate Limiting & Backoff** | Integrated sliding-window token limiter (60 RPM, 5000 RPD) with exponential backoff on upstream API spikes (429, 503, 504). |

---

## 🏛️ System Architecture

### 1. Natural Language to SQL Execution Flow

```mermaid
sequenceDiagram
    autonumber
    actor User as User / Client
    participant UI as Frontend Web App
    participant API as FastAPI Backend (main.py)
    participant Schema as Schema Service (schema_service.py)
    participant AI as NVIDIA NIM / Groq LLM
    participant Guard as SQL Guard (sql_guard.py)
    participant PG as PostgreSQL Database

    User->>UI: Enter Question ("Top 5 customers by spend")
    UI->>API: POST /api/query {"question": "..."}
    API->>Schema: introspect_database_schema(pool)
    Schema->>PG: Query information_schema & foreign keys
    PG-->>Schema: Table schemas, column types, FK maps
    Schema-->>API: Formatted schema string
    API->>AI: generate_sql_from_question(question, schema)
    Note over AI: System prompt with schema + Zero-temp inference
    AI-->>API: Generated raw SQL string
    API->>Guard: validate_sql_safety(raw_sql)
    Note over Guard: Check single statement, blacklist, append LIMIT
    Guard-->>API: Validated safe SQL
    API->>PG: Execute in READONLY transaction (5s timeout)
    PG-->>API: Query result rows
    API-->>UI: JSON {question, sql, columns, rows, latency_ms}
    UI-->>User: Render SQL receipt + Interactive Data Table
```

---

### 2. 50GB Streaming Multi-Format Ingestion Pipeline

```mermaid
flowchart TD
    A[Incoming Upload: CSV / Parquet / SQL / JSON / Excel / SQLite] --> B[Save Upload Chunk-by-Chunk to Disk\nUPLOAD_CHUNK_SIZE = 4MB]
    B --> C{Format Classifier}
    
    C -->|CSV / TSV| D1[Sniff Delimiter & Header\nInfer PG Types\nStream in Batches: 10,000 rows]
    C -->|Parquet| D2[PyArrow Vectorized Batching\nArrow-to-PG Type Mapping\nBulk Copy]
    C -->|SQL Dump| D3[Read Stream\nDirect Transactional Execution]
    C -->|JSON / JSONL| D4[Extract Key Fields\nDynamic Type Inference\nBulk Copy]
    C -->|Excel .xlsx| D5[OpenPyXL Read-Only Streaming\nType Resolution\nBulk Copy]
    C -->|SQLite .db| D6[Iterate User Tables\nExtract Schema & PRAGMA\nBatch Transfer]
    
    D1 --> E[PostgreSQL Table Created & Populated]
    D2 --> E
    D3 --> E
    D4 --> E
    D5 --> E
    D6 --> E
    
    E --> F[PyArrow Bridge: Sync Table to DuckDB In-Memory Engine]
    F --> G[Live Schema Updated for AI & UI]
```

---

### 3. Dual-Engine Analytical Architecture (Postgres + DuckDB)

```mermaid
graph LR
    subgraph Client Layer
        WebUI[Queryable Web Console]
        PlaygroundUI[DuckDB SQL Playground]
    end

    subgraph FastAPI Application Layer
        Router[API Router / Middleware]
        Limiter[Sliding Window Rate Limiter]
        GuardService[SQL Safety Guard]
        IngestService[Streaming Ingestion Engine]
    end

    subgraph Data & Compute Engines
        PG[(PostgreSQL 14+\nPrimary Relational Storage)]
        Duck[(DuckDB 1.1+\nIn-Memory Columnar OLAP)]
        Arrow[PyArrow Zero-Copy Bridge]
    end

    subgraph AI Intelligence Layer
        NVIDIA[NVIDIA NIM API\nmeta/llama-3.3-70b-instruct]
        Groq[Groq API Fallback\nllama-3.3-70b-versatile]
    end

    WebUI --> Router
    PlaygroundUI --> Router
    Router --> Limiter
    Limiter --> NVIDIA
    Limiter -.-> Groq
    Router --> GuardService
    GuardService --> PG
    IngestService --> PG
    PG --> Arrow
    Arrow --> Duck
    PlaygroundUI --> Duck
```

---

## 📂 Repository Structure

```
t2sql/
├── .gitignore                      # Git ignore rules for secrets, virtualenvs, DBs
├── README.md                       # Complete production documentation
├── backend/
│   ├── .env                        # Local environment secrets (not in git)
│   ├── .env.example                # Template configuration with descriptions
│   ├── duckdb_service.py           # DuckDB OLAP engine, PyArrow sync & metadata
│   ├── ingestion.py                # 50GB multi-format chunked ingestion pipeline
│   ├── main.py                     # FastAPI application, lifecycle, routes & CORS
│   ├── nvidia_client.py            # NVIDIA NIM / Groq wrapper with rate limiting
│   ├── requirements.txt            # Python dependencies with pinned versions
│   ├── schema_service.py           # Database introspection & catalog extraction
│   └── sql_guard.py                # Defense-in-depth SQL safety validator
├── db/
│   └── schema.sql                  # PostgreSQL sample e-commerce DDL & seed data
└── frontend/
    ├── app.js                      # Natural language UI controller & schema drawer
    ├── index.html                  # Main conversational query interface
    ├── playground.html             # DuckDB SQL Playground web console
    ├── playground.js               # SQL editor, autocomplete engine & table manager
    └── styles.css                  # Modern dark-mode styling & design system
```

---

## 📦 Prerequisites

Ensure the following tools and services are installed and running in your environment:

1. **Python 3.10+** (Python 3.11 or 3.12 recommended)
2. **PostgreSQL 14+** (Local service or hosted instances like Supabase, Neon, AWS RDS)
3. **NVIDIA NIM API Key** (from [build.nvidia.com](https://build.nvidia.com)) or **Groq API Key** (from [console.groq.com](https://console.groq.com))
4. Modern Web Browser (Chrome, Firefox, Safari, Edge)

---

## 🚀 Quick Start Guide

### Step 1: Clone Repository

```bash
git clone https://github.com/nitinrajg/Queryable---A-text-2-SQL-agent.git
cd Queryable---A-text-2-SQL-agent
```

### Step 2: Database Initialization (PostgreSQL)

Create a dedicated database and apply the seed schema:

```bash
# Create PostgreSQL database
createdb text2sql_demo

# Seed with e-commerce dataset (customers, products, orders, order_items)
psql -d text2sql_demo -f db/schema.sql
```

*(Optional) Create a dedicated read-only database user for maximum production security:*

```sql
CREATE ROLE readonly_app LOGIN PASSWORD 'secure_password';
GRANT CONNECT ON DATABASE text2sql_demo TO readonly_app;
GRANT USAGE ON SCHEMA public TO readonly_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO readonly_app;
```

---

### Step 3: Backend Setup

Navigate to the `backend` directory, create an isolated virtual environment, and install dependencies:

```bash
cd backend

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On macOS / Linux:
source venv/bin/activate
# On Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# On Windows (Command Prompt):
.\venv\Scripts\activate.bat

# Install dependencies
pip install -r requirements.txt
```

#### Configure Environment Variables

Copy the template configuration and configure your secrets:

```bash
cp .env.example .env
```

Edit `.env` using your preferred text editor:

```env
# PostgreSQL connection string
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/text2sql_demo

# NVIDIA NIM API Key (or Groq Key)
NVIDIA_API_KEY=nvapi-your-key-here

# Selected LLM Model
NVIDIA_MODEL=meta/llama-3.3-70b-instruct
```

#### Start FastAPI Server

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

The API will now be listening at `http://localhost:8000`.  
- **Interactive Swagger Docs:** `http://localhost:8000/docs`
- **Redoc Documentation:** `http://localhost:8000/redoc`

---

### Step 4: Frontend Launch

Because the frontend is built using Vanilla JavaScript and modern CSS, no Node.js build process is required. Serve the `frontend/` directory using Python's built-in HTTP server or VS Code Live Server:

```bash
# Open a new terminal in the repository root
cd frontend
python -m http.server 5500
```

Open your browser and navigate to:
```
http://localhost:5500
```

To access the DuckDB SQL Playground, click **SQL Playground** in the header or navigate to:
```
http://localhost:5500/playground.html
```

---

## ⚙️ Configuration Reference (`.env`)

| Variable | Type | Default Value | Description |
| :--- | :--- | :--- | :--- |
| `DATABASE_URL` | `string` | `postgresql://localhost:5432/text2sql_demo` | PostgreSQL connection URI. Supports local instances and cloud databases like **Neon** (`postgresql://...sslmode=require&channel_binding=require`). |
| `GOOGLE_API_KEY` | `string` | `""` | Google AI Studio API key (`AIzaSy...`). When set, automatically activates the Gemini engine. |
| `GEMINI_MODEL` | `string` | `gemini-2.5-flash` | Gemini model name (e.g. `gemini-2.5-flash`, `gemini-1.5-flash`, `gemini-1.5-pro`). |
| `NVIDIA_API_KEY` | `string` | `""` | API key from NVIDIA NIM (`nvapi-...`). |
| `NVIDIA_MODEL` | `string` | `meta/llama-3.3-70b-instruct` | LLM model identifier for SQL code generation. |
| `NVIDIA_BASE_URL` | `string` | `https://integrate.api.nvidia.com/v1` | Base URL for OpenAI-compatible endpoints (NVIDIA NIM, Grok, Ollama). |
| `GROQ_API_KEY` | `string` | `""` | Groq API key (`gsk_...`) for ultra-low latency LPU inference. |
| `GROQ_MODEL` | `string` | `llama-3.3-70b-versatile` | Groq model identifier. |
| `XAI_API_KEY` | `string` | `""` | Optional xAI Grok API key. |
| `XAI_MODEL` | `string` | `grok-beta` | Optional xAI Grok model identifier. |

---

## 📡 REST API Reference

### 1. `GET /api/health`
Returns backend health status, database pool connectivity, and supported ingestion formats.

**Response `(200 OK)`:**
```json
{
  "status": "ok",
  "database_connected": true,
  "supported_formats": [
    ".csv", ".tsv", ".parquet", ".sql", 
    ".json", ".jsonl", ".xlsx", ".sqlite", ".db"
  ]
}
```

---

### 2. `GET /api/schema`
Returns full database introspection metadata for UI rendering and the exact prompt string supplied to the LLM.

**Response `(200 OK)`:**
```json
{
  "schema": "Table: customers(id integer, name text, email text, city text, country text, signed_up_at date)\nTable: orders(id integer, customer_id -> customers.id, status text, order_date date)...",
  "tables": [
    {
      "name": "customers",
      "row_count": 8,
      "columns": [
        { "name": "id", "type": "integer", "nullable": false, "foreign_key": null },
        { "name": "name", "type": "text", "nullable": false, "foreign_key": null },
        { "name": "email", "type": "text", "nullable": false, "foreign_key": null }
      ]
    }
  ],
  "table_count": 4
}
```

---

### 3. `GET /api/tables`
Returns active user tables, columns, data types, and foreign key relationships.

---

### 4. `GET /api/tables/{table_name}/sample`
Fetches a 5-row sample preview of any specified table.

**Example Request:**
```bash
curl -X GET "http://localhost:8000/api/tables/customers/sample"
```

**Response `(200 OK)`:**
```json
{
  "table": "customers",
  "columns": ["id", "name", "email", "city", "country", "signed_up_at"],
  "rows": [
    {
      "id": 1,
      "name": "Ananya Rao",
      "email": "ananya.rao@example.com",
      "city": "Hyderabad",
      "country": "India",
      "signed_up_at": "2024-01-15"
    }
  ],
  "count": 5
}
```

---

### 5. `DELETE /api/tables/{table_name}`
Safely drops a user-created table from PostgreSQL and cleans up DuckDB replicated state.

---

### 6. `POST /api/upload`
Uploads and ingests a dataset up to 50 GB. Streams content directly to disk and loads it into PostgreSQL and DuckDB.

**Request:** `multipart/form-data`
- `file`: Binary file stream
- `table_name` (optional): Custom PostgreSQL table name

**Example Curl:**
```bash
curl -X POST "http://localhost:8000/api/upload" \
  -F "file=@sales_q3.parquet" \
  -F "table_name=sales_q3"
```

**Response `(200 OK)`:**
```json
{
  "status": "success",
  "message": "Successfully ingested 'sales_q3.parquet'",
  "bytes_received": 14205882,
  "details": {
    "table_name": "sales_q3",
    "rows_inserted": 250000,
    "columns": [
      { "name": "transaction_id", "type": "BIGINT" },
      { "name": "amount", "type": "DOUBLE PRECISION" },
      { "name": "timestamp", "type": "TIMESTAMP" }
    ]
  }
}
```

---

### 7. `POST /api/query`
The primary Text-to-SQL endpoint. Accepts a natural language question, generates safe SQL, executes it against PostgreSQL, and returns structured rows with performance metrics.

**Request `(application/json)`:**
```json
{
  "question": "Show me top 3 customers with their total order spend"
}
```

**Response `(200 OK)`:**
```json
{
  "question": "Show me top 3 customers with their total order spend",
  "generated_sql": "SELECT c.name, SUM(oi.quantity * oi.unit_price) AS total_spend FROM customers c JOIN orders o ON c.id = o.customer_id JOIN order_items oi ON o.id = oi.order_id GROUP BY c.name ORDER BY total_spend DESC LIMIT 3;",
  "columns": ["name", "total_spend"],
  "rows": [
    { "name": "Priya Nair", "total_spend": "14999.00" },
    { "name": "Emma Wilson", "total_spend": "8999.00" },
    { "name": "Liam Chen", "total_spend": "3499.00" }
  ],
  "row_count": 3,
  "latency_ms": 384
}
```

---

### 8. `POST /api/playground/duckdb`
Executes SQL queries directly inside the in-memory DuckDB analytical engine. Supports specialized OLAP commands (`SUMMARIZE`, `PIVOT`, `QUALIFY`, `EXPLAIN`, window functions).

**Request `(application/json)`:**
```json
{
  "sql": "SUMMARIZE customers;",
  "max_rows": 1000
}
```

**Response `(200 OK)`:**
```json
{
  "sql": "SUMMARIZE customers;",
  "columns": ["column_name", "column_type", "min", "max", "approx_unique", "avg", "std"],
  "types": ["VARCHAR", "VARCHAR", "VARCHAR", "VARCHAR", "BIGINT", "DOUBLE", "DOUBLE"],
  "rows": [
    {
      "column_name": "lifetime_spend",
      "column_type": "DECIMAL(10,2)",
      "min": "450.25",
      "max": "5200.80",
      "approx_unique": 8,
      "avg": 2221.48,
      "std": 1692.12
    }
  ],
  "row_count": 7,
  "total_rows": 7,
  "latency_ms": 3,
  "engine": "DuckDB 1.1.0",
  "tables": [...]
}
```

---

### 9. `GET /api/playground/tables`
Returns real-time schema metadata, column types, and row counts of all active DuckDB tables.

---

### 10. `POST /api/playground`
Executes raw SQL queries against PostgreSQL inside a safe read-only transaction.

---

## 🔬 Deep Dive: Core Components

### 1. Multi-LLM Provider Engine (`nvidia_client.py`)
- **Dynamic Provider Routing:** Automatically selects the optimal AI engine based on active credentials:
  1. **Google Gemini (`gemini-2.5-flash` / `gemini-1.5-flash`):** Uses official `google-genai` SDK with automated payload formatting and zero-temperature deterministic SQL generation.
  2. **NVIDIA NIM (`meta/llama-3.3-70b-instruct`):** Connects to NVIDIA enterprise inference microservices for complex schema reasoning.
  3. **Groq LPU (`llama-3.3-70b-versatile`):** Sub-second ultra-low latency inference via Groq's Language Processing Units.
  4. **xAI Grok & OpenAI-Compatible Endpoints:** Configurable base URL support for self-hosted or alternative API providers.
- **Strict Few-Shot Prompt Engineering:** Enforces single-statement read-only rules, strict exclusion of markdown fences, syntax-compliant column escaping, and live catalog context injection.
- **Sliding-Window Rate Limiter:** Protects upstream token quotas with a dual-tiered sliding memory deque:
  $$\text{Capacity} = 60\text{ RPM} \quad \text{and} \quad 5000\text{ RPD}$$
- **Exponential Backoff:** Automatically retries transient network interruptions and HTTP 429 / 5xx responses with adaptive intervals ($1.5\text{s} \to 3.0\text{s} \to 5.0\text{s}$).
- **Dynamic Hot Reloading:** Re-reads `.env` on every request invocation so changes to API keys or model names take effect immediately without stopping Uvicorn.

### 2. Defense-in-Depth SQL Guard (`sql_guard.py`)
Even before touching database permissions, all generated queries pass through `validate_sql_safety()`:
1. **Clause Filtering:** Must strictly begin with `SELECT` or `WITH ... SELECT`.
2. **Keyword Blacklist:** Uses regex word-boundary matching (`\bKEYWORD\b`) to block destructive operations (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `CREATE`, `GRANT`, `REVOKE`, `EXEC`, `CALL`, `COPY`, `VACUUM`) while avoiding false positives on column names like `created_at`.
3. **Stacked Query Elimination:** Strictly forbids semicolons `;` within the body to prevent query chaining injections.
4. **Defensive Limit Injection:** If the model omits a `LIMIT` clause, the guard defensively appends `LIMIT 50`.

### 3. Dynamic Schema Introspection (`schema_service.py`)
- Queries PostgreSQL `information_schema.tables`, `columns`, and `table_constraints`.
- Extracts foreign key relationships (`kcu.table_name` $\to$ `ccu.table_name`).
- Generates a compact semantic notation for LLMs:
  ```
  Table: orders(id integer, customer_id -> customers.id, status text, order_date date)
  ```

### 4. Multi-Format Streaming Ingestion Engine (`ingestion.py`)
- **Memory Safety:** Uses `aiofiles` with 4MB chunk buffers to stream uploads up to 50 GB straight to disk.
- **Type Inference Engine:** Dynamically samples records and assigns optimal PostgreSQL types (`INTEGER`, `BIGINT`, `DOUBLE PRECISION`, `BOOLEAN`, `TIMESTAMP`, `TEXT`).
- **High-Speed Batch Copy:** Uses asyncpg's binary `copy_records_to_table` with 10,000-row batching for near line-rate ingestion.

### 5. In-Memory DuckDB OLAP Engine (`duckdb_service.py`)
- Connects to an in-memory DuckDB instance (`:memory:`) executing analytical queries with sub-millisecond latencies.
- **PyArrow Synchronization Bridge:** Automatically transforms PostgreSQL records into PyArrow tables (`pa.Table.from_pylist`) and registers them as native DuckDB tables without disk roundtrips.
- Pre-seeded with realistic e-commerce datasets (`customers`, `products`, `orders`).

### 6. Modern Liquid Glass UI & Design System
- **Zero-Dependency Architecture:** Pure Vanilla JavaScript, CSS custom properties, and semantic HTML5.
- **Capsule Form Factors:** High-aesthetic pill design system (`border-radius: var(--radius-full)`) across buttons, chips, and modal triggers.
- **Curated Gradients & Micro-Interactions:** Subtle luminous glows, animated loading dots, ripple origin effects, and custom scanline overlays.
- **Smart SQL Autocomplete:** In-editor popup offering real-time keyword suggestions, table names, dynamic column suggestions, and DuckDB analytical macros.
- **Keyboard Navigation:** Full keyboard navigation (`↑`/`↓` to traverse suggestions, `Tab`/`Enter` to insert, `Ctrl+Enter` to execute).
- **Export & Formatter:** Integrated SQL query beautifier, clipboard copying, and client-side CSV/JSON export.

---

## 📊 Supported Ingestion Formats & Specifications

| Format | Extension | Ingestion Strategy | Max Recommended File Size |
| :--- | :--- | :--- | :--- |
| **Apache Parquet** | `.parquet` | Vectorized PyArrow batch streaming | **50 GB** |
| **CSV / TSV** | `.csv`, `.tsv`, `.txt` | Sniffer dialect detection + asyncpg bulk copy | **25 GB** |
| **JSON Lines** | `.jsonl`, `.ndjson` | Line-by-line streaming generator | **20 GB** |
| **Standard JSON** | `.json` | Streamed dictionary list parser | **5 GB** |
| **Microsoft Excel** | `.xlsx`, `.xls` | OpenPyXL read-only iterator | **2 GB** |
| **SQLite Database** | `.sqlite`, `.sqlite3`, `.db` | Multi-table catalog extractor | **10 GB** |
| **SQL Script Dump** | `.sql` | Transactional direct execution | **10 GB** |

---

## 🔒 Security & Sandbox Model

Queryable adheres to strict security standards to ensure user safety when executing AI-generated code:

```
[ Natural Language Prompt ]
            │
            ▼
   [ Rate Limiter Deque ] ──► (Reject 429 if > 60 RPM or 5000 RPD)
            │
            ▼
[ Multi-LLM Provider Prompt ] ──► (Google Gemini / NVIDIA NIM / Groq with read-only system instruction)
            │
            ▼
   [ SQL Guard Validator ] ──► (Block destructive keywords, check single statement, force LIMIT)
            │
            ▼
[ Read-Only PG Transaction ] ──► (BEGIN READ ONLY; SET LOCAL statement_timeout = 5000;)
            │
            ▼
 [ Hard Truncation Guard ] ──► (Truncate output to MAX_ROWS = 100)
```

1. **Read-Only Transaction Isolation:** All PostgreSQL queries run inside `async with conn.transaction(readonly=True)`. Even if an exploit bypasses the string guard, the database kernel strictly aborts any write operations.
2. **Statement Timeouts:** PostgreSQL `statement_timeout` is set to 5000ms (5 seconds) to prevent Denial of Service (DoS) attacks via expensive Cartesian joins.
3. **DuckDB Filesystem Sandbox Guardrail:** All DuckDB playground queries are validated before execution to prohibit file-system inspection functions (`read_csv`, `read_parquet`, `copy`, `install`, `load`, `pragma_database_list`).
4. **Safe Identifier Sanitization:** All user-supplied table names and columns are sanitized using `^[a-zA-Z_][a-zA-Z0-9_]{0,62}$`.
5. **Origin-Restricted CORS Policy:** Cross-Origin Resource Sharing is locked down to authorized local frontend origins to prevent cross-site request forgery and data exfiltration.
6. **Environment Secret Isolation:** Credentials reside strictly in `backend/.env` which is ignored in [.gitignore](.gitignore).

---

## ❓ Troubleshooting & FAQ

### 1. `error: Database connection is not available`
- **Cause:** PostgreSQL is not running or `DATABASE_URL` in `backend/.env` is incorrect.
- **Solution:** Verify PostgreSQL is running:
  ```bash
  # Check PostgreSQL status (Linux/macOS)
  sudo service postgresql status
  # Test connection
  psql -d text2sql_demo
  ```
  Ensure `DATABASE_URL` in `backend/.env` matches your username and password.

### 2. `HTTP 401 Unauthorized: NVIDIA API Authentication failed`
- **Cause:** Missing or invalid API key.
- **Solution:** Sign in to [build.nvidia.com](https://build.nvidia.com), generate a personal API key (starts with `nvapi-`), and paste it into `backend/.env`. If using Groq, ensure your key starts with `gsk_`.

### 3. `HTTP 429: Rate limit exceeded`
- **Cause:** You have exceeded either the local 60 RPM rate limiter or upstream API quotas.
- **Solution:** Wait a few seconds before retrying. You can increase `max_rpm` in `backend/nvidia_client.py` if your plan supports higher throughput.

### 4. `CORS Policy Blocked`
- **Cause:** Frontend was opened using a different protocol/port not allowed by CORS.
- **Solution:** The backend includes permissive CORS defaults (`allow_origins=["*"]`). Ensure the backend server is running on `http://localhost:8000`.

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

---

<p align="center">
  <b>Queryable</b> — Designed &amp; Engineered for High-Performance Text-to-SQL Analytics.
</p>
