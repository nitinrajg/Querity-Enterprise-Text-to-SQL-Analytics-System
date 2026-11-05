"""
Database setup and teardown for Querity evaluation suite.
Ensures evaluation runs against an isolated database or dedicated schema,
never touching existing tables or production data.
"""

import os
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from dotenv import load_dotenv
import asyncpg

# Load environment from backend/.env
env_path = Path(__file__).resolve().parent.parent / "backend" / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

BASE_DB_URL = os.environ.get("DATABASE_URL", "").strip()
SCHEMA_FILE = Path(__file__).resolve().parent.parent / "db" / "schema.sql"

# Dedicated evaluation schema or database
EVAL_DB_NAME = "text2sql_eval"
EVAL_SCHEMA_NAME = "eval_ecommerce"


def mask_url(url: str) -> str:
    """Masks password in connection string for safe printing."""
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.password:
        netloc = f"{parsed.username}:***@{parsed.hostname}"
        if parsed.port:
            netloc += f":{parsed.port}"
        return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    return url


def get_eval_db_url(eval_db_name: str = EVAL_DB_NAME) -> str:
    """Returns database URL pointing to the eval database."""
    if not BASE_DB_URL:
        raise ValueError("DATABASE_URL is not set in backend/.env")
    parsed = urlparse(BASE_DB_URL)
    new_path = f"/{eval_db_name}"
    eval_url = urlunparse((
        parsed.scheme,
        parsed.netloc,
        new_path,
        parsed.params,
        parsed.query,
        parsed.fragment
    ))
    return eval_url


async def setup_eval_environment() -> dict:
    """
    Sets up the evaluation environment.
    Attempts:
    1. Try creating and connecting to a dedicated database 'text2sql_eval'.
    2. If cloud provider (like Neon pooled connection) disallows CREATE DATABASE,
       falls back to creating an isolated schema 'eval_ecommerce' in the current database.
    Applies db/schema.sql into this isolated environment.
    """
    if not BASE_DB_URL:
        raise ValueError("DATABASE_URL not found in environment.")

    print(f"[setup_eval_db] Connecting to base PostgreSQL...")
    conn = await asyncpg.connect(BASE_DB_URL)
    
    can_create_db = False
    mode = "database"
    target_url = None

    try:
        # Check if CREATE DATABASE is allowed
        db_exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", EVAL_DB_NAME
        )
        if not db_exists:
            print(f"[setup_eval_db] Attempting to CREATE DATABASE {EVAL_DB_NAME}...")
            # CREATE DATABASE cannot run in transaction
            await conn.execute(f'CREATE DATABASE "{EVAL_DB_NAME}"')
            print(f"[setup_eval_db] Created database '{EVAL_DB_NAME}' successfully.")
        can_create_db = True
        target_url = get_eval_db_url(EVAL_DB_NAME)
    except Exception as e:
        print(f"[setup_eval_db] Note: Database creation not permitted on this server/pooler ({e}).")
        print(f"[setup_eval_db] Falling back to dedicated isolated schema: '{EVAL_SCHEMA_NAME}'")
        mode = "schema"
    finally:
        await conn.close()

    # Now connect to the target environment
    if mode == "database":
        target_conn = await asyncpg.connect(target_url)
    else:
        target_conn = await asyncpg.connect(BASE_DB_URL)
        # Create dedicated schema and set search_path
        await target_conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{EVAL_SCHEMA_NAME}";')
        await target_conn.execute(f'SET search_path TO "{EVAL_SCHEMA_NAME}", public;')

    # Load and execute db/schema.sql
    print(f"[setup_eval_db] Seeding schema and data from {SCHEMA_FILE}...")
    with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    # If schema mode, set search_path first
    if mode == "schema":
        schema_sql = f'SET search_path TO "{EVAL_SCHEMA_NAME}", public;\n' + schema_sql

    await target_conn.execute(schema_sql)

    # Verify tables created
    if mode == "database":
        tables = await target_conn.fetch("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """)
    else:
        tables = await target_conn.fetch("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = $1 AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """, EVAL_SCHEMA_NAME)

    table_names = [r["table_name"] for r in tables]
    print(f"[setup_eval_db] Verified tables in eval environment ({mode}): {table_names}")

    # Row counts verification
    counts = {}
    for t in table_names:
        prefix = f'"{EVAL_SCHEMA_NAME}"."' if mode == "schema" else '"'
        c = await target_conn.fetchval(f'SELECT count(*) FROM {prefix}{t}"')
        counts[t] = c
    print(f"[setup_eval_db] Table row counts: {counts}")

    await target_conn.close()

    return {
        "status": "success",
        "mode": mode,
        "eval_db_name": EVAL_DB_NAME if mode == "database" else None,
        "eval_schema_name": EVAL_SCHEMA_NAME if mode == "schema" else None,
        "db_url": target_url if mode == "database" else BASE_DB_URL,
        "tables": table_names,
        "counts": counts
    }


async def teardown_eval_environment():
    """Drops the evaluation database or schema."""
    conn = await asyncpg.connect(BASE_DB_URL)
    try:
        print(f"[setup_eval_db] Tearing down evaluation schema '{EVAL_SCHEMA_NAME}'...")
        await conn.execute(f'DROP SCHEMA IF EXISTS "{EVAL_SCHEMA_NAME}" CASCADE;')
        print(f"[setup_eval_db] Teardown complete.")
    finally:
        await conn.close()


if __name__ == "__main__":
    import asyncio
    if len(sys.argv) > 1 and sys.argv[1] == "teardown":
        asyncio.run(teardown_eval_environment())
    else:
        res = asyncio.run(setup_eval_environment())
        res_safe = dict(res)
        res_safe["db_url"] = mask_url(res_safe.get("db_url", ""))
        print(f"[setup_eval_db] Ready for evaluation: {res_safe}")
