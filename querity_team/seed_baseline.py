"""Seed querity_team/baseline_metrics.json from an actual run of Querity's tests and evals."""

from __future__ import annotations

import json
from pathlib import Path

from .deterministic_nodes import BASELINE_PATH, run_benchmark_and_guardrails
from .schemas import BaselineMetrics, EvalReport


def seed_baseline(output_path: Path = BASELINE_PATH) -> BaselineMetrics:
    """Run the deterministic benchmark suite and persist real baseline metrics."""
    # Temporarily ignore any existing baseline while seeding
    result = run_benchmark_and_guardrails(baseline_path=output_path.with_suffix(".nonexistent.json"))
    report = EvalReport.model_validate(result["eval_report"])

    baseline = BaselineMetrics(
        seeded_at=report.timestamp,
        pytest_total=report.pytest_total,
        pytest_passed=report.pytest_passed,
        eval_cases_total=report.eval_cases_total,
        execution_accuracy=report.execution_accuracy,
        sql_syntax_validity=report.sql_syntax_validity,
        guardrail_block_rate=report.guardrail_block_rate,
        p95_latency_ms=report.p95_latency_ms,
    )
    output_path.write_text(json.dumps(baseline.model_dump(), indent=2) + "\n", encoding="utf-8")
    return baseline


if __name__ == "__main__":
    seeded = seed_baseline()
    print(json.dumps(seeded.model_dump(), indent=2))
