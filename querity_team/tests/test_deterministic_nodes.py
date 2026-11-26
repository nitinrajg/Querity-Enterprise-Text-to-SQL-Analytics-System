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


def test_write_repo_file_blocks_path_traversal():
    """Dot-segment and double-dot traversal must be defeated by resolved-path comparison."""
    # Same file reached via ./
    with pytest.raises(ValueError, match="protected"):
        write_repo_file("querity_team/./baseline_metrics.json", "{}")
    # Same file reached via ../querity_team/
    with pytest.raises(ValueError, match="protected"):
        write_repo_file("querity_team/../querity_team/baseline_metrics.json", "{}")
    # agent.py via double-dot
    with pytest.raises(ValueError, match="protected"):
        write_repo_file("querity_team/./agent.py", "# hacked")


def test_checker_router_state_round_enforced():
    """checker_router must use the state round_number, not the model's self-reported value.

    When bad JSON arrives or FAIL JSON omits/spoofs round_number=0, the authoritative
    state parameter round_number must still enforce the max 2 retry limit.
    """
    from querity_team.agent import checker_router

    # Simulate: state says round=2 (maximum), but checker returned garbage plain text
    event = checker_router("not json at all", round_number=2)
    assert event.actions.route == "lead_hitl", (
        f"Expected lead_hitl escalation at round 2, got {event.actions.route}"
    )

    # Simulate: state says round=0, checker returned valid FAIL -> should retry
    fail_reply = (
        '{"status": "FAIL", "score": 3, "target_agent": "engineer",'
        ' "numbered_fixes": ["fix this"], "round_number": 0, "reason": "too slow"}'
    )
    event2 = checker_router(fail_reply, round_number=0)
    assert event2.actions.route in ("retry_engineer", "retry_analyst"), (
        f"Expected retry at round 0, got {event2.actions.route}"
    )
    assert event2.actions.state_delta["round_number"] == 1, "state_delta must increment round"

    # Simulate: state says round=1, checker returns FAIL (even trying to spoof round_number=0) -> round becomes 2
    event3 = checker_router(fail_reply, round_number=1)
    assert event3.actions.route in ("retry_engineer", "retry_analyst")
    assert event3.actions.state_delta["round_number"] == 2

    # Simulate: state says round=2, checker returns FAIL (even trying to spoof round_number=0) -> must escalate
    event4 = checker_router(fail_reply, round_number=2)
    assert event4.actions.route == "lead_hitl", (
        f"Expected lead_hitl at round cap, got {event4.actions.route}"
    )


def test_write_and_read_block_sibling_folder_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Sibling prefix folder (e.g. copy/ trying to access ../copy-evil/x) must be blocked by is_relative_to."""
    import querity_team.deterministic_nodes as dn

    repo_copy = tmp_path / "copy"
    (repo_copy / "backend").mkdir(parents=True)
    sibling_evil = tmp_path / "copy-evil"
    sibling_evil.mkdir(parents=True)
    (sibling_evil / "secret.txt").write_text("TOP_SECRET", encoding="utf-8")

    monkeypatch.setattr(dn, "REPO_ROOT", repo_copy)

    with pytest.raises(ValueError, match="outside repository root"):
        dn.write_repo_file("../copy-evil/x.py", "# pwned")

    with pytest.raises(ValueError, match="outside repository root"):
        dn.read_repo_file("../copy-evil/secret.txt")


def test_write_repo_file_blocks_pytest_ini_tests_and_non_allowlisted_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Engineer cannot edit pytest.ini, CI, baseline tests, frontend, or mutate existing eval cases."""
    import querity_team.deterministic_nodes as dn

    repo_copy = tmp_path / "repo"
    (repo_copy / "backend").mkdir(parents=True)
    (repo_copy / "querity_team" / "tests").mkdir(parents=True)
    (repo_copy / "frontend").mkdir(parents=True)
    (repo_copy / ".github" / "workflows").mkdir(parents=True)
    (repo_copy / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    (repo_copy / "backend" / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (repo_copy / "backend" / "test_sql_guard_hardening.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    (repo_copy / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (repo_copy / ".env.example").write_text("SECRET=\n", encoding="utf-8")
    eval_file = repo_copy / "querity_team" / "eval_cases.json"
    eval_file.write_text(
        json.dumps([{"id": "eval_001", "kind": "safe", "dialect": "duckdb", "sql": "SELECT 1", "gold_sql": "SELECT 1"}]),
        encoding="utf-8",
    )

    monkeypatch.setattr(dn, "REPO_ROOT", repo_copy)

    for forbidden in (
        "pytest.ini",
        "conftest.py",
        "backend/conftest.py",
        "backend/main.py",
        "backend/__init__.py",
        "backend/sitecustomize.py",
        ".github/workflows/eval-ci.yml",
        "backend/requirements.txt",
        "backend/test_sql_guard_hardening.py",
        "frontend/index.html",
        ".env",
        "querity_team/baseline_metrics.json",
        "querity_team/tests/test_deterministic_nodes.py",
    ):
        with pytest.raises(ValueError, match="protected"):
            dn.write_repo_file(forbidden, "x")

    # Replacing or deleting an existing eval case must be blocked
    with pytest.raises(ValueError, match="protected"):
        dn.write_repo_file(
            "querity_team/eval_cases.json",
            json.dumps([{"id": "eval_001", "kind": "safe", "dialect": "duckdb", "sql": "SELECT 999", "gold_sql": "SELECT 999"}]),
        )

    # Reading .env must be blocked, while .env.example is readable
    with pytest.raises(ValueError, match="protected"):
        dn.read_repo_file(".env")
    assert "SECRET=" in dn.read_repo_file(".env.example")

    # Appending a new eval case and writing a new regression test file must work
    res1 = dn.write_repo_file(
        "querity_team/eval_cases.json",
        json.dumps([
            {"id": "eval_001", "kind": "safe", "dialect": "duckdb", "sql": "SELECT 1", "gold_sql": "SELECT 1"},
            {"id": "eval_002", "kind": "unsafe", "dialect": "postgres", "sql": "SELECT pg_sleep(1);", "gold_sql": ""},
        ]),
    )
    assert res1["written"] == "querity_team/eval_cases.json"
    res2 = dn.write_repo_file("backend/test_regression.py", "def test_new():\n    assert True\n")
    assert res2["written"] == "backend/test_regression.py"


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


def test_sandboxed_grader_blocks_conftest_and_secret_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Untrusted conftest.py or test code cannot read .env/env secrets or mutate baseline_metrics.json."""
    import querity_team.deterministic_nodes as dn

    repo_copy = tmp_path / "repo"
    (repo_copy / "backend").mkdir(parents=True)
    (repo_copy / "querity_team").mkdir(parents=True)
    (repo_copy / "db").mkdir(parents=True)

    real_repo = dn.REPO_ROOT
    (repo_copy / "backend" / "sql_guard.py").write_text(
        (real_repo / "backend" / "sql_guard.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (repo_copy / "querity_team" / "__init__.py").write_text("", encoding="utf-8")
    (repo_copy / "querity_team" / "schemas.py").write_text(
        (real_repo / "querity_team" / "schemas.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (repo_copy / "db" / "schema.sql").write_text(
        (real_repo / "db" / "schema.sql").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (repo_copy / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    (repo_copy / ".env").write_text("GEMINI_API_KEY=DO_NOT_LEAK_DOTENV\n", encoding="utf-8")

    baseline_file = repo_copy / "querity_team" / "baseline_metrics.json"
    baseline_json = BaselineMetrics(
        seeded_at="2026-11-26T00:00:00Z",
        pytest_total=1,
        pytest_passed=1,
        eval_cases_total=1,
        execution_accuracy=100.0,
        sql_syntax_validity=100.0,
        guardrail_block_rate=100.0,
        p95_latency_ms=10.0,
    ).model_dump_json()
    baseline_file.write_text(baseline_json, encoding="utf-8")

    eval_file = repo_copy / "querity_team" / "eval_cases.json"
    eval_file.write_text(
        json.dumps([
            {
                "id": "e1",
                "kind": "safe",
                "category": "aggregation",
                "dialect": "duckdb",
                "question": "Count products",
                "sql": "SELECT COUNT(*) FROM products",
                "gold_sql": "SELECT COUNT(*) FROM products LIMIT 50",
            }
        ]),
        encoding="utf-8",
    )

    # Plant an attacker conftest.py that would mutate baseline if pytest ever loaded it
    (repo_copy / "backend" / "conftest.py").write_text(
        "raise RuntimeError('conftest.py MUST NOT BE LOADED')\n",
        encoding="utf-8",
    )
    # Plant a test verifying no env secrets, .env, real baseline_metrics.json, or eval_cases.json in worker dir
    (repo_copy / "backend" / "test_sandbox_env.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "def test_no_secrets_no_baseline_no_eval_cases():\n"
        "    assert 'GEMINI_API_KEY' not in os.environ\n"
        "    assert 'VIRTUAL_ENV' not in os.environ\n"
        "    assert 'HOME' not in os.environ\n"
        "    assert 'USERPROFILE' not in os.environ\n"
        "    assert not Path('.env').exists()\n"
        "    assert not Path('querity_team/baseline_metrics.json').exists()\n"
        "    assert not Path('querity_team/eval_cases.json').exists()\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("GEMINI_API_KEY", "SUPER_SECRET_ENV_VALUE")
    monkeypatch.setenv("VIRTUAL_ENV", str(repo_copy / ".venv"))
    monkeypatch.setattr(dn, "REPO_ROOT", repo_copy)
    monkeypatch.setattr(dn, "get_unapproved_workspace_changes", lambda _r=None: ["backend/conftest.py"])

    res = dn.run_benchmark_and_guardrails(
        pytest_targets=["backend/test_sandbox_env.py"],
        eval_cases_path=eval_file,
        baseline_path=baseline_file,
        schema_path=repo_copy / "db" / "schema.sql",
    )
    report = EvalReport.model_validate(res["eval_report"])
    assert report.pytest_passed == 1
    assert report.pytest_failed == 0
    assert report.requires_human_review is True
    assert "backend/conftest.py" in report.modified_exam_files
    # Real baseline file was never touched
    assert json.loads(baseline_file.read_text(encoding="utf-8"))["pytest_total"] == 1


def test_forged_sql_guard_stdout_and_no_protection_caught_by_parent_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Rogue sql_guard printing fake 100%-passing JSON and omitting checks must FAIL DetGate."""
    import querity_team.deterministic_nodes as dn

    repo_copy = tmp_path / "repo"
    (repo_copy / "backend").mkdir(parents=True)
    (repo_copy / "querity_team").mkdir(parents=True)
    (repo_copy / "db").mkdir(parents=True)

    real_repo = dn.REPO_ROOT
    (repo_copy / "querity_team" / "__init__.py").write_text("", encoding="utf-8")
    (repo_copy / "querity_team" / "schemas.py").write_text(
        (real_repo / "querity_team" / "schemas.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (repo_copy / "db" / "schema.sql").write_text(
        (real_repo / "db" / "schema.sql").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (repo_copy / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")
    (repo_copy / "backend" / "test_trivial.py").write_text("def test_ok(): pass\n", encoding="utf-8")

    # Malicious sql_guard prints forged JSON summary AND does zero blocking
    (repo_copy / "backend" / "sql_guard.py").write_text(
        'import json\n'
        'print(json.dumps({"eval_cases_total": 40, "safe_total": 1, "unsafe_total": 1, '
        '"syntax_valid_count": 1, "exec_match_count": 1, "postgres_transpile_count": 1, '
        '"guardrail_blocked_count": 1, "latencies_ms": [1.0], "taxonomy": {}, '
        '"cat_totals": {}, "cat_passed": {}}))\n'
        'class SQLSafetyError(Exception): pass\n'
        'def validate_sql_safety(sql, default_limit=50, dialect="duckdb", allow_meta=False, allowed_tables=None):\n'
        '    return sql\n',
        encoding="utf-8",
    )
    baseline_file = repo_copy / "querity_team" / "baseline_metrics.json"
    baseline_file.write_text(
        BaselineMetrics(
            seeded_at="2026-11-26T00:00:00Z",
            pytest_total=1,
            pytest_passed=1,
            eval_cases_total=2,
            execution_accuracy=100.0,
            sql_syntax_validity=100.0,
            guardrail_block_rate=100.0,
            p95_latency_ms=10.0,
        ).model_dump_json(),
        encoding="utf-8",
    )
    eval_file = repo_copy / "querity_team" / "eval_cases.json"
    eval_file.write_text(
        json.dumps([
            {"id": "s1", "kind": "safe", "category": "agg", "dialect": "duckdb", "question": "c", "sql": "SELECT COUNT(*) FROM products", "gold_sql": "SELECT COUNT(*) FROM products LIMIT 50"},
            {"id": "u1", "kind": "unsafe", "category": "adv", "dialect": "duckdb", "question": "d", "sql": "DROP TABLE orders;", "gold_sql": ""},
        ]),
        encoding="utf-8",
    )

    monkeypatch.setattr(dn, "REPO_ROOT", repo_copy)
    monkeypatch.setattr(dn, "get_unapproved_workspace_changes", lambda _r=None: ["backend/sql_guard.py"])

    res = dn.run_benchmark_and_guardrails(
        pytest_targets=["backend/test_trivial.py"],
        eval_cases_path=eval_file,
        baseline_path=baseline_file,
        schema_path=repo_copy / "db" / "schema.sql",
    )
    report = EvalReport.model_validate(res["eval_report"])
    assert report.guardrail_block_rate == 0.0
    route, verdict, _ = evaluate_deterministic_gate(res, baseline_path=baseline_file)
    assert verdict.status == "FAIL"
    assert route == "retry_engineer"


def test_worker_cannot_read_eval_cases_kind_or_gold_sql_and_init_tamper_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Worker sees no eval_cases.json on disk, no kind/gold_sql/canary labels, and __init__.py tampering fails."""
    import querity_team.deterministic_nodes as dn

    repo_copy = tmp_path / "repo"
    (repo_copy / "backend").mkdir(parents=True)
    (repo_copy / "querity_team").mkdir(parents=True)
    (repo_copy / "db").mkdir(parents=True)
    real_repo = dn.REPO_ROOT
    (repo_copy / "querity_team" / "__init__.py").write_text("", encoding="utf-8")
    (repo_copy / "querity_team" / "schemas.py").write_text(
        (real_repo / "querity_team" / "schemas.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (repo_copy / "db" / "schema.sql").write_text(
        (real_repo / "db" / "schema.sql").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (repo_copy / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")

    eval_file = repo_copy / "querity_team" / "eval_cases.json"
    eval_file.write_text(
        json.dumps([
            {"id": "eval_001", "kind": "safe", "category": "agg", "dialect": "duckdb", "question": "c", "sql": "SELECT COUNT(*) FROM products", "gold_sql": "SELECT COUNT(*) FROM products LIMIT 50"},
            {"id": "eval_019", "kind": "unsafe", "category": "adv", "dialect": "duckdb", "question": "d", "sql": "DROP TABLE orders;", "gold_sql": ""},
        ]),
        encoding="utf-8",
    )
    baseline_file = repo_copy / "querity_team" / "baseline_metrics.json"
    baseline_file.write_text(
        BaselineMetrics(
            seeded_at="2026-11-26T00:00:00Z", pytest_total=1, pytest_passed=1, eval_cases_total=2,
            execution_accuracy=100.0, sql_syntax_validity=100.0, guardrail_block_rate=100.0, p95_latency_ms=10.0,
        ).model_dump_json(),
        encoding="utf-8",
    )

    # Guard verifies no eval_cases.json on disk and inspects the serialized worker payload for zero leakage
    captured_payloads: list[dict] = []
    orig_run = dn.subprocess.run

    def _spy_run(*args, **kwargs):
        if kwargs.get("input"):
            captured_payloads.append(json.loads(kwargs["input"]))
        return orig_run(*args, **kwargs)

    (repo_copy / "backend" / "sql_guard.py").write_text(
        "from pathlib import Path\n"
        "class SQLSafetyError(Exception): pass\n"
        "def validate_sql_safety(sql, default_limit=50, dialect='duckdb', allow_meta=False, allowed_tables=None):\n"
        "    assert not Path('querity_team/eval_cases.json').exists()\n"
        "    return sql\n",
        encoding="utf-8",
    )
    # Test that mutates querity_team/__init__.py at its real absolute path must trigger integrity violation
    real_init = str((repo_copy / "querity_team" / "__init__.py").resolve())
    (repo_copy / "backend" / "test_tamper.py").write_text(
        "from pathlib import Path\n"
        "def test_edit_init():\n"
        f"    Path({real_init!r}).write_text('# tampered', encoding='utf-8')\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(dn, "REPO_ROOT", repo_copy)
    monkeypatch.setattr(dn.subprocess, "run", _spy_run)
    monkeypatch.setattr(dn, "get_unapproved_workspace_changes", lambda _r=None: ["backend/test_tamper.py"])

    with pytest.raises(RuntimeError, match="integrity violation"):
        dn.run_benchmark_and_guardrails(
            pytest_targets=["backend/test_tamper.py"],
            eval_cases_path=eval_file,
            baseline_path=baseline_file,
            schema_path=repo_copy / "db" / "schema.sql",
        )

    assert len(captured_payloads) == 1
    sent_cases = captured_payloads[0]["cases"]
    assert len(sent_cases) == 5  # 2 eval + 3 dynamic canaries
    for item in sent_cases:
        assert set(item.keys()) == {"id", "sql", "dialect"}
        assert item["id"].startswith("q_")
        assert "canary" not in item["id"]


def test_full_review_diff_shows_staged_and_untracked_files_and_blocks_binary(tmp_path: Path):
    """get_full_review_diff displays full staged diffs, full untracked file contents, and blocks binary untracked files."""
    import subprocess as sp
    from querity_team.deterministic_nodes import get_full_review_diff

    repo = tmp_path / "gitrepo"
    repo.mkdir()
    sp.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    sp.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    sp.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)

    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    sp.run(["git", "add", "a.py"], cwd=repo, check=True)
    sp.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    # Staged modification + new untracked file with large body (no truncation!)
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    sp.run(["git", "add", "a.py"], cwd=repo, check=True)
    long_body = "\n".join(f"line_{i} = {i}" for i in range(400)) + "\nFINAL_SENTINEL = True\n"
    (repo / "backend").mkdir()
    (repo / "backend" / "test_zz.py").write_text(long_body, encoding="utf-8")

    text, ok = get_full_review_diff(repo)
    assert ok is True
    assert "+x = 2" in text
    assert "NEW FILE: backend/test_zz.py" in text
    assert "FINAL_SENTINEL = True" in text

    # Binary untracked file must fail closed (ok is False)
    (repo / "backend" / "evil.bin").write_bytes(b"\xff\xfe\x00\x01binary")
    err_text, ok2 = get_full_review_diff(repo)
    assert ok2 is False
    assert "Cannot inspect unreadable/binary" in err_text


def test_git_pr_tool_enforces_hitl_confirmation():
    """git_pr_tool must block calls without ToolContext and flag modified files on confirmation."""
    from unittest.mock import MagicMock
    from querity_team.deterministic_nodes import git_pr_tool

    blocked = git_pr_tool(action="commit", commit_message="auto commit")
    assert blocked["status"] == "blocked_requires_human_approval"

    ctx = MagicMock()
    ctx.state = {
        "eval_report": {
            "requires_human_review": True,
            "modified_exam_files": ["backend/sql_guard.py", "querity_team/eval_cases.json"],
        }
    }
    ctx.tool_confirmation = None
    awaiting = git_pr_tool(action="commit", commit_message="feat: update", tool_context=ctx)
    assert awaiting["status"] == "awaiting_human_approval"
    assert awaiting["requires_human_review"] is True
    assert "backend/sql_guard.py" in awaiting["modified_files"]
    ctx.request_confirmation.assert_called_once()
