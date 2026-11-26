# Querity Team Memory

Read by `Lead` before every workflow run. Never edited without explicit user approval.

## Known Dialect & Guardrail Rules
- Primary schema (`db/schema.sql`) uses PostgreSQL-style DDL (`SERIAL PRIMARY KEY`, `NUMERIC(10, 2)`), adapted to DuckDB for local deterministic evals.
- `backend/sql_guard.py` enforces single-statement `SELECT`/`WITH` queries, blocks destructive DDL/DML keywords (`DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER`, etc.) using word boundaries so columns like `created_at` are not false-flagged, and appends `LIMIT 50` if omitted.
- Always run on a feature branch (never `main` or `master`).
- `p95_latency_ms` is warning-only in `DetGate` to prevent free-tier Gemini API jitter from causing false-positive retry loops.

## Approved Prompt Tweaks
- *(None yet — weekly improvement loop will propose one change per week after reviewing `runs_log.jsonl`.)*
