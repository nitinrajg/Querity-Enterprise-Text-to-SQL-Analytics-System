"""
Dynamic database schema introspection, relationship inference, and table management for PostgreSQL.
Provides live schema context, primary/foreign keys, and join paths to the LLM agent.
"""

import re
import logging
from typing import Any, Dict, List, Set, Tuple
import asyncpg

logger = logging.getLogger("text2sql")

# Pattern for safe SQL table and column identifiers (letters, digits, underscore)
SAFE_IDENTIFIER_REGEX = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")


def is_safe_identifier(name: str) -> bool:
    return bool(SAFE_IDENTIFIER_REGEX.match(name))


async def introspect_database_schema(pool: asyncpg.Pool) -> tuple[str, list[dict[str, Any]]]:
    """
    Queries PostgreSQL system catalogs to build:
    1. A rich, relationship-aware schema description string for LLM system prompt.
    2. A structured metadata list for the UI schema drawer.
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

    query_primary_keys = """
        SELECT
            kcu.table_name,
            kcu.column_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = 'public';
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
        pk_rows = await conn.fetch(query_primary_keys)
        fk_rows = await conn.fetch(query_foreign_keys)

        # Primary keys map: table_name -> set of pk_column_names
        pk_map: Dict[str, Set[str]] = {}
        for r in pk_rows:
            pk_map.setdefault(r["table_name"], set()).add(r["column_name"])

        # Explicit Foreign keys map: (src_table, src_column) -> "target_table.target_column"
        fk_map: Dict[Tuple[str, str], str] = {}
        explicit_relationships: List[str] = []
        for fk in fk_rows:
            src_t, src_c = fk["src_table"], fk["src_column"]
            tgt_t, tgt_c = fk["target_table"], fk["target_column"]
            fk_map[(src_t, src_c)] = f"{tgt_t}.{tgt_c}"
            explicit_relationships.append(f"• {src_t}.{src_c} = {tgt_t}.{tgt_c} (FK Constraint)")

        # Group columns by table
        tables_dict: Dict[str, List[Dict[str, Any]]] = {}
        for row in col_rows:
            t_name = row["table_name"]
            if t_name not in tables_dict:
                tables_dict[t_name] = []

            c_name = row["column_name"]
            fk_target = fk_map.get((t_name, c_name))
            is_pk = c_name in pk_map.get(t_name, set())

            tables_dict[t_name].append({
                "name": c_name,
                "type": row["data_type"],
                "nullable": row["is_nullable"] == "YES",
                "primary_key": is_pk,
                "foreign_key": fk_target
            })

        # Infer relationships if explicit FKs are absent (common in imported CSVs/Excel)
        inferred_relationships: List[str] = []
        all_tables = list(tables_dict.keys())

        for i, src_t in enumerate(all_tables):
            src_cols = {c["name"]: c for c in tables_dict[src_t]}
            for tgt_t in all_tables:
                if src_t == tgt_t:
                    continue
                tgt_cols = {c["name"]: c for c in tables_dict[tgt_t]}

                # Rule 1: src_col is <singular_target>_id or <target>_id (e.g. customer_id -> customers.id)
                for c_name in src_cols:
                    if (src_t, c_name) in fk_map:
                        continue  # already mapped by explicit constraint

                    # Check for patterns like customer_id, order_id, user_id, product_id, dept_id
                    if c_name.endswith("_id") or c_name.endswith("_code") or c_name.endswith("_key"):
                        prefix = re.sub(r"(_id|_code|_key)$", "", c_name).lower()
                        # Match target table variations: prefix, prefix+'s', prefix+'_data', prefix+'_table'
                        possible_targets = [prefix, f"{prefix}s", f"{prefix}es", f"{prefix}_data", f"{prefix}_table"]
                        if tgt_t.lower() in possible_targets:
                            # Target column could be 'id', '<tgt>_id', or same col name
                            target_candidates = ["id", c_name, f"{prefix}_id"]
                            for cand in target_candidates:
                                if cand in tgt_cols:
                                    fk_map[(src_t, c_name)] = f"{tgt_t}.{cand}"
                                    src_cols[c_name]["foreign_key"] = f"{tgt_t}.{cand}"
                                    rel_str = f"• {src_t}.{c_name} = {tgt_t}.{cand} (Inferred Join Key)"
                                    if rel_str not in inferred_relationships:
                                        inferred_relationships.append(rel_str)
                                    break

                    # Rule 2: exact non-generic matching column name between tables (e.g. order_id, sku, email)
                    elif c_name in tgt_cols and c_name not in ("id", "created_at", "updated_at", "name", "status", "type", "description"):
                        if (src_t, c_name) not in fk_map and (tgt_t, c_name) not in fk_map:
                            rel_str = f"• {src_t}.{c_name} = {tgt_t}.{c_name} (Shared Attribute Join Key)"
                            if rel_str not in inferred_relationships:
                                inferred_relationships.append(rel_str)

        # Build schema description with sample values for low-cardinality categorical columns
        tables_meta: List[Dict[str, Any]] = []
        schema_sections: List[str] = []

        for t_name, cols in tables_dict.items():
            # Get row count safely
            try:
                count_res = await conn.fetchval(f'SELECT count(*) FROM "{t_name}"')
                row_count = int(count_res) if count_res is not None else 0
            except Exception:
                row_count = 0

            # Inspect sample values for text/enum columns to help LLM with exact value filters
            col_lines = []
            for col in cols:
                c_name = col["name"]
                c_type = col["type"]
                flags = []
                if col.get("primary_key"):
                    flags.append("PK")
                if col.get("foreign_key"):
                    flags.append(f"FK -> {col['foreign_key']}")

                flag_str = f" [{', '.join(flags)}]" if flags else ""

                # Try fetching top 3 distinct sample values for text columns with <= 100 rows
                sample_str = ""
                if "text" in c_type or "char" in c_type:
                    try:
                        distinct_samples = await conn.fetch(
                            f'SELECT DISTINCT "{c_name}" FROM "{t_name}" WHERE "{c_name}" IS NOT NULL LIMIT 4'
                        )
                        vals = [str(r[0]) for r in distinct_samples if r[0] is not None]
                        if vals and len(vals) <= 4:
                            sample_str = f" (e.g. {', '.join([repr(v) for v in vals[:3]])})"
                    except Exception:
                        pass

                col_lines.append(f"    - {c_name}: {c_type}{flag_str}{sample_str}")

            tables_meta.append({
                "name": t_name,
                "columns": cols,
                "row_count": row_count
            })

            table_block = f"Table: \"{t_name}\" ({row_count} rows)\n" + "\n".join(col_lines)
            schema_sections.append(table_block)

        # Assemble full prompt schema
        all_relationships = explicit_relationships + inferred_relationships
        relationships_section = ""
        if all_relationships:
            relationships_section = "\n\nAvailable Join Relationships:\n" + "\n".join(all_relationships)

        schema_description = "\n\n".join(schema_sections) + relationships_section
        if not schema_description.strip():
            schema_description = "No tables found in database. Please upload datasets or seed tables."

        return schema_description, tables_meta


async def get_table_sample(pool: asyncpg.Pool, table_name: str, limit: int = 5) -> dict[str, Any]:
    """Returns sample records and column names from a table."""
    if not is_safe_identifier(table_name):
        raise ValueError(f"Invalid table name: {table_name}")

    async with pool.acquire() as conn:
        rows = await conn.fetch(f'SELECT * FROM "{table_name}" LIMIT $1', limit)
        if not rows:
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
