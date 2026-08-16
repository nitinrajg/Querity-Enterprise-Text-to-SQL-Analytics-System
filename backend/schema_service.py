"""
Dynamic database schema introspection and table management for PostgreSQL.
Provides live schema context to NVIDIA AI and table metadata to the frontend.
"""

import re
import logging
from typing import Any
import asyncpg

logger = logging.getLogger("text2sql")

# Pattern for safe SQL table and column identifiers (letters, digits, underscore)
SAFE_IDENTIFIER_REGEX = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")


def is_safe_identifier(name: str) -> bool:
    return bool(SAFE_IDENTIFIER_REGEX.match(name))


async def introspect_database_schema(pool: asyncpg.Pool) -> tuple[str, list[dict[str, Any]]]:
    """
    Queries PostgreSQL system catalogs to build:
    1. A natural language schema string for NVIDIA AI system prompt.
    2. A structured metadata list for the UI.
    """
    query_tables_and_cols = """
        SELECT 
            c.table_name,
            c.column_name,
            c.data_type,
            c.is_nullable,
            c.ordinal_position
        FROM information_schema.tables t
        JOIN information_schema.columns c 
            ON t.table_name = c.table_name 
            AND t.table_schema = c.table_schema
        WHERE t.table_schema = 'public'
          AND t.table_type = 'BASE TABLE'
        ORDER BY c.table_name, c.ordinal_position;
    """

    query_foreign_keys = """
        SELECT
            kcu.table_name AS src_table,
            kcu.column_name AS src_column,
            ccu.table_name AS target_table,
            ccu.column_name AS target_column
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage AS ccu
            ON ccu.constraint_name = tc.constraint_name
            AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = 'public';
    """

    async with pool.acquire() as conn:
        col_rows = await conn.fetch(query_tables_and_cols)
        fk_rows = await conn.fetch(query_foreign_keys)

        # Build FK map: (src_table, src_column) -> "target_table.target_column"
        fk_map: dict[tuple[str, str], str] = {}
        for fk in fk_rows:
            fk_map[(fk["src_table"], fk["src_column"])] = f"{fk['target_table']}.{fk['target_column']}"

        # Group columns by table
        tables_dict: dict[str, list[dict[str, Any]]] = {}
        for row in col_rows:
            t_name = row["table_name"]
            if t_name not in tables_dict:
                tables_dict[t_name] = []
            
            c_name = row["column_name"]
            fk_target = fk_map.get((t_name, c_name))
            tables_dict[t_name].append({
                "name": c_name,
                "type": row["data_type"],
                "nullable": row["is_nullable"] == "YES",
                "foreign_key": fk_target
            })

        # Fetch row counts for each table
        tables_meta: list[dict[str, Any]] = []
        schema_lines: list[str] = []

        for t_name, cols in tables_dict.items():
            # Get count safely using quoted table name
            try:
                count_res = await conn.fetchval(f'SELECT count(*) FROM "{t_name}"')
                row_count = int(count_res) if count_res is not None else 0
            except Exception:
                row_count = 0

            tables_meta.append({
                "name": t_name,
                "columns": cols,
                "row_count": row_count
            })

            # Format line for Gemini
            col_strs = []
            for col in cols:
                if col.get("foreign_key"):
                    col_strs.append(f"{col['name']} -> {col['foreign_key']}")
                else:
                    col_strs.append(f"{col['name']} {col['type']}")
            
            schema_lines.append(f"Table: {t_name}({', '.join(col_strs)})")

        schema_description = "\n".join(schema_lines)
        if not schema_description.strip():
            schema_description = "No tables found in database. Please upload data or seed tables."

        return schema_description, tables_meta


async def get_table_sample(pool: asyncpg.Pool, table_name: str, limit: int = 5) -> dict[str, Any]:
    """Returns sample records and column names from a table."""
    if not is_safe_identifier(table_name):
        raise ValueError(f"Invalid table name: {table_name}")

    async with pool.acquire() as conn:
        rows = await conn.fetch(f'SELECT * FROM "{table_name}" LIMIT $1', limit)
        if not rows:
            # Table is empty, fetch column names from information_schema
            col_rows = await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=$1 ORDER BY ordinal_position",
                table_name
            )
            columns = [r["column_name"] for r in col_rows]
            return {"table": table_name, "columns": columns, "rows": [], "count": 0}

        columns = list(rows[0].keys())
        return {
            "table": table_name,
            "columns": columns,
            "rows": [dict(r) for r in rows],
            "count": len(rows)
        }


async def drop_table(pool: asyncpg.Pool, table_name: str) -> None:
    """Safely drops a table."""
    if not is_safe_identifier(table_name):
        raise ValueError(f"Invalid table name: {table_name}")

    async with pool.acquire() as conn:
        await conn.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')
        logger.info("Dropped table %s", table_name)
