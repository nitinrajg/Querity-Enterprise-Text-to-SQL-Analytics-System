"""Unit tests for deterministic PreFlight, Runner, and DetGate nodes."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from querity_team.deterministic_nodes import (
    evaluate_deterministic_gate,
    preflight_check,
    run_benchmark_and_guardrails,
    write_repo_file,
)
from querity_team.schemas import BaselineMetrics, EvalReport, MetricDeltas


def test_preflight_blocks_main_branch(tmp_path: Path):
    with patch("querity_team.deterministic_nodes.get_current_git_branch", return_value="main"):
        with pytest.raises(RuntimeError, match="feature branch"):
            preflight_check("Add CTE eval")


def test_preflight_allows_feature_branch(tmp_path: Path):
    mem = tmp_path / "team_memory.md"
    mem.write_text("# Memory", encoding="utf-8")
    with patch("querity_team.deterministic_nodes.get_current_git_branch", return_value="feat/adk-eval-team"):
        res = preflight_check("Add CTE eval", baseline_path=tmp_path / "missing.json", memory_path=mem)
    assert res["branch"] == "feat/adk-eval-team"
    assert res["branch_ok"] is True


def test_det_gate_enforces_round_2_escalation(tmp_path: Path):
    baseline_file = tmp_path / "baseline.json"
    baseline_file.write_text(
        BaselineMetrics(
            seeded_at="2026-11-26T00:00:00Z",
            pytest_total=13,
            pytest_passed=13,
            execution_accuracy=100.0,
            sql_syntax_validity=100.0,
            guardrail_block_rate=100.0,
            p95_latency_ms=10.0,
        ).model_dump_json(),
        encoding="utf-8",
    )
    failing_report = EvalReport(
        timestamp="2026-11-26T00:01:00Z",
        pytest_total=13,
        pytest_passed=12,
        pytest_failed=1,
        eval_cases_total=6,
        execution_accuracy=100.0,
        sql_syntax_validity=100.0,
        guardrail_block_rate=100.0,
        p95_latency_ms=10.0,
        raw_pytest_summary="1 failed, 12 passed",
    ).model_dump()

    # Round 0 -> retry_engineer (round 1)
    route0, verdict0, next0 = evaluate_deterministic_gate(
        {"eval_report": failing_report, "round_number": 0}, baseline_path=baseline_file
    )
    assert route0 == "retry_engineer"
    assert next0 == 1
    assert verdict0.status == "FAIL"

    # Round 2 -> escalate to Lead -> HITL (never loops past 2)
    route2, verdict2, next2 = evaluate_deterministic_gate(
        {"eval_report": failing_report, "round_number": 2}, baseline_path=baseline_file
    )
    assert route2 == "escalate"
    assert next2 == 2
    assert verdict2.status == "FAIL"


def test_det_gate_p95_latency_is_warning_only(tmp_path: Path):
    baseline_file = tmp_path / "baseline.json"
    baseline_file.write_text(
        BaselineMetrics(
            seeded_at="2026-11-26T00:00:00Z",
            pytest_total=13,
            pytest_passed=13,
            execution_accuracy=100.0,
            sql_syntax_validity=100.0,
            guardrail_block_rate=100.0,
            p95_latency_ms=5.0,
        ).model_dump_json(),
        encoding="utf-8",
    )
    slow_passing_report = EvalReport(
        timestamp="2026-11-26T00:01:00Z",
        pytest_total=13,
        pytest_passed=13,
        pytest_failed=0,
        eval_cases_total=6,
        execution_accuracy=100.0,
        sql_syntax_validity=100.0,
        guardrail_block_rate=100.0,
        p95_latency_ms=950.0,
        baseline_deltas=MetricDeltas(p95_latency_ms_delta=945.0),
        latency_warning="Warning: p95 latency exceeded baseline (warning-only).",
    ).model_dump()

    route, verdict, _ = evaluate_deterministic_gate(
        {"eval_report": slow_passing_report, "round_number": 0}, baseline_path=baseline_file
    )
    assert route == "checker"
    assert verdict.status == "PASS"


def test_runner_emits_valid_eval_report():
    out = run_benchmark_and_guardrails(pytest_targets=["backend/test_multitable_joins.py"])
    report = EvalReport.model_validate(out["eval_report"])
    assert report.pytest_passed == 2
    assert report.pytest_failed == 0
    assert report.execution_accuracy == 100.0
    assert report.sql_syntax_validity == 100.0
    assert report.guardrail_block_rate == 100.0


def test_write_repo_file_blocks_protected_exam_paths():
    with pytest.raises(ValueError, match="protected"):
        write_repo_file("querity_team/baseline_metrics.json", "{}")
    with pytest.raises(ValueError, match="protected"):
        write_repo_file("querity_team/deterministic_nodes.py", "# hacked")


def test_det_gate_catches_shrunk_eval_cases(tmp_path: Path):
    baseline_file = tmp_path / "baseline.json"
    baseline_file.write_text(
        BaselineMetrics(
            seeded_at="2026-11-26T00:00:00Z",
            pytest_total=13,
            pytest_passed=13,
            eval_cases_total=31,
            execution_accuracy=100.0,
            sql_syntax_validity=100.0,
            guardrail_block_rate=100.0,
            p95_latency_ms=10.0,
        ).model_dump_json(),
        encoding="utf-8",
    )
    shrunk_report = EvalReport(
        timestamp="2026-11-26T00:01:00Z",
        pytest_total=13,
        pytest_passed=13,
        pytest_failed=0,
        eval_cases_total=10,
        execution_accuracy=100.0,
        sql_syntax_validity=100.0,
        guardrail_block_rate=100.0,
        p95_latency_ms=10.0,
    ).model_dump()

    route, verdict, _ = evaluate_deterministic_gate(
        {"eval_report": shrunk_report, "round_number": 0}, baseline_path=baseline_file
    )
    assert route == "retry_engineer"
    assert verdict.status == "FAIL"
    assert any("shrank" in fix for fix in verdict.numbered_fixes)
