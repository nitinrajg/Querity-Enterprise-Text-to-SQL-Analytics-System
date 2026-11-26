"""Deterministic Python nodes for the Querity Eval-to-PR ADK 2.x workflow."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import sqlglot
from google.adk.events import Event, EventActions
from google.adk.tools import ToolContext

from .schemas import BaselineMetrics, CheckerVerdict, EvalReport, MetricDeltas

REPO_ROOT = Path(__file__).resolve().parent.parent
TEAM_DIR = Path(__file__).resolve().parent
BASELINE_PATH = TEAM_DIR / "baseline_metrics.json"
EVAL_CASES_PATH = TEAM_DIR / "eval_cases.json"
TEAM_MEMORY_PATH = TEAM_DIR / "team_memory.md"
RUNS_LOG_PATH = TEAM_DIR / "runs_log.jsonl"
SCHEMA_SQL_PATH = REPO_ROOT / "db" / "schema.sql"

DEFAULT_PYTEST_TARGETS = [
    "backend/test_multitable_joins.py",
    "backend/test_llm_providers.py",
    "backend/test_live_generation.py",
    "backend/test_sql_guard_hardening.py",
]


def _discover_pytest_targets(repo_root: Path) -> list[str]:
    """Return baseline pytest targets plus any new regression tests in backend/."""
    targets = list(DEFAULT_PYTEST_TARGETS)
    backend_dir = repo_root / "backend"
    if backend_dir.exists():
        for test_file in sorted(backend_dir.glob("test_*.py")):
            rel = str(test_file.relative_to(repo_root)).replace("\\", "/")
            if rel not in targets:
                targets.append(rel)
    return targets
def _import_sql_guard():
    """Import validate_sql_safety and SQLSafetyError from backend without polluting sys.path."""
    backend_dir = str(REPO_ROOT / "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from sql_guard import SQLSafetyError, validate_sql_safety  # type: ignore

    return validate_sql_safety, SQLSafetyError


def load_baseline_metrics(path: Path | None = None) -> BaselineMetrics | None:
    """Load persisted baseline_metrics.json if it exists."""
    target = path or BASELINE_PATH
    if not target.exists():
        return None
    return BaselineMetrics.model_validate_json(target.read_text(encoding="utf-8"))


def get_current_git_branch(repo_root: Path | None = None) -> str:
    """Return the active Git branch name."""
    target_root = repo_root or REPO_ROOT
    proc = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=target_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def preflight_check(
    node_input: Any = None,
    repo_root: Path | None = None,
    baseline_path: Path | None = None,
    memory_path: Path | None = None,
) -> dict[str, Any]:
    """Deterministic pre-flight: verify feature branch (never main/master) and load baseline + memory."""
    resolved_root = repo_root or REPO_ROOT
    resolved_baseline = baseline_path or BASELINE_PATH
    resolved_memory = memory_path or TEAM_MEMORY_PATH

    branch = get_current_git_branch(resolved_root)
    if branch in {"main", "master"}:
        raise RuntimeError(
            f"Pre-flight halted: current Git branch is '{branch}'. "
            "Switch to a feature branch before Engineer edits any file."
        )

    baseline = load_baseline_metrics(resolved_baseline)
    memory_text = resolved_memory.read_text(encoding="utf-8") if resolved_memory.exists() else ""
    return {
        "branch": branch,
        "branch_ok": True,
        "baseline_metrics": baseline.model_dump() if baseline else None,
        "team_memory": memory_text,
        "brief": node_input if isinstance(node_input, str) else str(node_input or ""),
    }


_SANDBOX_SAFE_ENV_KEYS = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "OS",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "LANG",
    "LC_ALL",
    "PYTHONIOENCODING",
)

# Child worker acts ONLY as a raw SQL validation oracle: stdout/stderr during untrusted import
# and execution are redirected to a sink, and per-case outputs go to a temp file path on stdin.
# Scoring, canary generation, DuckDB execution, and accuracy checks run ONLY in the parent.
_EVAL_WORKER_SCRIPT = r"""
import contextlib, io, json, sys
from pathlib import Path

payload = json.loads(sys.stdin.read())
sandbox_root = Path(payload["sandbox_root"])
out_path = Path(payload["out_path"])
cases = payload["cases"]

sys.path.insert(0, str(sandbox_root / "backend"))
_sink = io.StringIO()
with contextlib.redirect_stdout(_sink), contextlib.redirect_stderr(_sink):
    from sql_guard import SQLSafetyError, validate_sql_safety

results = []
for case in cases:
    cid = case["id"]
    dialect = case.get("dialect", "duckdb")
    try:
        with contextlib.redirect_stdout(_sink), contextlib.redirect_stderr(_sink):
            validated = validate_sql_safety(case["sql"], dialect=dialect)
        results.append({"id": cid, "status": "ok", "validated_sql": validated})
    except SQLSafetyError as exc:
        results.append({"id": cid, "status": "blocked", "error": str(exc)})
    except Exception as exc:
        results.append({"id": cid, "status": "error", "error": str(exc)})

out_path.write_text(json.dumps({"results": results}), encoding="utf-8")
"""


def _load_duckdb_from_schema(schema_path: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Load db/schema.sql into an in-memory DuckDB connection for trusted parent-process grading."""
    resolved = schema_path or SCHEMA_SQL_PATH
    raw_sql = resolved.read_text(encoding="utf-8")
    adapted = re.sub(r"\bDROP\s+TABLE\s+IF\s+EXISTS\s+(\w+)\s+CASCADE;", r"DROP TABLE IF EXISTS \1;", raw_sql, flags=re.IGNORECASE)
    adapted = re.sub(r"\bON\s+(?:DELETE|UPDATE)\s+(?:CASCADE|SET\s+NULL|SET\s+DEFAULT)\b", "", adapted, flags=re.IGNORECASE)

    conn = duckdb.connect(":memory:", config={"enable_external_access": False})
    seq_idx = 0

    def _replace_serial(match: re.Match[str]) -> str:
        nonlocal seq_idx
        seq_idx += 1
        seq_name = f"seq_serial_{seq_idx}"
        conn.execute(f"CREATE SEQUENCE {seq_name} START 1;")
        return f"INTEGER PRIMARY KEY DEFAULT nextval('{seq_name}')"

    adapted = re.sub(r"\bSERIAL\s+PRIMARY\s+KEY\b", _replace_serial, adapted, flags=re.IGNORECASE)
    for stmt in adapted.split(";"):
        clean = stmt.strip()
        if not clean:
            continue
        executable = "\n".join(ln for ln in clean.splitlines() if not ln.strip().startswith("--")).strip()
        if executable:
            conn.execute(executable)
    return conn


def _sanitized_sandbox_env(sandbox_root: Path) -> dict[str, str]:
    """Build a minimal subprocess env stripped of API secrets, tokens, DB credentials, HOME, and VIRTUAL_ENV."""
    env: dict[str, str] = {}
    for key in _SANDBOX_SAFE_ENV_KEYS:
        if key in os.environ:
            env[key] = os.environ[key]
    tmp_dir = sandbox_root / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    env["TMP"] = str(tmp_dir)
    env["TEMP"] = str(tmp_dir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(sandbox_root)
    return env


def _prepare_temp_eval_workspace(
    temp_root: Path,
    repo_root: Path,
    schema_file: Path,
    targets: list[str],
) -> Path:
    """Populate a disposable temp workspace with ONLY code under test, schemas.py, and schema.sql.

    Intentionally excludes .env, .git, conftest.py, baseline_metrics.json, and eval_cases.json
    so worker code cannot read gold_sql or kind labels from disk.
    """
    backend_dst = temp_root / "backend"
    backend_dst.mkdir(parents=True, exist_ok=True)
    backend_src = repo_root / "backend"
    if backend_src.exists():
        for py_file in backend_src.glob("*.py"):
            if py_file.name in {"conftest.py", "sitecustomize.py", "usercustomize.py"}:
                continue
            shutil.copy2(py_file, backend_dst / py_file.name)

    qt_dst = temp_root / "querity_team"
    qt_dst.mkdir(parents=True, exist_ok=True)
    for rel in ("querity_team/__init__.py", "querity_team/schemas.py"):
        src = repo_root / rel
        if src.exists():
            shutil.copy2(src, temp_root / rel)

    if (repo_root / "pytest.ini").exists():
        shutil.copy2(repo_root / "pytest.ini", temp_root / "pytest.ini")

    db_dst = temp_root / "db"
    db_dst.mkdir(parents=True, exist_ok=True)
    temp_schema = db_dst / "schema.sql"
    shutil.copy2(schema_file, temp_schema)

    for t in targets:
        t_path = repo_root / t
        if t_path.exists():
            dst = temp_root / t
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(t_path, dst)

    return temp_schema


def _fingerprint_protected_files(repo_root: Path) -> dict[Path, str | None]:
    """Hash protected files, all files under querity_team/.agents/.github/db, and .env before/after runs."""
    exact, _, _ = _protected_paths(repo_root)
    watched: set[Path] = set(exact) | {(repo_root / ".env").resolve()}
    for subdir in ("querity_team", ".agents", ".github", "db"):
        dir_path = (repo_root / subdir).resolve()
        if dir_path.exists():
            for p in dir_path.rglob("*"):
                if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
                    watched.add(p.resolve())
    return {p: _sha256_or_none(p) for p in watched}


def get_unapproved_workspace_changes(repo_root: Path | None = None) -> list[str]:
    """Return sorted relative paths of modified/untracked files relevant to code, tests, or evals."""
    root = repo_root or REPO_ROOT
    changed: list[str] = []
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return []
        for line in proc.stdout.splitlines():
            if len(line) <= 3:
                continue
            path_part = line[3:].strip().replace("\\", "/")
            if " -> " in path_part:
                path_part = path_part.split(" -> ", 1)[1].strip()
            if (
                path_part.startswith("backend/")
                or path_part in {"pytest.ini", "conftest.py", "querity_team/eval_cases.json"}
                or path_part.startswith("querity_team/tests/")
                or path_part.startswith(".github/")
            ):
                changed.append(path_part)
    except Exception:
        return []
    return sorted(set(changed))


def _run_pytest_targets(
    targets: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, int, int, str]:
    """Run pytest with --noconftest inside an isolated working directory and sanitized environment."""
    run_cwd = cwd or REPO_ROOT
    run_env = env if env is not None else _sanitized_sandbox_env(run_cwd)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--noconftest", "-o", "addopts=", *targets, "-q", "--tb=short"],
        cwd=run_cwd,
        env=run_env,
        capture_output=True,
        text=True,
    )
    output = f"{proc.stdout}\n{proc.stderr}".strip()
    passed_match = re.search(r"(\d+)\s+passed", output)
    failed_match = re.search(r"(\d+)\s+failed", output)
    error_match = re.search(r"(\d+)\s+error", output)

    passed = int(passed_match.group(1)) if passed_match else 0
    failed = (int(failed_match.group(1)) if failed_match else 0) + (
        int(error_match.group(1)) if error_match else 0
    )
    if proc.returncode != 0 and failed == 0:
        failed = 1
    total = passed + failed
    summary_line = output.splitlines()[-1] if output else f"exit={proc.returncode}"
    return total, passed, failed, summary_line


def _percentile(values: list[float], pct: float) -> float:
    """Compute p-th percentile in milliseconds."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return round(ordered[idx], 3)


def _sha256_or_none(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _grade_eval_cases_trusted(
    cases: list[dict[str, Any]],
    results_by_id: dict[str, dict[str, Any]],
    schema_path: Path,
    canary_safe_id: str,
    canary_safe_val: int,
    canary_unsafe_ids: tuple[str, str],
) -> dict[str, Any]:
    """Verify every individual case outcome in the trusted parent process using our own DuckDB + sqlglot."""
    conn = _load_duckdb_from_schema(schema_path)

    safe_cases = [c for c in cases if c.get("kind") == "safe"]
    unsafe_cases = [c for c in cases if c.get("kind") == "unsafe"]

    syntax_valid_count = 0
    exec_match_count = 0
    postgres_transpile_count = 0
    guardrail_blocked_count = 0
    latencies_ms: list[float] = []
    taxonomy: dict[str, int] = {}
    cat_totals: dict[str, int] = {}
    cat_passed: dict[str, int] = {}

    c_safe_res = results_by_id.get(canary_safe_id)
    if not c_safe_res or c_safe_res.get("status") != "ok":
        taxonomy["canary_safe_failed"] = 1
    else:
        try:
            canary_rows = conn.execute(str(c_safe_res.get("validated_sql", ""))).fetchall()
            if canary_rows != [(1, canary_safe_val)]:
                taxonomy["canary_safe_mismatch"] = 1
        except Exception:
            taxonomy["canary_safe_error"] = 1

    for cid in canary_unsafe_ids:
        c_res = results_by_id.get(cid)
        if not c_res or c_res.get("status") != "blocked":
            taxonomy["canary_unsafe_bypassed"] = taxonomy.get("canary_unsafe_bypassed", 0) + 1

    from sqlglot import exp as _exp

    for case in safe_cases:
        t0 = time.perf_counter()
        cid = case["id"]
        dialect = case.get("dialect", "duckdb")
        cat = case.get("category", "general")
        cat_totals[cat] = cat_totals.get(cat, 0) + 1
        res = results_by_id.get(cid)
        if not res or res.get("status") != "ok":
            taxonomy["guardrail_false_positive"] = taxonomy.get("guardrail_false_positive", 0) + 1
            latencies_ms.append((time.perf_counter() - t0) * 1000.0)
            continue

        validated_sql = str(res.get("validated_sql", ""))
        try:
            parsed = sqlglot.parse_one(validated_sql, read=dialect)
            if not isinstance(parsed, (_exp.Select, _exp.Union, _exp.Intersect, _exp.Except)):
                raise ValueError("Non-read-only SQL returned by guard")
            syntax_valid_count += 1
        except Exception:
            taxonomy["dialect_syntax_error"] = taxonomy.get("dialect_syntax_error", 0) + 1
            latencies_ms.append((time.perf_counter() - t0) * 1000.0)
            continue

        try:
            sqlglot.transpile(validated_sql, read=dialect, write="postgres")
            postgres_transpile_count += 1
        except Exception:
            taxonomy["postgres_transpile_error"] = taxonomy.get("postgres_transpile_error", 0) + 1

        try:
            actual_rows = conn.execute(validated_sql).fetchall()
            gold_rows = conn.execute(case["gold_sql"]).fetchall()
            if actual_rows == gold_rows:
                exec_match_count += 1
                cat_passed[cat] = cat_passed.get(cat, 0) + 1
            else:
                taxonomy["execution_mismatch"] = taxonomy.get("execution_mismatch", 0) + 1
        except Exception:
            taxonomy["execution_error"] = taxonomy.get("execution_error", 0) + 1
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)

    for case in unsafe_cases:
        t0 = time.perf_counter()
        cid = case["id"]
        cat = case.get("category", "adversarial")
        cat_totals[cat] = cat_totals.get(cat, 0) + 1
        res = results_by_id.get(cid)
        if res and res.get("status") == "blocked":
            guardrail_blocked_count += 1
            cat_passed[cat] = cat_passed.get(cat, 0) + 1
        else:
            taxonomy["guardrail_false_negative"] = taxonomy.get("guardrail_false_negative", 0) + 1
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)

    conn.close()

    exec_acc = 0.0 if (taxonomy.get("canary_safe_failed") or taxonomy.get("canary_safe_mismatch") or taxonomy.get("canary_safe_error")) else round((exec_match_count / len(safe_cases) * 100.0) if safe_cases else 100.0, 2)
    block_rate = 0.0 if taxonomy.get("canary_unsafe_bypassed") else round((guardrail_blocked_count / len(unsafe_cases) * 100.0) if unsafe_cases else 100.0, 2)

    return {
        "eval_cases_total": len(cases),
        "syntax_val": round((syntax_valid_count / len(safe_cases) * 100.0) if safe_cases else 100.0, 2),
        "exec_acc": exec_acc,
        "pg_rate": round((postgres_transpile_count / len(safe_cases) * 100.0) if safe_cases else 100.0, 2),
        "block_rate": block_rate,
        "category_accuracy": {k: round(cat_passed.get(k, 0) / total * 100.0, 2) for k, total in sorted(cat_totals.items())},
        "p95_ms": _percentile(latencies_ms, 95.0),
        "taxonomy": taxonomy,
    }


def run_benchmark_and_guardrails(
    node_input: Any = None,
    pytest_targets: list[str] | None = None,
    eval_cases_path: Path | None = None,
    baseline_path: Path | None = None,
    schema_path: Path | None = None,
    round_number: int = 0,
) -> dict[str, Any]:
    """Deterministic Python node: runs pytest in temp sandbox and grades per-case oracle results in parent."""
    targets = pytest_targets if pytest_targets is not None else _discover_pytest_targets(REPO_ROOT)
    cases_file = eval_cases_path or EVAL_CASES_PATH
    resolved_baseline = baseline_path or BASELINE_PATH
    resolved_schema = schema_path or SCHEMA_SQL_PATH

    baseline = load_baseline_metrics(resolved_baseline)
    pre_baseline_hash = _sha256_or_none(resolved_baseline)
    pre_protected_hashes = _fingerprint_protected_files(REPO_ROOT)

    cases: list[dict[str, Any]] = json.loads(cases_file.read_text(encoding="utf-8"))
    canary_safe_val = secrets.randbelow(90000) + 10000
    canary_safe_id = f"internal_canary_safe_{secrets.token_hex(6)}"
    canary_unsafe_dml_id = f"internal_canary_dml_{secrets.token_hex(6)}"
    canary_unsafe_fn_id = f"internal_canary_fn_{secrets.token_hex(6)}"

    all_cases_for_grading = [
        *cases,
        {
            "id": canary_safe_id,
            "kind": "safe",
            "dialect": "duckdb",
            "sql": f"SELECT id, {canary_safe_val} AS canary_val FROM customers ORDER BY id ASC LIMIT 1",
        },
        {
            "id": canary_unsafe_dml_id,
            "kind": "unsafe",
            "dialect": "duckdb",
            "sql": f"DELETE FROM customers WHERE id = {canary_safe_val};",
        },
        {
            "id": canary_unsafe_fn_id,
            "kind": "unsafe",
            "dialect": "postgres",
            "sql": f"SELECT pg_sleep({canary_safe_val % 9 + 1});",
        },
    ]

    # Map every case to a uniform opaque ID and strip kind/gold_sql/question before worker sees it
    opaque_to_real: dict[str, str] = {}
    worker_cases: list[dict[str, str]] = []
    for c in all_cases_for_grading:
        opaque_id = f"q_{secrets.token_hex(8)}"
        opaque_to_real[opaque_id] = str(c["id"])
        worker_cases.append({
            "id": opaque_id,
            "sql": str(c["sql"]),
            "dialect": str(c.get("dialect", "duckdb")),
        })
    secrets.SystemRandom().shuffle(worker_cases)

    with tempfile.TemporaryDirectory(prefix="querity_eval_tmp_") as tmp_dir_str:
        temp_root = Path(tmp_dir_str)
        _prepare_temp_eval_workspace(temp_root, REPO_ROOT, resolved_schema, targets)
        sanitized_env = _sanitized_sandbox_env(temp_root)

        pytest_total, pytest_passed, pytest_failed, pytest_summary = _run_pytest_targets(
            targets, cwd=temp_root, env=sanitized_env
        )

        out_results_path = temp_root / f"_oracle_{secrets.token_hex(8)}.json"
        worker_payload = json.dumps({
            "sandbox_root": str(temp_root),
            "out_path": str(out_results_path),
            "cases": worker_cases,
        })
        worker_proc = subprocess.run(
            [sys.executable, "-c", _EVAL_WORKER_SCRIPT],
            input=worker_payload,
            cwd=temp_root,
            env=sanitized_env,
            capture_output=True,
            text=True,
        )
        if worker_proc.returncode == 0 and out_results_path.exists():
            raw_items = json.loads(out_results_path.read_text(encoding="utf-8")).get("results", [])
            results_by_id = {
                opaque_to_real[str(item["id"])]: item
                for item in raw_items
                if isinstance(item, dict) and str(item.get("id")) in opaque_to_real
            }
        else:
            results_by_id = {}

    post_baseline_hash = _sha256_or_none(resolved_baseline)
    post_protected_hashes = _fingerprint_protected_files(REPO_ROOT)
    if post_baseline_hash != pre_baseline_hash or post_protected_hashes != pre_protected_hashes:
        raise RuntimeError(
            "Host integrity violation: protected repository file or baseline was modified during evaluation."
        )

    graded = _grade_eval_cases_trusted(
        cases=cases,
        results_by_id=results_by_id,
        schema_path=resolved_schema,
        canary_safe_id=canary_safe_id,
        canary_safe_val=canary_safe_val,
        canary_unsafe_ids=(canary_unsafe_dml_id, canary_unsafe_fn_id),
    )

    deltas = MetricDeltas()
    latency_warning: str | None = None
    if baseline is not None:
        deltas = MetricDeltas(
            execution_accuracy_delta=round(graded["exec_acc"] - baseline.execution_accuracy, 2),
            sql_syntax_validity_delta=round(graded["syntax_val"] - baseline.sql_syntax_validity, 2),
            guardrail_block_rate_delta=round(graded["block_rate"] - baseline.guardrail_block_rate, 2),
            p95_latency_ms_delta=round(graded["p95_ms"] - baseline.p95_latency_ms, 3),
        )
        if baseline.p95_latency_ms > 0 and graded["p95_ms"] > baseline.p95_latency_ms * 1.25:
            latency_warning = (
                f"Warning: p95 latency {graded['p95_ms']}ms exceeded baseline "
                f"{baseline.p95_latency_ms}ms by >25% (warning-only; no FAIL triggered)."
            )

    modified_files = get_unapproved_workspace_changes(REPO_ROOT)

    report = EvalReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        pytest_total=pytest_total,
        pytest_passed=pytest_passed,
        pytest_failed=pytest_failed,
        eval_cases_total=int(graded["eval_cases_total"]),
        execution_accuracy=graded["exec_acc"],
        sql_syntax_validity=graded["syntax_val"],
        guardrail_block_rate=graded["block_rate"],
        postgres_transpile_rate=graded["pg_rate"],
        category_accuracy=graded["category_accuracy"],
        p95_latency_ms=graded["p95_ms"],
        failure_taxonomy=graded["taxonomy"],
        baseline_deltas=deltas,
        latency_warning=latency_warning,
        raw_pytest_summary=pytest_summary,
        requires_human_review=bool(modified_files),
        modified_exam_files=modified_files,
    )

    round_num = int(round_number or 0)

    return {
        "eval_report": report.model_dump(),
        "round_number": round_num,
        "engineer_output": node_input,
    }


def evaluate_deterministic_gate(
    payload: dict[str, Any],
    baseline_path: Path | None = None,
) -> tuple[str, CheckerVerdict, int]:
    """Pure-Python gate logic returning (route, CheckerVerdict, next_round).

    - Enforces round >= 2 cap: if failing at round >= 2, returns route='escalate'.
    - Keeps p95_latency_ms as warning-only (never causes FAIL).
    """
    report = EvalReport.model_validate(payload["eval_report"])
    current_round = int(payload.get("round_number", 0))
    baseline = load_baseline_metrics(baseline_path or BASELINE_PATH)

    failures: list[str] = []
    if report.pytest_failed > 0:
        failures.append(f"pytest failed ({report.pytest_failed}/{report.pytest_total}): {report.raw_pytest_summary}")

    if baseline is not None:
        if baseline.eval_cases_total > 0 and report.eval_cases_total < baseline.eval_cases_total:
            failures.append(
                f"eval_cases_total shrank: {report.eval_cases_total} < baseline {baseline.eval_cases_total}"
            )
        if report.execution_accuracy + 1e-6 < baseline.execution_accuracy:
            failures.append(
                f"execution_accuracy regressed: {report.execution_accuracy}% < baseline {baseline.execution_accuracy}%"
            )
        if report.sql_syntax_validity + 1e-6 < baseline.sql_syntax_validity:
            failures.append(
                f"sql_syntax_validity regressed: {report.sql_syntax_validity}% < baseline {baseline.sql_syntax_validity}%"
            )
        if report.guardrail_block_rate + 1e-6 < baseline.guardrail_block_rate:
            failures.append(
                f"guardrail_block_rate regressed: {report.guardrail_block_rate}% < baseline {baseline.guardrail_block_rate}%"
            )

    if failures:
        if current_round >= 2:
            verdict = CheckerVerdict(
                status="FAIL",
                score=max(0, 100 - 25 * len(failures)),
                target_agent="none",
                numbered_fixes=failures,
                round_number=2,
                reason="Deterministic gate failed at max round 2; escalating to Lead -> HITL.",
            )
            return "escalate", verdict, 2

        next_round = current_round + 1
        verdict = CheckerVerdict(
            status="FAIL",
            score=max(0, 100 - 25 * len(failures)),
            target_agent="engineer",
            numbered_fixes=failures,
            round_number=next_round,
            reason=f"Deterministic gate failed (round {next_round}/2); routing back to Engineer.",
        )
        return "retry_engineer", verdict, next_round

    verdict = CheckerVerdict(
        status="PASS",
        score=100,
        target_agent="none",
        numbered_fixes=[],
        round_number=current_round,
        reason="Deterministic gate passed (pytest + sqlglot + baseline checks OK).",
    )
    return "checker", verdict, current_round


def deterministic_gate_node(node_input: dict[str, Any], round_number: int = 0) -> Event:
    """ADK Workflow router node wrapping evaluate_deterministic_gate.

    If pre-execution human approval was rejected (node_input lacks 'eval_report'),
    halt cleanly by routing to 'escalate' (Lead) without retrying Engineer.
    """
    current_round = int(round_number or 0)
    if not isinstance(node_input, dict) or "eval_report" not in node_input:
        reason = (
            str(node_input.get("error", ""))
            if isinstance(node_input, dict) and node_input.get("error")
            else "Execution rejected or missing EvalReport; stopping cleanly without retry."
        )
        verdict = CheckerVerdict(
            status="FAIL",
            score=0,
            target_agent="none",
            numbered_fixes=[reason],
            round_number=min(2, current_round),
            reason=reason,
        )
        modified_files = get_unapproved_workspace_changes(REPO_ROOT)
        out = {
            "rejected": True,
            "det_verdict": verdict.model_dump(),
            "round_number": current_round,
            "requires_human_review": True,
            "modified_exam_files": modified_files,
        }
        return Event(
            output=out,
            actions=EventActions(
                route="escalate",
                state_delta={
                    "execution_rejected": True,
                    "det_verdict": verdict.model_dump(),
                    "requires_human_review": True,
                    "modified_exam_files": modified_files,
                },
            ),
        )

    effective_round = int(node_input.get("round_number", current_round))
    payload = {**node_input, "round_number": effective_round}
    route, verdict, next_round = evaluate_deterministic_gate(payload)
    eval_report = payload["eval_report"]
    requires_review = bool(eval_report.get("requires_human_review", False))
    modified_files = list(eval_report.get("modified_exam_files", []))
    out = {
        "eval_report": eval_report,
        "det_verdict": verdict.model_dump(),
        "round_number": next_round,
        "requires_human_review": requires_review,
        "modified_exam_files": modified_files,
    }
    return Event(
        output=out,
        actions=EventActions(
            route=route,
            state_delta={
                "execution_rejected": False,
                "round_number": next_round,
                "eval_report": eval_report,
                "requires_human_review": requires_review,
                "modified_exam_files": modified_files,
            },
        ),
    )


def log_run_event(
    eval_report: dict[str, Any],
    verdict: dict[str, Any],
    branch: str = "",
    log_path: Path = RUNS_LOG_PATH,
) -> dict[str, Any]:
    """Append a structured run entry to querity_team/runs_log.jsonl."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "branch": branch or get_current_git_branch(),
        "eval_report": eval_report,
        "verdict": verdict,
    }
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return {"logged": True, "log_path": str(log_path)}


def _is_env_secret_path(target: Path) -> bool:
    """Return True if path refers to a .env secret file or directory (allows .env.example)."""
    if target.name == ".env" or (target.name.startswith(".env.") and target.name != ".env.example"):
        return True
    return any(p.name == ".env" for p in target.parents)


def read_repo_file(relative_path: str) -> str:
    """Read a file inside the repository (read-only)."""
    repo_root = REPO_ROOT.resolve()
    target = (repo_root / relative_path).resolve()
    if not target.is_relative_to(repo_root):
        raise ValueError("Access denied outside repository root.")
    if _is_env_secret_path(target):
        raise ValueError(f"Refusing to read protected path: {relative_path}")
    if not target.exists():
        return f"ERROR: File not found: {relative_path}"
    return target.read_text(encoding="utf-8")


def _protected_paths(repo_root: Path) -> tuple[set[Path], list[Path], Path]:
    """Resolve protected files and directories relative to repo_root."""
    exact = {
        (repo_root / "pytest.ini").resolve(),
        (repo_root / "conftest.py").resolve(),
        (repo_root / "backend" / "conftest.py").resolve(),
        (repo_root / "backend" / "sitecustomize.py").resolve(),
        (repo_root / "backend" / "usercustomize.py").resolve(),
        (repo_root / "backend" / "__init__.py").resolve(),
        (repo_root / "backend" / "main.py").resolve(),
        (repo_root / "backend" / "duckdb_service.py").resolve(),
        (repo_root / "backend" / "ingestion.py").resolve(),
        (repo_root / "backend" / "schema_service.py").resolve(),
        (repo_root / "backend" / "nvidia_client.py").resolve(),
        (repo_root / "backend" / "requirements.txt").resolve(),
        (repo_root / "backend" / "test_sql_guard_hardening.py").resolve(),
        (repo_root / "backend" / "test_multitable_joins.py").resolve(),
        (repo_root / "backend" / "test_llm_providers.py").resolve(),
        (repo_root / "backend" / "test_live_generation.py").resolve(),
        (repo_root / "querity_team" / "__init__.py").resolve(),
        (repo_root / "querity_team" / "baseline_metrics.json").resolve(),
        (repo_root / "querity_team" / "deterministic_nodes.py").resolve(),
        (repo_root / "querity_team" / "agent.py").resolve(),
        (repo_root / "querity_team" / "schemas.py").resolve(),
        (repo_root / "querity_team" / "seed_baseline.py").resolve(),
        (repo_root / "querity_team" / "run.py").resolve(),
        (repo_root / "querity_team" / "build_dashboard.py").resolve(),
        (repo_root / "querity_team" / "requirements.txt").resolve(),
        (repo_root / "querity_team" / "README.md").resolve(),
        (repo_root / "querity_team" / "team_memory.md").resolve(),
        (repo_root / "querity_team" / "runs_log.jsonl").resolve(),
    }
    prefixes = [
        (repo_root / "querity_team" / "tests").resolve(),
        (repo_root / ".git").resolve(),
        (repo_root / ".github").resolve(),
        (repo_root / ".venv").resolve(),
        (repo_root / ".agents").resolve(),
    ]
    eval_cases = (repo_root / "querity_team" / "eval_cases.json").resolve()
    return exact, prefixes, eval_cases


def _validate_eval_cases_append_only(eval_cases_path: Path, new_content: str) -> None:
    """Prevent replacing or weakening existing baseline eval cases in eval_cases.json."""
    try:
        incoming_cases = json.loads(new_content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"eval_cases.json must be valid JSON: {exc}") from exc
    if not isinstance(incoming_cases, list):
        raise ValueError("eval_cases.json must be a JSON array.")

    existing_cases = json.loads(eval_cases_path.read_text(encoding="utf-8"))
    incoming_by_id = {c.get("id"): c for c in incoming_cases if isinstance(c, dict) and c.get("id")}
    for old_case in existing_cases:
        cid = old_case.get("id")
        if cid not in incoming_by_id:
            raise ValueError(f"Refusing to delete or rename protected baseline eval case: {cid}")
        new_case = incoming_by_id[cid]
        for field in ("kind", "dialect", "sql", "gold_sql"):
            if new_case.get(field) != old_case.get(field):
                raise ValueError(f"Refusing to mutate protected eval case '{cid}' field '{field}'.")


def write_repo_file(relative_path: str, content: str) -> dict[str, Any]:
    """Write or update an allowlisted file inside the repository.

    Uses ``Path.resolve()`` and ``Path.is_relative_to()`` for containment so sibling
    directory prefixes (e.g. ``../copy-evil/x``) and dot-segment traversal are blocked.
    Only ``backend/sql_guard.py``, new ``backend/test_*.py`` regression files, and
    append-only updates to ``querity_team/eval_cases.json`` are permitted.
    """
    repo_root = REPO_ROOT.resolve()
    target = (repo_root / relative_path).resolve()

    if not target.is_relative_to(repo_root):
        raise ValueError("Access denied outside repository root.")

    if _is_env_secret_path(target):
        raise ValueError(f"Refusing to write protected path: {relative_path}")

    protected_exact, protected_prefixes, eval_cases_path = _protected_paths(repo_root)
    if target in protected_exact or any(target.is_relative_to(prefix) for prefix in protected_prefixes):
        raise ValueError(f"Refusing to write protected path: {relative_path}")

    sql_guard_path = (repo_root / "backend" / "sql_guard.py").resolve()
    backend_dir = (repo_root / "backend").resolve()
    is_new_regression_test = (
        target.parent == backend_dir
        and bool(re.fullmatch(r"test_[a-zA-Z0-9_]+\.py", target.name))
    )
    is_sql_guard = target == sql_guard_path
    is_eval_cases = target == eval_cases_path
    if not (is_sql_guard or is_new_regression_test or is_eval_cases):
        raise ValueError(f"Refusing to write protected path: {relative_path}")

    if is_eval_cases and eval_cases_path.exists():
        _validate_eval_cases_append_only(eval_cases_path, content)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"written": relative_path, "bytes": len(content.encode("utf-8"))}


def get_full_review_diff(repo_root: Path | None = None) -> tuple[str, bool]:
    """Return full staged + unstaged tracked diff and complete contents of every untracked new file.

    Returns ``(diff_text, True)`` when every change was completely inspected, or
    ``(error_message, False)`` if any change is unreadable/binary or git inspection fails.
    """
    root = (repo_root or REPO_ROOT).resolve()
    status = subprocess.run(
        ["git", "status", "--porcelain", "-uall"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        return (f"ERROR: git status failed: {status.stderr.strip()}", False)

    head_check = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=root, capture_output=True, text=True)
    if head_check.returncode == 0:
        diff_proc = subprocess.run(["git", "diff", "HEAD"], cwd=root, capture_output=True, text=True)
        if diff_proc.returncode != 0:
            return (f"ERROR: git diff HEAD failed: {diff_proc.stderr.strip()}", False)
        tracked_diff = diff_proc.stdout
    else:
        staged = subprocess.run(["git", "diff", "--cached"], cwd=root, capture_output=True, text=True)
        unstaged = subprocess.run(["git", "diff"], cwd=root, capture_output=True, text=True)
        if staged.returncode != 0 or unstaged.returncode != 0:
            return ("ERROR: git diff failed.", False)
        tracked_diff = f"{staged.stdout}\n{unstaged.stdout}".strip()

    new_file_sections: list[str] = []
    for line in status.stdout.splitlines():
        if not line.startswith("?? "):
            continue
        rel = line[3:].strip().replace("\\", "/")
        if rel.startswith('"') and rel.endswith('"'):
            rel = rel[1:-1]
        target = (root / rel).resolve()
        if not target.is_relative_to(root):
            return (f"ERROR: Untracked path escapes repo root: '{rel}'.", False)
        if not target.is_file():
            return (f"ERROR: Untracked entry '{rel}' is not a readable file.", False)
        try:
            content = target.read_text(encoding="utf-8")
        except Exception as exc:
            return (f"ERROR: Cannot inspect unreadable/binary new file '{rel}': {exc}", False)
        new_file_sections.append(f"=== NEW FILE: {rel} ({len(content.encode('utf-8'))} bytes) ===\n{content}")

    sections = [f"STATUS:\n{status.stdout.rstrip()}"]
    if tracked_diff.strip():
        sections.append(f"TRACKED DIFF (STAGED + UNSTAGED):\n{tracked_diff.rstrip()}")
    if new_file_sections:
        sections.append("UNTRACKED NEW FILES:\n" + "\n\n".join(new_file_sections))
    return ("\n\n".join(sections), True)


def get_git_diff() -> str:
    """Return full git status, staged/unstaged diff, and untracked file contents (read-only)."""
    review_text, _ok = get_full_review_diff(REPO_ROOT)
    return review_text


def git_pr_tool(
    action: str,
    commit_message: str = "",
    pr_title: str = "",
    pr_body: str = "",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Guarded Git/PR tool requiring explicit HITL confirmation before any git commit or gh pr create.

    Enforces ``requires_human_review``: refuses to run without an approved ``ToolContext``,
    and lists modified code/test/eval files inside the confirmation prompt when present.
    """
    if action not in {"commit", "create_pr"}:
        return {"status": "error", "message": f"Unsupported action: {action}"}

    if tool_context is None:
        return {
            "status": "blocked_requires_human_approval",
            "action": action,
            "message": "Explicit HITL ToolContext confirmation is required before commit or PR creation.",
        }

    if isinstance(tool_context.state, dict) and tool_context.state.get("execution_rejected"):
        return {
            "status": "blocked_execution_rejected",
            "action": action,
            "message": "Workflow execution was rejected by the user; commit and PR creation are disabled.",
        }

    eval_report = tool_context.state.get("eval_report", {}) if isinstance(tool_context.state, dict) else {}
    requires_review = bool(eval_report.get("requires_human_review", False))
    modified_files = list(eval_report.get("modified_exam_files", [])) or get_unapproved_workspace_changes()
    if modified_files:
        requires_review = True

    if not tool_context.tool_confirmation:
        review_suffix = f" [HUMAN REVIEW REQUIRED for: {', '.join(modified_files)}]" if requires_review and modified_files else ""
        tool_context.request_confirmation(
            hint=f"Approve `{action}` on branch `{get_current_git_branch()}`?{review_suffix}",
            payload={
                "action": action,
                "commit_message": commit_message,
                "pr_title": pr_title,
                "requires_human_review": requires_review,
                "modified_files": modified_files,
            },
        )
        return {
            "status": "awaiting_human_approval",
            "action": action,
            "requires_human_review": requires_review,
            "modified_files": modified_files,
        }

    if not tool_context.tool_confirmation.confirmed:
        return {"status": "rejected_by_human", "action": action}

    if action == "commit":
        subprocess.run(["git", "add", "-A"], cwd=REPO_ROOT, check=True)
        proc = subprocess.run(
            ["git", "commit", "-m", commit_message or "chore: querity eval-to-pr update"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        return {"status": "committed" if proc.returncode == 0 else "error", "output": proc.stdout + proc.stderr}

    proc = subprocess.run(
        ["gh", "pr", "create", "--title", pr_title or "Querity Eval Update", "--body", pr_body or "Automated eval summary"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return {"status": "pr_created" if proc.returncode == 0 else "error", "output": proc.stdout + proc.stderr}
