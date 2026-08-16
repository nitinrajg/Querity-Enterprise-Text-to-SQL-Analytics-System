"""
High-performance, streaming multi-format data ingestion engine for PostgreSQL.
Supports files up to 50 GB by streaming to disk and batching records into Postgres.

Supported Formats:
- CSV / TSV (.csv, .tsv, .txt)
- Apache Parquet (.parquet)
- SQL dumps and scripts (.sql)
- JSON / JSON Lines (.json, .jsonl, .ndjson)
- Microsoft Excel (.xlsx, .xls)
- SQLite databases (.sqlite, .sqlite3, .db)
"""

import os
import re
import csv
import json
import sqlite3
import logging
import asyncio
from pathlib import Path
from typing import Any, AsyncGenerator, Tuple
import datetime

import aiofiles
import asyncpg
from fastapi import UploadFile

logger = logging.getLogger("text2sql")

# Chunk size for streaming uploads to disk (4 MB)
UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024
# Batch size for database inserts
DB_BATCH_SIZE = 10000


def sanitize_identifier(raw: str, default: str = "custom_table") -> str:
    """Converts raw string or filename into a clean, safe Postgres SQL identifier."""
    # Strip file extension if any
    name = Path(raw).stem
    # Replace non-alphanumerics with underscores
    clean = re.sub(r"[^a-zA-Z0-9_]+", "_", name).strip("_").lower()
    # Ensure doesn't start with a number or digit
    if not clean or clean[0].isdigit():
        clean = f"t_{clean}" if clean else default
    # Postgres identifiers max 63 characters
    return clean[:63]


def infer_pg_type_from_value(val: Any) -> str:
    """Infers PostgreSQL data type from a Python value or string."""
    if val is None or val == "":
        return "TEXT"
    
    if isinstance(val, bool):
        return "BOOLEAN"
    if isinstance(val, int):
        if -2147483648 <= val <= 2147483647:
            return "INTEGER"
        return "BIGINT"
    if isinstance(val, float):
        return "DOUBLE PRECISION"
    if isinstance(val, (datetime.datetime, datetime.date)):
        return "TIMESTAMP"

    val_str = str(val).strip()
    if not val_str:
        return "TEXT"

    # Try boolean
    if val_str.lower() in ("true", "false"):
        return "BOOLEAN"

    # Try integer
    try:
        iv = int(val_str)
        if -2147483648 <= iv <= 2147483647:
            return "INTEGER"
        return "BIGINT"
    except ValueError:
        pass

    # Try float
    try:
        float(val_str)
        return "DOUBLE PRECISION"
    except ValueError:
        pass

    # Try date / ISO timestamp
    if re.match(r"^\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}(?::\d{2})?)?", val_str):
        return "TIMESTAMP"

    return "TEXT"


def cast_value_for_pg(val: Any, target_type: str) -> Any:
    """Converts a value to its appropriate Python type before asyncpg insert."""
    if val is None or val == "" or (isinstance(val, str) and val.lower() in ("null", "none", "nan")):
        return None

    try:
        if target_type == "BOOLEAN":
            if isinstance(val, bool):
                return val
            return str(val).strip().lower() in ("true", "1", "t", "yes")
        elif target_type == "INTEGER":
            return int(float(val))
        elif target_type == "BIGINT":
            return int(float(val))
        elif target_type == "DOUBLE PRECISION":
            return float(val)
        elif target_type == "TIMESTAMP":
            if isinstance(val, (datetime.datetime, datetime.date)):
                return val
            # Try parsing ISO format
            s = str(val).strip().replace("Z", "")
            return datetime.datetime.fromisoformat(s)
        else:
            return str(val)
    except Exception:
        return str(val) if val is not None else None


async def save_upload_to_disk(upload_file: UploadFile, dest_path: Path) -> int:
    """
    Streams an incoming upload directly to disk in chunks to avoid high RAM usage.
    Supports up to 50 GB files without memory spikes.
    """
    bytes_written = 0
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiofiles.open(dest_path, "wb") as out_file:
        while True:
            chunk = await upload_file.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            await out_file.write(chunk)
            bytes_written += len(chunk)

    return bytes_written


# -----------------------------------------------------------------------------
# Ingestion Handlers by Format
# -----------------------------------------------------------------------------

async def ingest_csv(file_path: Path, table_name: str, pool: asyncpg.Pool) -> dict[str, Any]:
    """Parses and streams CSV/TSV data into PostgreSQL."""
    # Detect delimiter and sample header
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        sample = f.read(64 * 1024)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            delimiter = dialect.delimiter
        except Exception:
            delimiter = "\t" if file_path.suffix.lower() == ".tsv" else ","

        reader = csv.reader(f, delimiter=delimiter)
        try:
            raw_headers = next(reader)
        except StopIteration:
            raise ValueError("CSV file is empty.")

        headers = [sanitize_identifier(h, f"col_{i+1}") for i, h in enumerate(raw_headers)]
        # Ensure unique column names
        seen = {}
        unique_headers = []
        for h in headers:
            if h in seen:
                seen[h] += 1
                unique_headers.append(f"{h}_{seen[h]}")
            else:
                seen[h] = 0
                unique_headers.append(h)
        headers = unique_headers

        # Sample rows for type inference
        sample_rows = []
        for _ in range(500):
            try:
                row = next(reader)
                if row:
                    sample_rows.append(row)
            except StopIteration:
                break

    # Infer column types
    col_types = {}
    for i, col in enumerate(headers):
        types_observed = set()
        for r in sample_rows:
            if i < len(r) and r[i] != "":
                types_observed.add(infer_pg_type_from_value(r[i]))
        if "TEXT" in types_observed or not types_observed:
            col_types[col] = "TEXT"
        elif "TIMESTAMP" in types_observed:
            col_types[col] = "TIMESTAMP"
        elif "DOUBLE PRECISION" in types_observed:
            col_types[col] = "DOUBLE PRECISION"
        elif "BIGINT" in types_observed:
            col_types[col] = "BIGINT"
        elif "INTEGER" in types_observed:
            col_types[col] = "INTEGER"
        elif "BOOLEAN" in types_observed:
            col_types[col] = "BOOLEAN"
        else:
            col_types[col] = "TEXT"

    # Create table in PostgreSQL
    cols_ddl = ", ".join([f'"{col}" {col_types[col]}' for col in headers])
    async with pool.acquire() as conn:
        await conn.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE;')
        await conn.execute(f'CREATE TABLE "{table_name}" ({cols_ddl});')

    # Stream rows into Postgres in batches
    total_rows = 0
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter=delimiter)
        next(reader)  # Skip header

        batch = []
        async with pool.acquire() as conn:
            for row in reader:
                if not row:
                    continue
                # Pad or trim row to match headers
                padded = row + [""] * (len(headers) - len(row))
                converted_row = tuple(
                    cast_value_for_pg(padded[i], col_types[headers[i]])
                    for i in range(len(headers))
                )
                batch.append(converted_row)

                if len(batch) >= DB_BATCH_SIZE:
                    await conn.copy_records_to_table(table_name, records=batch, columns=headers)
                    total_rows += len(batch)
                    batch = []

            if batch:
                await conn.copy_records_to_table(table_name, records=batch, columns=headers)
                total_rows += len(batch)

    return {
        "table_name": table_name,
        "rows_inserted": total_rows,
        "columns": [{"name": col, "type": col_types[col]} for col in headers]
    }


async def ingest_parquet(file_path: Path, table_name: str, pool: asyncpg.Pool) -> dict[str, Any]:
    """Streams Apache Parquet data into PostgreSQL via PyArrow."""
    import pyarrow.parquet as pq

    parquet_file = pq.ParquetFile(str(file_path))
    schema = parquet_file.schema_arrow

    arrow_to_pg = {
        "int8": "INTEGER",
        "int16": "INTEGER",
        "int32": "INTEGER",
        "int64": "BIGINT",
        "uint8": "INTEGER",
        "uint16": "INTEGER",
        "uint32": "BIGINT",
        "uint64": "BIGINT",
        "float": "DOUBLE PRECISION",
        "double": "DOUBLE PRECISION",
        "bool": "BOOLEAN",
        "string": "TEXT",
        "large_string": "TEXT",
        "timestamp": "TIMESTAMP",
        "date32": "TIMESTAMP",
        "date64": "TIMESTAMP",
    }

    headers = []
    col_types = {}
    for name in schema.names:
        clean_name = sanitize_identifier(name)
        headers.append(clean_name)
        arrow_type = str(schema.field(name).type).lower()
        
        pg_t = "TEXT"
        for k, v in arrow_to_pg.items():
            if k in arrow_type:
                pg_t = v
                break
        col_types[clean_name] = pg_t

    # Create table
    cols_ddl = ", ".join([f'"{col}" {col_types[col]}' for col in headers])
    async with pool.acquire() as conn:
        await conn.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE;')
        await conn.execute(f'CREATE TABLE "{table_name}" ({cols_ddl});')

    total_rows = 0
    async with pool.acquire() as conn:
        for batch in parquet_file.iter_batches(batch_size=DB_BATCH_SIZE):
            pydict = batch.to_pydict()
            row_count = len(next(iter(pydict.values()))) if pydict else 0
            if row_count == 0:
                continue

            records = []
            orig_names = schema.names
            for i in range(row_count):
                row_tuple = tuple(
                    cast_value_for_pg(pydict[orig_names[j]][i], col_types[headers[j]])
                    for j in range(len(headers))
                )
                records.append(row_tuple)

            await conn.copy_records_to_table(table_name, records=records, columns=headers)
            total_rows += row_count

    return {
        "table_name": table_name,
        "rows_inserted": total_rows,
        "columns": [{"name": col, "type": col_types[col]} for col in headers]
    }


async def ingest_sql(file_path: Path, pool: asyncpg.Pool) -> dict[str, Any]:
    """Executes a .sql script/dump directly against PostgreSQL."""
    async with aiofiles.open(file_path, "r", encoding="utf-8", errors="replace") as f:
        sql_content = await f.read()

    if not sql_content.strip():
        raise ValueError("SQL file is empty.")

    async with pool.acquire() as conn:
        # Execute script statements
        await conn.execute(sql_content)

    return {
        "table_name": "Multiple (SQL script executed)",
        "rows_inserted": 0,
        "columns": []
    }


async def ingest_json(file_path: Path, table_name: str, pool: asyncpg.Pool) -> dict[str, Any]:
    """Parses JSON or JSON Lines (.jsonl / .ndjson) and loads into PostgreSQL."""
    is_jsonl = file_path.suffix.lower() in (".jsonl", ".ndjson")
    records = []

    if is_jsonl:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    else:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
            if isinstance(data, list):
                records = data
            elif isinstance(data, dict):
                # Try finding first list in dictionary
                list_val = next((v for v in data.values() if isinstance(v, list)), None)
                records = list_val if list_val else [data]
            else:
                raise ValueError("Unsupported JSON format. Expected array of objects.")

    if not records:
        raise ValueError("No records found in JSON file.")

    # Determine unique fields
    all_keys = set()
    for r in records[:500]:
        if isinstance(r, dict):
            all_keys.update(r.keys())

    headers = [sanitize_identifier(k) for k in all_keys]
    key_to_header = {k: sanitize_identifier(k) for k in all_keys}

    # Infer types
    col_types = {}
    for k, h in key_to_header.items():
        observed = set(infer_pg_type_from_value(r.get(k)) for r in records[:500] if isinstance(r, dict) and r.get(k) is not None)
        if "TEXT" in observed or not observed:
            col_types[h] = "TEXT"
        elif "TIMESTAMP" in observed:
            col_types[h] = "TIMESTAMP"
        elif "DOUBLE PRECISION" in observed:
            col_types[h] = "DOUBLE PRECISION"
        elif "BIGINT" in observed:
            col_types[h] = "BIGINT"
        elif "INTEGER" in observed:
            col_types[h] = "INTEGER"
        elif "BOOLEAN" in observed:
            col_types[h] = "BOOLEAN"
        else:
            col_types[h] = "TEXT"

    # Create table
    cols_ddl = ", ".join([f'"{h}" {col_types[h]}' for h in headers])
    async with pool.acquire() as conn:
        await conn.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE;')
        await conn.execute(f'CREATE TABLE "{table_name}" ({cols_ddl});')

    # Insert rows
    batch = []
    total_rows = 0
    async with pool.acquire() as conn:
        for r in records:
            if not isinstance(r, dict):
                continue
            row_tuple = tuple(
                cast_value_for_pg(r.get(k), col_types[key_to_header[k]])
                for k in all_keys
            )
            batch.append(row_tuple)

            if len(batch) >= DB_BATCH_SIZE:
                await conn.copy_records_to_table(table_name, records=batch, columns=headers)
                total_rows += len(batch)
                batch = []

        if batch:
            await conn.copy_records_to_table(table_name, records=batch, columns=headers)
            total_rows += len(batch)

    return {
        "table_name": table_name,
        "rows_inserted": total_rows,
        "columns": [{"name": col, "type": col_types[col]} for col in headers]
    }


async def ingest_excel(file_path: Path, table_name: str, pool: asyncpg.Pool) -> dict[str, Any]:
    """Streams an Excel spreadsheet (.xlsx, .xls) into PostgreSQL."""
    import openpyxl

    wb = openpyxl.load_workbook(str(file_path), read_only=True, data_only=True)
    sheet = wb.active

    rows_iter = sheet.iter_rows(values_only=True)
    try:
        raw_headers = next(rows_iter)
    except StopIteration:
        raise ValueError("Excel sheet is empty.")

    headers = [sanitize_identifier(str(h) if h else f"col_{i+1}") for i, h in enumerate(raw_headers)]
    
    # Infer types from first rows
    sample_rows = []
    for _ in range(200):
        try:
            r = next(rows_iter)
            if any(cell is not None for cell in r):
                sample_rows.append(r)
        except StopIteration:
            break

    col_types = {}
    for i, col in enumerate(headers):
        types_observed = set(infer_pg_type_from_value(r[i]) for r in sample_rows if i < len(r) and r[i] is not None)
        if "TEXT" in types_observed or not types_observed:
            col_types[col] = "TEXT"
        elif "TIMESTAMP" in types_observed:
            col_types[col] = "TIMESTAMP"
        elif "DOUBLE PRECISION" in types_observed:
            col_types[col] = "DOUBLE PRECISION"
        elif "BIGINT" in types_observed:
            col_types[col] = "BIGINT"
        elif "INTEGER" in types_observed:
            col_types[col] = "INTEGER"
        elif "BOOLEAN" in types_observed:
            col_types[col] = "BOOLEAN"
        else:
            col_types[col] = "TEXT"

    # Create table
    cols_ddl = ", ".join([f'"{col}" {col_types[col]}' for col in headers])
    async with pool.acquire() as conn:
        await conn.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE;')
        await conn.execute(f'CREATE TABLE "{table_name}" ({cols_ddl});')

    # Stream remaining rows
    wb.close()
    wb = openpyxl.load_workbook(str(file_path), read_only=True, data_only=True)
    sheet = wb.active
    rows_iter = sheet.iter_rows(values_only=True)
    next(rows_iter)  # Skip header

    batch = []
    total_rows = 0
    async with pool.acquire() as conn:
        for row in rows_iter:
            if not any(c is not None for c in row):
                continue
            padded = list(row) + [None] * (len(headers) - len(row))
            row_tuple = tuple(
                cast_value_for_pg(padded[i], col_types[headers[i]])
                for i in range(len(headers))
            )
            batch.append(row_tuple)

            if len(batch) >= DB_BATCH_SIZE:
                await conn.copy_records_to_table(table_name, records=batch, columns=headers)
                total_rows += len(batch)
                batch = []

        if batch:
            await conn.copy_records_to_table(table_name, records=batch, columns=headers)
            total_rows += len(batch)

    wb.close()
    return {
        "table_name": table_name,
        "rows_inserted": total_rows,
        "columns": [{"name": col, "type": col_types[col]} for col in headers]
    }


async def ingest_sqlite(file_path: Path, pool: asyncpg.Pool) -> dict[str, Any]:
    """Reads tables from an SQLite database and transfers them to PostgreSQL."""
    conn_sq = sqlite3.connect(str(file_path))
    cursor = conn_sq.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    tables = [r[0] for r in cursor.fetchall()]

    if not tables:
        conn_sq.close()
        raise ValueError("No user tables found in SQLite file.")

    created_tables = []
    total_rows = 0

    for sq_table in tables:
        pg_table = sanitize_identifier(sq_table)
        cursor.execute(f"PRAGMA table_info('{sq_table}')")
        col_info = cursor.fetchall()
        
        headers = [sanitize_identifier(col[1]) for col in col_info]
        col_types = {}
        for col in col_info:
            c_name = sanitize_identifier(col[1])
            sq_type = col[2].upper()
            if "INT" in sq_type:
                col_types[c_name] = "BIGINT"
            elif "REAL" in sq_type or "FLOA" in sq_type or "DOUB" in sq_type:
                col_types[c_name] = "DOUBLE PRECISION"
            elif "BOOL" in sq_type:
                col_types[c_name] = "BOOLEAN"
            else:
                col_types[c_name] = "TEXT"

        cols_ddl = ", ".join([f'"{col}" {col_types[col]}' for col in headers])
        async with pool.acquire() as conn:
            await conn.execute(f'DROP TABLE IF EXISTS "{pg_table}" CASCADE;')
            await conn.execute(f'CREATE TABLE "{pg_table}" ({cols_ddl});')

        # Stream rows
        cursor.execute(f"SELECT * FROM '{sq_table}'")
        batch = []
        table_rows = 0
        async with pool.acquire() as conn:
            while True:
                rows = cursor.fetchmany(DB_BATCH_SIZE)
                if not rows:
                    break
                records = [
                    tuple(cast_value_for_pg(r[i], col_types[headers[i]]) for i in range(len(headers)))
                    for r in rows
                ]
                await conn.copy_records_to_table(pg_table, records=records, columns=headers)
                table_rows += len(records)

        created_tables.append(pg_table)
        total_rows += table_rows

    conn_sq.close()
    return {
        "table_name": ", ".join(created_tables),
        "rows_inserted": total_rows,
        "columns": []
    }


# -----------------------------------------------------------------------------
# Main Ingestion Dispatcher
# -----------------------------------------------------------------------------

async def process_file_ingestion(
    file_path: Path,
    original_filename: str,
    custom_table_name: str | None,
    pool: asyncpg.Pool
) -> dict[str, Any]:
    """Dispatches file to the appropriate format parser and ingests into PostgreSQL."""
    ext = file_path.suffix.lower()
    table_name = sanitize_identifier(custom_table_name if custom_table_name else original_filename)

    logger.info("Starting ingestion for %s (format: %s) into table '%s'", original_filename, ext, table_name)

    if ext in (".csv", ".tsv", ".txt"):
        result = await ingest_csv(file_path, table_name, pool)
    elif ext == ".parquet":
        result = await ingest_parquet(file_path, table_name, pool)
    elif ext == ".sql":
        result = await ingest_sql(file_path, pool)
    elif ext in (".json", ".jsonl", ".ndjson"):
        result = await ingest_json(file_path, table_name, pool)
    elif ext in (".xlsx", ".xls"):
        result = await ingest_excel(file_path, table_name, pool)
    elif ext in (".sqlite", ".sqlite3", ".db"):
        result = await ingest_sqlite(file_path, pool)
    else:
        raise ValueError(f"Unsupported file format '{ext}'. Supported: CSV, Parquet, SQL, JSON, Excel, SQLite.")

    logger.info("Ingestion completed: %s", result)
    return result
