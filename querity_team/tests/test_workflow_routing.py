"""End-to-end ADK Workflow tests with a fake BaseLlm (no API key required).

Tests all critical routing and retry-cap paths on the real ``root_agent`` graph:
1. Checker returns unreadable text -> 3 engineer attempts (initial + 2 retries), then lead
2. Checker always returns FAIL -> 3 engineer attempts (initial + 2 retries), then lead
3. DetGate always fails -> 3 engineer attempts (initial + 2 retries), then escalates to lead
4. Checker returns PASS -> 1 engineer attempt, 1 checker call, straight to lead
5. Checker returns FAIL targeting analyst -> routes through JoinNode cleanly, then lead
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import PrivateAttr

import querity_team.agent as agent_mod
import querity_team.deterministic_nodes as dn
from querity_team.schemas import BaselineMetrics


class _RoleScriptedFakeLlm(BaseLlm):
    """Minimal BaseLlm returning scripted responses without calling Gemini APIs."""

    model: str = "fake-llm"
    _replies: list[str] = PrivateAttr(default_factory=list)
    _calls: list[str] = PrivateAttr(default_factory=list)
    _role: str = PrivateAttr(default="")

    def __init__(self, replies: list[str], call_log: list[str], role: str):
        super().__init__(model=f"fake-{role}")
        self._replies = list(replies)
        self._calls = call_log
        self._role = role

    @classmethod
    def supported_models(cls) -> list[str]:
        return [r"fake-.*"]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self._calls.append(self._role)
        idx = min(sum(1 for c in self._calls if c == self._role) - 1, len(self._replies) - 1)
        reply_text = self._replies[idx]
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=reply_text)]),
            finish_reason=types.FinishReason.STOP,
        )


def _setup_fake_workflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    checker_replies: list[str],
    gate_passes: bool = True,
) -> list[str]:
    """Patch agent LLMs, git branch, baseline, and pytest subprocess for fast deterministic graph runs."""
    calls: list[str] = []

    role_replies = {
        "analyst": ['{"affected_files": ["backend/sql_guard.py"]}'],
        "engineer": ["Applied patch and expanded eval cases."],
        "checker": checker_replies,
        "lead": ["Final PR summary ready for HITL approval."],
    }
    for role, replies in role_replies.items():
        node = agent_mod.root_agent._get_static_node_by_name(role)
        monkeypatch.setattr(node, "model", _RoleScriptedFakeLlm(replies, calls, role))

    baseline_file = tmp_path / "baseline.json"
    baseline_file.write_text(
        BaselineMetrics(
            seeded_at="2026-11-26T00:00:00Z",
            pytest_total=5,
            pytest_passed=5,
            eval_cases_total=1,
            execution_accuracy=100.0,
            sql_syntax_validity=100.0,
            guardrail_block_rate=100.0,
            p95_latency_ms=10.0,
        ).model_dump_json(),
        encoding="utf-8",
    )
    eval_file = tmp_path / "eval_cases.json"
    eval_file.write_text(
        '[{"id":"e1","kind":"safe","category":"agg","dialect":"duckdb","question":"c","sql":"SELECT COUNT(*) FROM products","gold_sql":"SELECT COUNT(*) FROM products LIMIT 50"}]',
        encoding="utf-8",
    )
    mem_file = tmp_path / "team_memory.md"
    mem_file.write_text("# Memory", encoding="utf-8")

    monkeypatch.setattr(dn, "get_current_git_branch", lambda _root=None: "feat/test-workflow")
    monkeypatch.setattr(dn, "get_unapproved_workspace_changes", lambda _root=None: [])
    monkeypatch.setattr(dn, "BASELINE_PATH", baseline_file)
    monkeypatch.setattr(dn, "EVAL_CASES_PATH", eval_file)
    monkeypatch.setattr(dn, "TEAM_MEMORY_PATH", mem_file)
    monkeypatch.setattr(
        dn,
        "_run_pytest_targets",
        lambda _targets, **_kw: (5, 5, 0, "5 passed") if gate_passes else (5, 4, 1, "1 failed"),
    )
    return calls


async def _run_real_workflow(brief: str = "Test workflow routing") -> list[Any]:
    runner = InMemoryRunner(agent=agent_mod.root_agent, app_name="querity_test_wf")
    session = await runner.session_service.create_session(
        app_name="querity_test_wf",
        user_id="test_user",
    )
    events: list[Any] = []
    async for event in runner.run_async(
        user_id="test_user",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=brief)]),
    ):
        events.append(event)
    return events


def test_workflow_checker_unreadable_text_stops_at_round_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Unreadable Checker replies must fail-closed, retry twice (3 engineer runs), then escalate to lead."""
    calls = _setup_fake_workflow(
        monkeypatch,
        tmp_path,
        checker_replies=["not valid json!", "still not json!", "garbage again!"],
        gate_passes=True,
    )

    events = asyncio.run(_run_real_workflow())
    assert events, "Workflow produced no events"
    assert calls.count("engineer") == 3
    assert calls.count("checker") == 3
    assert calls.count("lead") == 1
    assert calls[-1] == "lead"


def test_workflow_checker_always_fail_stops_at_round_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Checker FAIL (even attempting round_number=0 spoof) stops after 3 engineer attempts and reaches lead."""
    spoofed_fail = (
        '{"status": "FAIL", "score": 20, "target_agent": "engineer", '
        '"numbered_fixes": ["Fix missing edge case"], "round_number": 0, "reason": "incomplete"}'
    )
    calls = _setup_fake_workflow(
        monkeypatch,
        tmp_path,
        checker_replies=[spoofed_fail],
        gate_passes=True,
    )

    asyncio.run(_run_real_workflow())
    assert calls.count("engineer") == 3
    assert calls.count("checker") == 3
    assert calls.count("lead") == 1
    assert calls[-1] == "lead"


def test_workflow_det_gate_always_fails_stops_at_round_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """When DetGate always fails pytest, workflow retries Engineer twice (3 runs), skips Checker, and escalates to lead."""
    calls = _setup_fake_workflow(
        monkeypatch,
        tmp_path,
        checker_replies=[
            '{"status": "PASS", "score": 100, "target_agent": "none", "numbered_fixes": [], "round_number": 0, "reason": "ok"}'
        ],
        gate_passes=False,
    )

    asyncio.run(_run_real_workflow())
    assert calls.count("engineer") == 3
    assert calls.count("checker") == 0
    assert calls.count("lead") == 1
    assert calls[-1] == "lead"


def test_workflow_checker_pass_goes_straight_to_lead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """When DetGate and Checker both PASS on first try, workflow invokes 1 engineer, 1 checker, 1 lead."""
    calls = _setup_fake_workflow(
        monkeypatch,
        tmp_path,
        checker_replies=[
            '{"status": "PASS", "score": 98, "target_agent": "none", "numbered_fixes": [], "round_number": 0, "reason": "clean diff"}'
        ],
        gate_passes=True,
    )

    asyncio.run(_run_real_workflow())
    assert calls.count("analyst") == 1
    assert calls.count("engineer") == 1
    assert calls.count("checker") == 1
    assert calls.count("lead") == 1
    assert calls[-1] == "lead"


def test_workflow_retry_analyst_routes_through_join_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """FAIL targeting analyst routes back through JoinNode to engineer, then PASS on second check."""
    calls = _setup_fake_workflow(
        monkeypatch,
        tmp_path,
        checker_replies=[
            '{"status": "FAIL", "score": 50, "target_agent": "analyst", "numbered_fixes": ["Check schema"], "round_number": 0, "reason": "missing schema check"}',
            '{"status": "PASS", "score": 95, "target_agent": "none", "numbered_fixes": [], "round_number": 1, "reason": "verified"}',
        ],
        gate_passes=True,
    )

    asyncio.run(_run_real_workflow())
    assert calls.count("analyst") == 2
    assert calls.count("engineer") == 2
    assert calls.count("checker") == 2
    assert calls.count("lead") == 1
    assert calls[-1] == "lead"


def test_workflow_pauses_before_running_unapproved_code_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """When workspace files were edited, guarded_runner_node must interrupt for human approval BEFORE running code."""
    calls = _setup_fake_workflow(
        monkeypatch,
        tmp_path,
        checker_replies=['{"status": "PASS", "score": 100, "target_agent": "none", "numbered_fixes": [], "round_number": 0, "reason": "ok"}'],
        gate_passes=True,
    )
    # Simulate an unreviewed code edit by engineer_agent
    monkeypatch.setattr(dn, "get_unapproved_workspace_changes", lambda _root=None: ["backend/sql_guard.py"])

    runner_executed = []
    monkeypatch.setattr(
        dn,
        "_run_pytest_targets",
        lambda _targets, **_kw: (runner_executed.append(True), (5, 5, 0, "5 passed"))[1],
    )

    events = asyncio.run(_run_real_workflow())
    assert calls.count("analyst") == 1
    assert calls.count("engineer") == 1
    # Execution MUST pause before pytest/eval worker or checker or lead runs
    assert runner_executed == []
    assert calls.count("checker") == 0
    assert calls.count("lead") == 0
    assert any(
        ev.content
        and ev.content.parts
        and any(getattr(p, "function_call", None) and p.function_call.name == "adk_request_input" for p in ev.content.parts)
        for ev in events
    )
