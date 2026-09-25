"""
Text-to-SQL Demo API
FastAPI + PostgreSQL + NVIDIA API + 50GB Multi-Format Data Ingestion Engine

Endpoints:
    POST /api/query                 -> Convert NL question to SQL, execute, return rows
    GET  /api/schema                -> Return dynamic DB schema and table metadata
    GET  /api/tables                -> Return list of active tables and columns
    GET  /api/tables/{name}/sample  -> Return sample records from a table
    DELETE /api/tables/{name}       -> Drop an uploaded table
    POST /api/upload                -> Stream & ingest multi-format files (CSV, Parquet, SQL, JSON, Excel, SQLite)
    GET  /api/health                -> Health check
"""

import os
import time
import shutil
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

import asyncpg
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from sql_guard import validate_sql_safety, SQLSafetyError
from nvidia_client import generate_sql_from_question, NVIDIAGenerationError, NVIDIARateLimitError
from schema_service import introspect_database_schema, get_table_sample, drop_table, is_safe_identifier
from ingestion import save_upload_to_disk, process_file_ingestion, sanitize_identifier
from duckdb_service import execute_duckdb_sql, get_duckdb_tables_metadata, sync_postgres_tables_to_duckdb, sync_single_table_to_duckdb, drop_duckdb_table

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("text2sql")

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://localhost:5432/text2sql_demo"
)
MAX_ROWS = 100
QUERY_TIMEOUT_MS = 5000
TEMP_UPLOAD_DIR = Path("./temp_uploads")


# ---------------------------------------------------------------------------
# App lifecycle: manage connection pool and cleanup temp directories
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    TEMP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    try:
        app.state.pool = await asyncpg.create_pool(
            DATABASE_URL, min_size=1, max_size=10
        )
        logger.info("Database connection pool created successfully")
        # Sync Postgres tables into DuckDB in-memory analytical engine
        try:
            await sync_postgres_tables_to_duckdb(app.state.pool)
        except Exception as sync_err:
            logger.warning("Initial DuckDB sync skipped: %s", sync_err)
    except Exception as e:
        logger.error("Could not connect to PostgreSQL database: %s", e)
        app.state.pool = None

    yield

    if getattr(app.state, "pool", None):
        await app.state.pool.close()
        logger.info("Database pool closed")

    # Clean up temp uploads on shutdown
    if TEMP_UPLOAD_DIR.exists():
        shutil.rmtree(TEMP_UPLOAD_DIR, ignore_errors=True)


app = FastAPI(
    title="Querity — Text-to-SQL API",
    description="Converts natural language questions into SQL, runs them safely, and ingests multi-format data.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5500", "http://127.0.0.1:5500", "http://localhost:3000"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


# ---------------------------------------------------------------------------
# Helper: check database connection
# ---------------------------------------------------------------------------
async def require_db_pool():
    pool = getattr(app.state, "pool", None)
    if pool is not None:
        return pool

    # Dynamic reconnect: reload .env and attempt connection if pool wasn't ready at startup
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)
    db_url = os.environ.get("DATABASE_URL", "").strip()

    if db_url and "your_user" not in db_url and "ep-xxx" not in db_url:
        try:
            logger.info("Attempting dynamic connection to database...")
            pool = await asyncpg.create_pool(db_url, min_size=1, max_size=10)
            app.state.pool = pool
            logger.info("Database connection established dynamically!")
            try:
                await sync_postgres_tables_to_duckdb(pool)
            except Exception as sync_err:
                logger.warning("DuckDB sync skipped: %s", sync_err)
            return pool
        except Exception as exc:
            logger.error("Dynamic database connection failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Could not connect to database ({exc}). Please check your DATABASE_URL in backend/.env"
            )

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Database connection is not available. Please paste your Neon connection string into DATABASE_URL in backend/.env and save the file (Ctrl+S)."
    )


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------
class QueryRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=500, description="Natural language question")


class QueryResponse(BaseModel):
    question: str
    generated_sql: str
    columns: list[str]
    rows: list[dict]
    row_count: int
    latency_ms: int


class ErrorResponse(BaseModel):
    detail: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/health", tags=["meta"])
async def health_check():
    db_ok = False
    try:
        pool = await require_db_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
            db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "ok",
        "database_connected": db_ok,
        "supported_formats": [".csv", ".tsv", ".parquet", ".sql", ".json", ".jsonl", ".xlsx", ".sqlite", ".db", ".zip"]
    }


@app.get("/api/schema", tags=["schema"])
async def get_schema():
    """Returns the live introspected schema description for NVIDIA AI and metadata for UI."""
    pool = await require_db_pool()
    try:
        schema_text, tables_meta = await introspect_database_schema(pool)
        return {
            "schema": schema_text,
            "tables": tables_meta,
            "table_count": len(tables_meta)
        }
    except Exception as exc:
        logger.error("Failed to introspect schema: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to inspect database schema: {exc}")


@app.get("/api/tables", tags=["schema"])
async def list_tables():
    """Returns list of all user tables in the database."""
    pool = await require_db_pool()
    try:
        _, tables_meta = await introspect_database_schema(pool)
        return {"tables": tables_meta}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/tables/{table_name}/sample", tags=["schema"])
async def sample_table(table_name: str):
    """Returns 5 sample records from the specified table."""
    pool = await require_db_pool()
    try:
        sample_data = await get_table_sample(pool, table_name, limit=5)
        return sample_data
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch sample: {exc}")


@app.delete("/api/tables/{table_name}", tags=["schema"])
async def delete_table(table_name: str):
    """Drops a table from PostgreSQL and DuckDB."""
    pool = await require_db_pool()
    try:
        await drop_table(pool, table_name)
        drop_duckdb_table(table_name)
        return {"status": "success", "message": f"Table '{table_name}' dropped successfully."}
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to drop table: {exc}")


@app.post("/api/upload", tags=["ingestion"])
async def upload_dataset(
    files: list[UploadFile] = File(None),
    file: UploadFile = File(None),
    table_name: str = Form(None)
):
    """
    Streams and ingests single or multiple database files (CSV, Parquet, SQL, JSON, Excel, SQLite, ZIP).
    Supports multi-file batch uploads up to 50 GB.
    """
    upload_list: list[UploadFile] = []
    if files:
        upload_list.extend(files)
    if file and file not in upload_list:
        upload_list.append(file)

    if not upload_list:
        raise HTTPException(status_code=400, detail="No file(s) provided.")

    pool = await require_db_pool()
    results = []
    total_bytes = 0

    for upload_file in upload_list:
        if not upload_file.filename:
            continue

        dest_filename = f"{int(time.time())}_{sanitize_identifier(upload_file.filename)}{Path(upload_file.filename).suffix}"
        dest_path = TEMP_UPLOAD_DIR / dest_filename

        try:
            # 1. Stream file directly to disk
            bytes_written = await save_upload_to_disk(upload_file, dest_path)
            total_bytes += bytes_written
            logger.info("Saved %d bytes to %s", bytes_written, dest_path)

            # If single file and custom table_name given, use it; otherwise auto-name from filename
            target_t_name = table_name if (len(upload_list) == 1 and table_name) else None

            # 2. Process and ingest into PostgreSQL
            res = await process_file_ingestion(
                file_path=dest_path,
                original_filename=upload_file.filename,
                custom_table_name=target_t_name,
                pool=pool
            )
            results.append({
                "filename": upload_file.filename,
                "table_name": res.get("table_name"),
                "rows_inserted": res.get("rows_inserted", 0),
                "details": res
            })
        except Exception as exc:
            logger.error("Ingestion failed for %s: %s", upload_file.filename, exc)
            raise HTTPException(status_code=400, detail=f"Data ingestion failed for '{upload_file.filename}': {exc}")
        finally:
            if dest_path.exists():
                try:
                    dest_path.unlink()
                except Exception:
                    pass

    # 3. Synchronize all newly ingested tables to DuckDB engine
    try:
        await sync_postgres_tables_to_duckdb(pool)
    except Exception as sync_exc:
        logger.warning("DuckDB sync after upload failed: %s", sync_exc)

    table_names_str = ", ".join([r["table_name"] for r in results if r.get("table_name")])
    total_rows = sum([r["rows_inserted"] for r in results])

    return {
        "status": "success",
        "message": f"Successfully ingested {len(results)} file(s): {table_names_str}",
        "files_count": len(results),
        "total_rows": total_rows,
        "bytes_received": total_bytes,
        "results": results,
        "details": results[0]["details"] if len(results) == 1 else {"table_name": table_names_str, "rows_inserted": total_rows}
    }


@app.post(
    "/api/query",
    response_model=QueryResponse,
    responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    tags=["query"],
)
async def run_query(payload: QueryRequest):
    pool = await require_db_pool()
    start = time.perf_counter()

    # 1. Fetch live dynamic schema so Gemini always knows about all current tables
    try:
        live_schema, _ = await introspect_database_schema(pool)
    except Exception as exc:
        logger.warning("Could not introspect live schema, falling back: %s", exc)
        live_schema = "Schema introspection unavailable."

    # 2. Generate SQL from the natural language question via NVIDIA API
    try:
        generated_sql = await generate_sql_from_question(payload.question, live_schema)
    except NVIDIARateLimitError as exc:
        logger.warning("Rate limit active: %s", exc)
        raise HTTPException(status_code=429, detail=str(exc))
    except NVIDIAGenerationError as exc:
        logger.warning("NVIDIA generation failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Could not generate SQL: {exc}")

    # 3. Validate the SQL is safe to run (read-only, no destructive statements, ensure limit)
    try:
        generated_sql = validate_sql_safety(generated_sql)
    except SQLSafetyError as exc:
        logger.warning("Unsafe SQL blocked: %s", generated_sql)
        raise HTTPException(status_code=400, detail=f"Generated query was blocked: {exc}")

    # 4. Execute against Postgres with a hard row limit and timeout
    try:
        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                await conn.execute(f"SET LOCAL statement_timeout = {QUERY_TIMEOUT_MS}")
                rows = await conn.fetch(generated_sql)
    except asyncpg.exceptions.QueryCanceledError:
        raise HTTPException(status_code=408, detail="Query took too long and was cancelled.")
    except asyncpg.PostgresError as exc:
        logger.warning("SQL execution error: %s", exc)
        raise HTTPException(status_code=400, detail=f"The generated SQL failed to execute: {exc}")

    truncated_rows = rows[:MAX_ROWS]
    columns = list(truncated_rows[0].keys()) if truncated_rows else []
    latency_ms = int((time.perf_counter() - start) * 1000)

    return QueryResponse(
        question=payload.question,
        generated_sql=generated_sql,
        columns=columns,
        rows=[dict(r) for r in truncated_rows],
        row_count=len(truncated_rows),
        latency_ms=latency_ms,
    )


# ---------------------------------------------------------------------------
# DuckDB SQL Playground — High-Performance Analytical Engine
# ---------------------------------------------------------------------------
class DuckDBPlaygroundRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=10000, description="SQL query for DuckDB engine")
    max_rows: int = Field(default=1000, ge=1, le=5000, description="Max rows to return")


class DuckDBPlaygroundResponse(BaseModel):
    sql: str
    columns: list[str]
    types: list[str]
    rows: list[dict]
    row_count: int
    total_rows: int
    latency_ms: int
    engine: str
    tables: list[dict]


@app.post(
    "/api/playground/duckdb",
    response_model=DuckDBPlaygroundResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    tags=["playground"],
)
async def run_duckdb_playground_query(payload: DuckDBPlaygroundRequest):
    """
    Executes a SQL query inside the high-performance DuckDB in-memory analytical engine.
    Supports SELECT, CTEs, WINDOW functions, PIVOT, SUMMARIZE, EXPLAIN, DESCRIBE, and more.
    """
    # Allow DESCRIBE / EXPLAIN / SUMMARIZE / SHOW explicitly (DuckDB-specific meta commands)
    first_token = payload.sql.strip().split()[0].upper() if payload.sql.strip() else ""
    meta_commands = {"DESCRIBE", "EXPLAIN", "SUMMARIZE", "SHOW", "PRAGMA"}
    if first_token not in meta_commands:
        try:
            payload.sql = validate_sql_safety(payload.sql)
        except SQLSafetyError as exc:
            raise HTTPException(status_code=400, detail=f"Query blocked by safety guard: {exc}")
    try:
        result = execute_duckdb_sql(payload.sql, max_rows=payload.max_rows)
        return DuckDBPlaygroundResponse(**result)
    except Exception as exc:
        logger.warning("DuckDB playground error: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/playground/tables", tags=["playground"])
async def get_duckdb_tables():
    """Returns list of active DuckDB tables, schema columns, types and row counts."""
    pool = getattr(app.state, "pool", None)
    if pool:
        try:
            await sync_postgres_tables_to_duckdb(pool)
        except Exception as sync_exc:
            logger.warning("Auto-sync on get_duckdb_tables failed: %s", sync_exc)

    try:
        tables = get_duckdb_tables_metadata()
        return {"tables": tables, "count": len(tables)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# Legacy / direct PostgreSQL playground endpoint (read-only)
class PlaygroundRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=5000, description="Raw SQL query to execute against PostgreSQL")


class PlaygroundResponse(BaseModel):
    sql: str
    columns: list[str]
    rows: list[dict]
    row_count: int
    latency_ms: int


@app.post(
    "/api/playground",
    response_model=PlaygroundResponse,
    responses={400: {"model": ErrorResponse}, 408: {"model": ErrorResponse}},
    tags=["playground"],
)
async def run_playground_query(payload: PlaygroundRequest):
    """Execute a user-written SQL query directly against PostgreSQL (read-only)."""
    pool = await require_db_pool()
    start = time.perf_counter()

    try:
        safe_sql = validate_sql_safety(payload.sql)
    except SQLSafetyError as exc:
        raise HTTPException(status_code=400, detail=f"Query blocked by safety guard: {exc}")

    try:
        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                await conn.execute(f"SET LOCAL statement_timeout = {QUERY_TIMEOUT_MS}")
                rows = await conn.fetch(safe_sql)
    except asyncpg.exceptions.QueryCanceledError:
        raise HTTPException(status_code=408, detail="Query took too long and was cancelled (5s timeout).")
    except asyncpg.PostgresError as exc:
        logger.warning("Playground SQL error: %s", exc)
        raise HTTPException(status_code=400, detail=f"SQL execution error: {exc}")

    truncated_rows = rows[:MAX_ROWS]
    columns = list(truncated_rows[0].keys()) if truncated_rows else []
    latency_ms = int((time.perf_counter() - start) * 1000)

    return PlaygroundResponse(
        sql=safe_sql,
        columns=columns,
        rows=[dict(r) for r in truncated_rows],
        row_count=len(truncated_rows),
        latency_ms=latency_ms,
    )
