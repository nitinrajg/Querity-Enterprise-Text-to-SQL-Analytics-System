"""
DuckDB High-Performance Analytical Engine for the SQL Playground.
Supports:
- In-memory OLAP query execution with sub-millisecond latencies.
- Pre-seeded rich demo datasets (customers, products, orders).
- Seamless real-time synchronization with PostgreSQL user tables & uploaded datasets via PyArrow.
- Special DuckDB analytical functions: SUMMARIZE, PIVOT, WINDOW functions, EXPLAIN, DESCRIBE.
- Clean type-safe JSON serialization.
"""

import time
import math
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any
import asyncpg
import duckdb
import pyarrow as pa

logger = logging.getLogger("text2sql")

# Shared In-Memory DuckDB Connection
_duckdb_conn = duckdb.connect(database=":memory:")


def _clean_val(val: Any) -> Any:
    """Serializes Python / DuckDB types into JSON-safe values."""
    if val is None:
        return None
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    if isinstance(val, (bytes, bytearray)):
        return val.decode("utf-8", errors="replace")
    return val


def init_sample_data():
    """Seed DuckDB with rich demonstration datasets for instant experimentation."""
    try:
        _duckdb_conn.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                customer_id INTEGER PRIMARY KEY,
                name VARCHAR,
                email VARCHAR,
                country VARCHAR,
                signup_date DATE,
                lifetime_spend DECIMAL(10, 2),
                tier VARCHAR
            );

            INSERT OR IGNORE INTO customers VALUES
            (101, 'Alex Rivera', 'alex@example.com', 'United States', '2023-01-15', 3420.50, 'Platinum'),
            (102, 'Sara Chen', 'sara@example.com', 'Canada', '2023-03-22', 1890.00, 'Gold'),
            (103, 'Liam Müller', 'liam@example.com', 'Germany', '2023-05-10', 450.25, 'Silver'),
            (104, 'Yuki Tanaka', 'yuki@example.com', 'Japan', '2023-06-01', 5200.80, 'Platinum'),
            (105, 'Elena Rossi', 'elena@example.com', 'Italy', '2023-08-19', 820.00, 'Silver'),
            (106, 'Marcus Vance', 'marcus@example.com', 'United States', '2023-09-05', 2150.40, 'Gold'),
            (107, 'Priya Patel', 'priya@example.com', 'India', '2023-11-12', 3100.00, 'Platinum'),
            (108, 'Lucas Silva', 'lucas@example.com', 'Brazil', '2024-01-08', 670.30, 'Silver');

            CREATE TABLE IF NOT EXISTS products (
                product_id INTEGER PRIMARY KEY,
                name VARCHAR,
                category VARCHAR,
                unit_price DECIMAL(10, 2),
                stock_quantity INTEGER,
                rating DECIMAL(3, 2)
            );

            INSERT OR IGNORE INTO products VALUES
            (1, 'Quantum Wireless Mouse', 'Electronics', 49.99, 142, 4.8),
            (2, 'Ergonomic Mechanical Keyboard', 'Electronics', 129.99, 58, 4.9),
            (3, '4K Ultra-Wide Monitor 34"', 'Electronics', 549.99, 23, 4.7),
            (4, 'Noise-Cancelling Headphones Pro', 'Audio', 249.99, 85, 4.8),
            (5, 'USB-C Multi-Port Hub', 'Accessories', 39.99, 310, 4.5),
            (6, 'Standing Desk Converter', 'Furniture', 199.99, 34, 4.6),
            (7, 'Premium Leather Desk Mat', 'Accessories', 29.99, 210, 4.4),
            (8, 'Streamer Studio Microphone', 'Audio', 119.99, 62, 4.7);

            CREATE TABLE IF NOT EXISTS orders (
                order_id INTEGER PRIMARY KEY,
                customer_id INTEGER,
                order_date TIMESTAMP,
                status VARCHAR,
                total_amount DECIMAL(10, 2),
                shipping_country VARCHAR
            );

            INSERT OR IGNORE INTO orders VALUES
            (1001, 101, '2024-01-15 10:30:00', 'delivered', 179.98, 'United States'),
            (1002, 102, '2024-01-16 14:15:00', 'delivered', 549.99, 'Canada'),
            (1003, 104, '2024-01-18 09:00:00', 'delivered', 799.98, 'Japan'),
            (1004, 103, '2024-01-20 16:45:00', 'shipped', 79.98, 'Germany'),
            (1005, 101, '2024-01-22 11:20:00', 'delivered', 249.99, 'United States'),
            (1006, 105, '2024-01-25 13:10:00', 'cancelled', 199.99, 'Italy'),
            (1007, 107, '2024-02-01 18:00:00', 'delivered', 679.98, 'India'),
            (1008, 106, '2024-02-05 12:30:00', 'processing', 169.98, 'United States'),
            (1009, 104, '2024-02-10 15:40:00', 'delivered', 349.98, 'Japan'),
            (1010, 108, '2024-02-14 08:25:00', 'delivered', 119.99, 'Brazil');
        """)
        logger.info("Initialized DuckDB sample datasets.")
    except Exception as exc:
        logger.warning("Could not initialize sample datasets in DuckDB: %s", exc)


# Initialize sample data on module load
init_sample_data()


async def sync_single_table_to_duckdb(pool: asyncpg.Pool, table_name: str):
    """Syncs a single table from PostgreSQL into DuckDB using PyArrow for instant in-memory OLAP."""
    try:
        async with pool.acquire() as conn:
            # Fetch all rows from PostgreSQL (up to 100,000 for in-memory playground)
            rows = await conn.fetch(f'SELECT * FROM "{table_name}" LIMIT 100000;')
            if not rows:
                # If table is empty, create an empty table matching column schema
                col_rows = await conn.fetch(
                    "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=$1 ORDER BY ordinal_position",
                    table_name
                )
                if col_rows:
                    cols_def = [f'"{c["column_name"]}" VARCHAR' for c in col_rows]
                    _duckdb_conn.execute(f'CREATE OR REPLACE TABLE "{table_name}" ({", ".join(cols_def)});')
                return

            dict_rows = [dict(r) for r in rows]
            clean_rows = []
            for r in dict_rows:
                clean_r = {}
                for k, v in r.items():
                    if v is None:
                        clean_r[k] = None
                    elif hasattr(v, "isoformat"):
                        clean_r[k] = v.isoformat()
                    elif hasattr(v, "__float__"):
                        clean_r[k] = float(v)
                    else:
                        clean_r[k] = str(v) if not isinstance(v, (int, float, bool, str)) else v
                clean_rows.append(clean_r)

            arrow_table = pa.Table.from_pylist(clean_rows)
            _duckdb_conn.register(f"tmp_pg_{table_name}", arrow_table)
            _duckdb_conn.execute(f'CREATE OR REPLACE TABLE "{table_name}" AS SELECT * FROM "tmp_pg_{table_name}";')
            _duckdb_conn.unregister(f"tmp_pg_{table_name}")
            logger.info("Synced PostgreSQL table '%s' (%d rows) to DuckDB.", table_name, len(clean_rows))
    except Exception as exc:
        logger.error("Error syncing table '%s' to DuckDB: %s", table_name, exc)


async def sync_postgres_tables_to_duckdb(pool: asyncpg.Pool):
    """
    Syncs all user tables and uploaded datasets from PostgreSQL into the in-memory DuckDB engine.
    """
    try:
        async with pool.acquire() as conn:
            table_rows = await conn.fetch("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE';
            """)
            
            for t_row in table_rows:
                t_name = t_row["table_name"]
                await sync_single_table_to_duckdb(pool, t_name)
                    
        logger.info("Successfully synced all PostgreSQL tables into DuckDB playground engine.")
    except Exception as exc:
        logger.warning("Could not sync PostgreSQL tables to DuckDB: %s", exc)


def drop_duckdb_table(table_name: str):
    """Drops a table from the DuckDB in-memory engine when deleted in PostgreSQL."""
    try:
        _duckdb_conn.execute(f'DROP TABLE IF EXISTS "{table_name}";')
        logger.info("Dropped table '%s' from DuckDB engine.", table_name)
    except Exception as exc:
        logger.warning("Error dropping DuckDB table '%s': %s", table_name, exc)


def get_duckdb_tables_metadata() -> list[dict[str, Any]]:
    """Returns a list of all tables, schema definitions, and row counts currently in DuckDB."""
    try:
        tables_res = _duckdb_conn.sql("SHOW TABLES;").fetchall()
        tables_meta = []
        for (t_name,) in tables_res:
            if t_name.startswith("tmp_pg_"):
                continue
            cols_res = _duckdb_conn.sql(f'DESCRIBE "{t_name}";').fetchall()
            cols = []
            for col in cols_res:
                cols.append({
                    "name": str(col[0]),
                    "type": str(col[1]),
                    "nullable": col[2] == "YES" if len(col) > 2 else True
                })
            try:
                count_res = _duckdb_conn.sql(f'SELECT count(*) FROM "{t_name}";').fetchone()
                row_count = int(count_res[0]) if count_res else 0
            except Exception:
                row_count = 0

            tables_meta.append({
                "name": t_name,
                "columns": cols,
                "row_count": row_count
            })
        return tables_meta
    except Exception as exc:
        logger.error("Error inspecting DuckDB tables: %s", exc)
        return []


def execute_duckdb_sql(query: str, max_rows: int = 1000) -> dict[str, Any]:
    """
    Executes a raw SQL query using the high-performance DuckDB analytical engine.
    Supports SELECT, WITH, PIVOT, SUMMARIZE, EXPLAIN, DESCRIBE, CREATE, INSERT, etc.
    """
    start = time.perf_counter()
    cleaned_query = query.strip().rstrip(";")
    
    if not cleaned_query:
        raise ValueError("Query string cannot be empty.")

    try:
        rel = _duckdb_conn.sql(cleaned_query)
        
        # DDL / DML commands that return no relation
        if rel is None:
            tables = get_duckdb_tables_metadata()
            return {
                "sql": query,
                "columns": ["status"],
                "types": ["VARCHAR"],
                "rows": [{"status": "Query executed successfully."}],
                "row_count": 1,
                "total_rows": 1,
                "latency_ms": int((time.perf_counter() - start) * 1000),
                "engine": f"DuckDB {duckdb.__version__}",
                "tables": tables
            }

        col_names = list(rel.columns)
        col_types = [str(t) for t in rel.types]
        
        # Fetch rows up to max_rows
        fetched = rel.limit(max_rows).fetchall()
        
        rows = []
        for r in fetched:
            row_dict = {}
            for col_name, val in zip(col_names, r):
                row_dict[col_name] = _clean_val(val)
            rows.append(row_dict)

        latency_ms = int((time.perf_counter() - start) * 1000)
        tables = get_duckdb_tables_metadata()

        return {
            "sql": query,
            "columns": col_names,
            "types": col_types,
            "rows": rows,
            "row_count": len(rows),
            "total_rows": len(rows),
            "latency_ms": latency_ms,
            "engine": f"DuckDB {duckdb.__version__}",
            "tables": tables
        }

    except Exception as exc:
        logger.warning("DuckDB execution error: %s", exc)
        raise
