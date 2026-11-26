"""Deterministic Python nodes for the Querity Eval-to-PR ADK 2.x workflow."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
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


def _import_sql_guard():
    """Import validate_sql_safety and SQLSafetyError from backend without polluting sys.path."""
    backend_dir = str(REPO_ROOT / "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from sql_guard import SQLSafetyError, validate_sql_safety  # type: ignore

    return validate_sql_safety, SQLSafetyError


def load_baseline_metrics(path: Path = BASELINE_PATH) -> BaselineMetrics | None:
    """Load persisted baseline_metrics.json if it exists."""
    if not path.exists():
        return None
    return BaselineMetrics.model_validate_json(path.read_text(encoding="utf-8"))


def get_current_git_branch(repo_root: Path = REPO_ROOT) -> str:
    """Return the active Git branch name."""
    proc = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def preflight_check(
    node_input: Any = None,
    repo_root: Path = REPO_ROOT,
    baseline_path: Path = BASELINE_PATH,
    memory_path: Path = TEAM_MEMORY_PATH,
) -> dict[str, Any]:
    """Deterministic pre-flight: verify feature branch (never main/master) and load baseline + memory."""
    branch = get_current_git_branch(repo_root)
    if branch in {"main", "master"}:
        raise RuntimeError(
            f"Pre-flight halted: current Git branch is '{branch}'. "
            "Switch to a feature branch before Engineer edits any file."
        )

    baseline = load_baseline_metrics(baseline_path)
    memory_text = memory_path.read_text(encoding="utf-8") if memory_path.exists() else ""
    return {
        "branch": branch,
        "branch_ok": True,
        "baseline_metrics": baseline.model_dump() if baseline else None,
        "team_memory": memory_text,
        "brief": node_input if isinstance(node_input, str) else str(node_input or ""),
    }


def _load_duckdb_from_schema(schema_path: Path = SCHEMA_SQL_PATH) -> duckdb.DuckDBPyConnection:
    """Load db/schema.sql into an in-memory DuckDB connection for deterministic SQL evals."""
    raw_sql = schema_path.read_text(encoding="utf-8")
    adapted = re.sub(r"\bDROP\s+TABLE\s+IF\s+EXISTS\s+(\w+)\s+CASCADE;", r"DROP TABLE IF EXISTS \1;", raw_sql, flags=re.IGNORECASE)
    adapted = re.sub(r"\bON\s+(?:DELETE|UPDATE)\s+(?:CASCADE|SET\s+NULL|SET\s+DEFAULT)\b", "", adapted, flags=re.IGNORECASE)

    conn = duckdb.connect(":memory:")
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
        non_comment_lines = [ln for ln in clean.splitlines() if not ln.strip().startswith("--")]
        executable = "\n".join(non_comment_lines).strip()
        if executable:
            conn.execute(executable)
    return conn


def _run_pytest_targets(targets: list[str], repo_root: Path = REPO_ROOT) -> tuple[int, int, int, str]:
    """Run pytest on target test files and parse passed/failed counts."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *targets, "-q", "--tb=short"],
        cwd=repo_root,
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


def run_benchmark_and_guardrails(
    node_input: Any = None,
    pytest_targets: list[str] | None = None,
    eval_cases_path: Path = EVAL_CASES_PATH,
    baseline_path: Path = BASELINE_PATH,
    schema_path: Path = SCHEMA_SQL_PATH,
) -> dict[str, Any]:
    """Deterministic Python node: runs pytest, sqlglot, and DuckDB evals, emitting EvalReport JSON."""
    validate_sql_safety, SQLSafetyError = _import_sql_guard()
    targets = pytest_targets if pytest_targets is not None else DEFAULT_PYTEST_TARGETS
    pytest_total, pytest_passed, pytest_failed, pytest_summary = _run_pytest_targets(targets)

    cases = json.loads(eval_cases_path.read_text(encoding="utf-8"))
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

    for case in safe_cases:
        t0 = time.perf_counter()
        raw_sql = case["sql"]
        gold_sql = case["gold_sql"]
        dialect = case.get("dialect", "duckdb")
        cat = case.get("category", "general")
        cat_totals[cat] = cat_totals.get(cat, 0) + 1
        try:
            validated_sql = validate_sql_safety(raw_sql)
        except SQLSafetyError:
            taxonomy["guardrail_false_positive"] = taxonomy.get("guardrail_false_positive", 0) + 1
            latencies_ms.append((time.perf_counter() - t0) * 1000.0)
            continue

        try:
            sqlglot.parse_one(validated_sql, read=dialect)
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
            gold_rows = conn.execute(gold_sql).fetchall()
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
        cat = case.get("category", "adversarial")
        cat_totals[cat] = cat_totals.get(cat, 0) + 1
        try:
            validate_sql_safety(case["sql"])
            taxonomy["guardrail_false_negative"] = taxonomy.get("guardrail_false_negative", 0) + 1
        except SQLSafetyError:
            guardrail_blocked_count += 1
            cat_passed[cat] = cat_passed.get(cat, 0) + 1
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)

    conn.close()

    exec_acc = round((exec_match_count / len(safe_cases) * 100.0) if safe_cases else 100.0, 2)
    syntax_val = round((syntax_valid_count / len(safe_cases) * 100.0) if safe_cases else 100.0, 2)
    block_rate = round((guardrail_blocked_count / len(unsafe_cases) * 100.0) if unsafe_cases else 100.0, 2)
    pg_rate = round((postgres_transpile_count / len(safe_cases) * 100.0) if safe_cases else 100.0, 2)
    category_accuracy = {
        k: round(cat_passed.get(k, 0) / total * 100.0, 2) for k, total in sorted(cat_totals.items())
    }
    p95_ms = _percentile(latencies_ms, 95.0)

    baseline = load_baseline_metrics(baseline_path)
    deltas = MetricDeltas()
    latency_warning: str | None = None
    if baseline is not None:
        deltas = MetricDeltas(
            execution_accuracy_delta=round(exec_acc - baseline.execution_accuracy, 2),
            sql_syntax_validity_delta=round(syntax_val - baseline.sql_syntax_validity, 2),
            guardrail_block_rate_delta=round(block_rate - baseline.guardrail_block_rate, 2),
            p95_latency_ms_delta=round(p95_ms - baseline.p95_latency_ms, 3),
        )
        if baseline.p95_latency_ms > 0 and p95_ms > baseline.p95_latency_ms * 1.25:
            latency_warning = (
                f"Warning: p95 latency {p95_ms}ms exceeded baseline "
                f"{baseline.p95_latency_ms}ms by >25% (warning-only; no FAIL triggered)."
            )

    report = EvalReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        pytest_total=pytest_total,
        pytest_passed=pytest_passed,
        pytest_failed=pytest_failed,
        eval_cases_total=len(cases),
        execution_accuracy=exec_acc,
        sql_syntax_validity=syntax_val,
        guardrail_block_rate=block_rate,
        postgres_transpile_rate=pg_rate,
        category_accuracy=category_accuracy,
        p95_latency_ms=p95_ms,
        failure_taxonomy=taxonomy,
        baseline_deltas=deltas,
        latency_warning=latency_warning,
        raw_pytest_summary=pytest_summary,
    )

    round_num = 0
    if isinstance(node_input, dict):
        round_num = int(node_input.get("round_number", 0))

    return {
        "eval_report": report.model_dump(),
        "round_number": round_num,
        "engineer_output": node_input,
    }


def evaluate_deterministic_gate(
    payload: dict[str, Any],
    baseline_path: Path = BASELINE_PATH,
) -> tuple[str, CheckerVerdict, int]:
    """Pure-Python gate logic returning (route, CheckerVerdict, next_round).

    - Enforces round >= 2 cap: if failing at round >= 2, returns route='escalate'.
    - Keeps p95_latency_ms as warning-only (never causes FAIL).
    """
    report = EvalReport.model_validate(payload["eval_report"])
    current_round = int(payload.get("round_number", 0))
    baseline = load_baseline_metrics(baseline_path)

    failures: list[str] = []
    if report.pytest_failed > 0:
        failures.append(f"pytest failed ({report.pytest_failed}/{report.pytest_total}): {report.raw_pytest_summary}")

    if baseline is not None:
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


def deterministic_gate_node(node_input: dict[str, Any]) -> Event:
    """ADK Workflow router node wrapping evaluate_deterministic_gate."""
    route, verdict, next_round = evaluate_deterministic_gate(node_input)
    out = {
        "eval_report": node_input["eval_report"],
        "det_verdict": verdict.model_dump(),
        "round_number": next_round,
    }
    return Event(
        author="det_gate",
        output=out,
        actions=EventActions(route=route, stateDelta={"round_number": next_round, "eval_report": node_input["eval_report"]}),
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


def read_repo_file(relative_path: str) -> str:
    """Read a file inside the repository (read-only)."""
    target = (REPO_ROOT / relative_path).resolve()
    if not str(target).startswith(str(REPO_ROOT.resolve())):
        raise ValueError("Access denied outside repository root.")
    if not target.exists():
        return f"ERROR: File not found: {relative_path}"
    return target.read_text(encoding="utf-8")


def write_repo_file(relative_path: str, content: str) -> dict[str, Any]:
    """Write or update a file inside the repository (blocks .env and .git)."""
    norm = relative_path.replace("\\", "/").lstrip("/")
    if norm.startswith(".git") or norm.endswith(".env") or "/.env" in norm:
        raise ValueError(f"Refusing to write protected path: {relative_path}")
    target = (REPO_ROOT / relative_path).resolve()
    if not str(target).startswith(str(REPO_ROOT.resolve())):
        raise ValueError("Access denied outside repository root.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"written": relative_path, "bytes": len(content.encode("utf-8"))}


def get_git_diff() -> str:
    """Return current git diff and untracked status (read-only)."""
    diff = subprocess.run(["git", "diff"], cwd=REPO_ROOT, capture_output=True, text=True)
    status = subprocess.run(["git", "status", "--short"], cwd=REPO_ROOT, capture_output=True, text=True)
    return f"STATUS:\n{status.stdout}\nDIFF:\n{diff.stdout[:12000]}"


def git_pr_tool(
    action: str,
    commit_message: str = "",
    pr_title: str = "",
    pr_body: str = "",
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Guarded Git/PR tool requiring explicit HITL confirmation before any git commit or gh pr create."""
    if action not in {"commit", "create_pr"}:
        return {"status": "error", "message": f"Unsupported action: {action}"}

    if tool_context is not None and not tool_context.tool_confirmation:
        tool_context.request_confirmation(
            hint=f"Approve `{action}` on branch `{get_current_git_branch()}`?",
            payload={"action": action, "commit_message": commit_message, "pr_title": pr_title},
        )
        return {"status": "awaiting_human_approval", "action": action}

    if tool_context is not None and tool_context.tool_confirmation and not tool_context.tool_confirmation.confirmed:
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
