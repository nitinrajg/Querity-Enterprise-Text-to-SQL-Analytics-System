"""
Master Evaluation Runner for Querity Text-to-SQL.
Executes the full evaluation suite:
1. SQL Guard Unit Tests (pytest)
2. Text-to-SQL Accuracy Benchmark (45 questions)
3. Adversarial Attack Safety Benchmark (28 attacks)
"""

import sys
import subprocess
import argparse
from pathlib import Path

eval_dir = Path(__file__).resolve().parent
project_root = eval_dir.parent

def run_step(title: str, cmd: list[str]) -> bool:
    print("\n" + "=" * 70)
    print(f"STEP: {title}")
    print(f"COMMAND: {' '.join(cmd)}")
    print("=" * 70, flush=True)
    res = subprocess.run(cmd, cwd=project_root)
    return res.returncode == 0

def main():
    parser = argparse.ArgumentParser(description="Run Full Querity Evaluation Suite")
    parser.add_argument("--skip-unit-tests", action="store_true", help="Skip pytest unit tests")
    parser.add_argument("--skip-accuracy", action="store_true", help="Skip accuracy benchmark")
    parser.add_argument("--skip-safety", action="store_true", help="Skip safety adversarial benchmark")
    parser.add_argument("--limit", type=int, default=45, help="Limit number of accuracy questions (default: 45)")
    parser.add_argument("--sleep", type=float, default=2.5, help="Sleep between LLM calls (default: 2.5s)")
    args = parser.parse_args()

    python_bin = sys.executable

    # 1. SQL Guard Unit Tests
    if not args.skip_unit_tests:
        ok = run_step(
            "1/3: SQL Guard Unit Tests (54 cases)",
            [python_bin, "-m", "pytest", "tests/test_sql_guard.py", "-v"]
        )
        if not ok:
            print("[WARN] Some unit tests failed or xfailed (documented in eval/FINDINGS.md).")

    # 2. Accuracy Benchmark
    if not args.skip_accuracy:
        run_step(
            f"2/3: Text-to-SQL Accuracy Benchmark ({args.limit} questions)",
            [python_bin, "eval/accuracy/run_accuracy.py", "--limit", str(args.limit), "--sleep", str(args.sleep)]
        )

    # 3. Safety Benchmark
    if not args.skip_safety:
        run_step(
            "3/3: Adversarial Safety Benchmark (28 attacks)",
            [python_bin, "eval/safety/run_safety.py", "--sleep", "1.0"]
        )

    print("\n" + "=" * 70)
    print("ALL EVALUATIONS COMPLETE!")
    print(f"Reports saved to: {eval_dir / 'RESULTS.md'} and {eval_dir / 'results'}")
    print("=" * 70)

if __name__ == "__main__":
    main()
