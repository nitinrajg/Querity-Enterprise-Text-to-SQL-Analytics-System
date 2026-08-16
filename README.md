# Queryable — Text-to-SQL Demo

Ask an e-commerce database questions in plain English. The NVIDIA API (NVIDIA NIM) converts
your question into SQL, the backend validates it's safe, executes it read-only
against PostgreSQL, and the results show up as a live table.

## Stack

- **Backend:** FastAPI + asyncpg (PostgreSQL) + NVIDIA API (NIM / AI Foundation Endpoints)
- **Frontend:** Plain HTML/CSS/JS (no framework, no build step)
- **Database:** PostgreSQL, seeded with a small e-commerce dataset

## Project structure

```
text2sql/
├── backend/
│   ├── main.py            # FastAPI app, routes, request/response models
│   ├── nvidia_client.py   # NVIDIA API wrapper (NL -> SQL)
│   ├── sql_guard.py       # Safety checks before any SQL executes
│   ├── schema_service.py  # Live DB introspection & table operations
│   ├── ingestion.py       # Multi-format streaming data ingestion
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── index.html
│   ├── styles.css
│   └── app.js
└── db/
    └── schema.sql         # Tables + seed data
```

## Setup

### 1. Database

```bash
createdb text2sql_demo
psql text2sql_demo < db/schema.sql
```

### 2. Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env: set DATABASE_URL and NVIDIA_API_KEY
# Get your NVIDIA API key at https://build.nvidia.com

# Start the server:
uvicorn main:app --reload --port 8000
```

The API will be live at `http://localhost:8000`. Interactive docs (from
FastAPI's built-in OpenAPI support) are at `http://localhost:8000/docs`.

### 3. Frontend

No build step — just serve the folder:

```bash
cd frontend
python -m http.server 5500
```

Open `http://localhost:5500` in your browser.

## Endpoints

| Method | Path                         | Description                                            |
|--------|------------------------------|--------------------------------------------------------|
| GET    | `/api/health`                | Health check & database connection status              |
| GET    | `/api/schema`                | Returns live DB schema description & table metadata    |
| GET    | `/api/tables`                | List of tables and columns                             |
| GET    | `/api/tables/{name}/sample`  | 5 sample rows from a table                             |
| DELETE | `/api/tables/{name}`         | Drop table                                             |
| POST   | `/api/upload`                | Stream & ingest files (CSV, Parquet, SQL, JSON, Excel) |
| POST   | `/api/query`                 | `{ "question": "..." }` -> generated SQL + rows         |

## Safety notes

This is a learning/demo project, not production-hardened. What it *does* do:

- Rejects anything that isn't a single `SELECT`/`WITH` statement
- Blocks destructive keywords (`INSERT`, `UPDATE`, `DELETE`, `DROP`, etc.)
- Runs every query inside a read-only transaction with a statement timeout
- Enforces a hard row limit on returned results
- Includes built-in rate limiting and retry backoff on API requests
