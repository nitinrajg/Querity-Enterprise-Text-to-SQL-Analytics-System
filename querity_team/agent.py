"""ADK 2.x Graph Workflow for the Querity Eval-to-PR AI Team."""

from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv
from google.adk import Agent, Workflow
from google.adk.events import Event, EventActions
from google.adk.workflow import JoinNode, RetryConfig, START

from .deterministic_nodes import (
    deterministic_gate_node,
    get_git_diff,
    git_pr_tool,
    log_run_event,
    preflight_check,
    read_repo_file,
    run_benchmark_and_guardrails,
    write_repo_file,
)

load_dotenv()

MODEL_ID = os.getenv("GEMINI_MODEL", "gemini-4-neon")

LLM_RETRY_CONFIG = RetryConfig(
    max_attempts=4,
    initial_delay=2.0,
    max_delay=30.0,
    backoff_factor=2.0,
    jitter=0.5,
)

analyst_agent = Agent(
    name="analyst",
    model=MODEL_ID,
    retry_config=LLM_RETRY_CONFIG,
    description="Read-only Code & Schema Analyst for Querity.",
    instruction=(
        "JOB: Use read_repo_file to inspect backend/sql_guard.py, db/schema.sql, and querity_team/eval_cases.json "
        "for the user's Change/Issue Brief.\n"
        "OUTPUT: Concise AnalysisSpec listing affected_files, dialects_impacted, schema_constraints, and concrete edge_cases_to_test.\n"
        "NEVER: Edit files or guess paths not observed."
    ),
    tools=[read_repo_file],
    output_key="analysis_spec",
)

join_analysis_and_preflight = JoinNode(
    name="join_analysis_preflight",
    description="Synchronizes parallel Code & Schema Analyst and Branch/Baseline PreFlight.",
)

engineer_agent = Agent(
    name="engineer",
    model=MODEL_ID,
    retry_config=LLM_RETRY_CONFIG,
    description="Eval & Fix Engineer that expands golden eval cases, pytest tests, and minimal patches.",
    instruction=(
        "JOB: Use read_repo_file and write_repo_file to expand querity_team/eval_cases.json, add pytest regression tests, "
        "and apply minimal, clean SQL/Python fixes to backend/sql_guard.py based on AnalysisSpec (or Checker's numbered_fixes).\n"
        "NEVER: Soften pytest assertions, bypass sql_guard, or run git commit/push."
    ),
    tools=[read_repo_file, write_repo_file],
    output_key="engineer_output",
)

checker_agent = Agent(
    name="checker",
    model=MODEL_ID,
    retry_config=LLM_RETRY_CONFIG,
    description="Strict QA Checker reviewing diff, EvalReport, and draft PR summary.",
    instruction=(
        "JOB: Use get_git_diff to inspect actual repo changes, and review the EvalReport (which already passed DetGate) + baseline deltas.\n"
        "OUTPUT: Strict JSON with keys: status ('PASS' or 'FAIL'), score (0-100), target_agent ('engineer', 'analyst', or 'none'), "
        "numbered_fixes (list of strings), round_number (int), reason (string).\n"
        "NEVER: Edit files or allow >2 retry rounds."
    ),
    tools=[get_git_diff, read_repo_file],
    output_key="checker_verdict",
)


def checker_router(node_input: Any) -> Event:
    """Route Checker LLM verdict: PASS -> lead_hitl, FAIL (round < 2) -> retry, FAIL (round >= 2) -> lead_hitl.

    Defaults to FAIL on unparseable output so malformed Checker responses never auto-PASS.
    """
    raw = node_input
    round_num = 0
    verdict_dict: dict[str, Any] = {
        "status": "FAIL",
        "score": 0,
        "target_agent": "engineer",
        "numbered_fixes": ["Checker output was unparseable JSON; re-verify diff and EvalReport."],
        "round_number": 0,
        "reason": "Unparseable Checker verdict (fail-closed default).",
    }

    if isinstance(raw, dict):
        round_num = int(raw.get("round_number", 0))
        if "status" in raw:
            verdict_dict = raw
    elif isinstance(raw, str):
        try:
            cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict) and "status" in parsed:
                verdict_dict = parsed
                round_num = int(parsed.get("round_number", round_num))
        except Exception:
            pass

    status = str(verdict_dict.get("status", "PASS")).upper()
    target = str(verdict_dict.get("target_agent", "engineer")).lower()

    if status == "FAIL" and round_num < 2:
        route = "retry_analyst" if target == "analyst" else "retry_engineer"
        verdict_dict["round_number"] = round_num + 1
    else:
        route = "lead_hitl"

    return Event(author="checker_router", output=verdict_dict, actions=EventActions(route=route))


lead_agent = Agent(
    name="lead",
    model=MODEL_ID,
    retry_config=LLM_RETRY_CONFIG,
    description="Lead orchestrator, memory keeper, and HITL PR gate.",
    instruction=(
        "JOB: Present the final Markdown Eval & PR Summary including Execution Accuracy %, SQL Syntax Validity %, "
        "Guardrail Block Rate %, p95 latency (with any warning), baseline_metrics.json deltas, failure taxonomy, "
        "and Checker verdict (including any round-2 escalation).\n"
        "Pause for explicit 1-click user approval before calling git_pr_tool.\n"
        "Always call log_run_event to record the run."
    ),
    tools=[log_run_event, git_pr_tool],
    output_key="final_summary",
)

root_agent = Workflow(
    name="querity_eval_to_pr_team",
    description="ADK 2.x graph workflow for Querity Eval-to-PR with parallel fan-out, deterministic Runner/DetGate, 2-round quality loop, and HITL gate.",
    retry_config=LLM_RETRY_CONFIG,
    edges=[
        # Parallel fan-out: Analyst + deterministic PreFlight
        (START, analyst_agent),
        (START, preflight_check),
        # Join before Engineer
        (analyst_agent, join_analysis_and_preflight),
        (preflight_check, join_analysis_and_preflight),
        (join_analysis_and_preflight, engineer_agent),
        # Engineer -> Deterministic Runner -> Deterministic Gate
        (engineer_agent, run_benchmark_and_guardrails),
        (run_benchmark_and_guardrails, deterministic_gate_node),
        # DetGate conditional routing (max 2 rounds; p95 latency warning-only)
        (
            deterministic_gate_node,
            {
                "retry_engineer": engineer_agent,
                "escalate": lead_agent,
                "checker": checker_agent,
            },
        ),
        # Checker -> CheckerRouter -> retry or Lead HITL
        (checker_agent, checker_router),
        (
            checker_router,
            {
                "retry_engineer": engineer_agent,
                "retry_analyst": analyst_agent,
                "lead_hitl": lead_agent,
            },
        ),
    ],
)
