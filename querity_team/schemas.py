"""Strict Pydantic schemas for the Querity Eval-to-PR ADK 2.x team."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class BaselineMetrics(BaseModel):
    """Persisted baseline metrics seeded from an actual repo run."""

    seeded_at: str
    pytest_total: int
    pytest_passed: int
    eval_cases_total: int = 0
    execution_accuracy: float = Field(ge=0.0, le=100.0)
    sql_syntax_validity: float = Field(ge=0.0, le=100.0)
    guardrail_block_rate: float = Field(ge=0.0, le=100.0)
    postgres_transpile_rate: float = Field(default=100.0, ge=0.0, le=100.0)
    category_accuracy: dict[str, float] = Field(default_factory=dict)
    p95_latency_ms: float = Field(ge=0.0)


class MetricDeltas(BaseModel):
    """Exact metric deltas relative to baseline_metrics.json."""

    execution_accuracy_delta: float = 0.0
    sql_syntax_validity_delta: float = 0.0
    guardrail_block_rate_delta: float = 0.0
    p95_latency_ms_delta: float = 0.0


class EvalReport(BaseModel):
    """Deterministic output emitted by the Benchmark & Guardrail Runner node."""

    timestamp: str
    pytest_total: int
    pytest_passed: int
    pytest_failed: int
    eval_cases_total: int
    execution_accuracy: float = Field(ge=0.0, le=100.0)
    sql_syntax_validity: float = Field(ge=0.0, le=100.0)
    guardrail_block_rate: float = Field(ge=0.0, le=100.0)
    postgres_transpile_rate: float = Field(default=100.0, ge=0.0, le=100.0)
    category_accuracy: dict[str, float] = Field(default_factory=dict)
    p95_latency_ms: float = Field(ge=0.0)
    failure_taxonomy: dict[str, int] = Field(default_factory=dict)
    baseline_deltas: MetricDeltas = Field(default_factory=MetricDeltas)
    latency_warning: str | None = None
    raw_pytest_summary: str = ""


class CheckerVerdict(BaseModel):
    """Structured verdict from DetGate or Checker LLM."""

    status: Literal["PASS", "FAIL"]
    score: int = Field(ge=0, le=100)
    target_agent: Literal["analyst", "engineer", "none"]
    numbered_fixes: list[str] = Field(default_factory=list)
    round_number: int = Field(ge=0, le=2)
    reason: str
