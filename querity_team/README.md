# Querity Eval-to-PR AI Team (`querity_team`)

An isolated **Google ADK 2.x** graph workflow and Antigravity skill team that automates Querity's Text-to-SQL evaluation, `sqlglot` AST guardrail hardening, regression testing, and PR preparation—with a deterministic Python verification gate and Human-in-the-Loop (HITL) approval before any Git write.

## What It Solves
- Replaces manual eval expansion, dialect debugging, and regression checking with a repeatable graph:
  `PreFlight` + `Analyst` (parallel) → `Join` → `Engineer` → `Runner` (deterministic) → `DetGate` (deterministic, max 2 rounds) → `Checker` → `Lead` (`HITL`) → `GitPR`.
- Keeps `backend/requirements.txt` and core FastAPI routes decoupled.

## Install & Run in 3 Steps
1. **Create virtual env & install dependencies:**
   ```bash
   uv venv --python 3.12 .venv
   uv pip install --python .venv/Scripts/python.exe -r backend/requirements.txt -r querity_team/requirements.txt
   ```
2. **Set your Gemini API key in `.env`** (never commit `.env`):
   ```env
   GEMINI_API_KEY=your_key_here
   # Optional override (defaults to gemini-4-neon):
   # GEMINI_MODEL=gemini-4-neon
   ```
3. **Run the team or deterministic suite:**
   ```bash
   # Full ADK 2.x workflow (or `adk web` for visual graph UI):
   .venv/Scripts/python.exe -m querity_team.run "Harden sql_guard.py for CTE edge cases"

   # Offline deterministic Runner + DetGate + pytest:
   pytest backend/test_*.py querity_team/tests/ -v
   ```

## Trust Boundary & Security Notes
- When workspace edits exist, `guarded_runner_node` pauses for explicit human confirmation before running any unreviewed tests/code (`run.py` displays the full staged+unstaged diff and complete contents of every new untracked file, blocking approval if any file cannot be inspected).
- The host runner strips API secrets, DB credentials, `HOME`, `USERPROFILE`, and `VIRTUAL_ENV` from child subprocesses, copies code into a temporary workspace without `eval_cases.json` or `.env`, strips `kind`/`gold_sql` and randomizes/shuffles per-case IDs before calling the worker, re-grades every result in the parent process, and hash-checks protected repo directories (`querity_team/`, `.agents/`, `.github/`, `db/`, `.env`) before and after execution.
- **Important limitation (not a container/VM):** Local Python subprocesses still share the host OS user permissions and Python runtime environment (`sys.prefix`), so this is a **host test runner**, not an OS-level security sandbox. Always inspect the full diff before approving local runs, run untrusted agent code in a disposable container/VM without secrets for strong isolation, and connect production PostgreSQL endpoints with a restricted read-only DB role.
