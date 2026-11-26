"""One-command CLI runner for the Querity Eval-to-PR ADK 2.x Workflow."""

from __future__ import annotations

import asyncio
import re
import sys
from dotenv import load_dotenv
from google.adk.runners import InMemoryRunner
from google.genai import types

from .agent import root_agent

load_dotenv()


def _is_daily_quota_error(exc: BaseException) -> bool:
    """Detect daily quota exhaustion (PerDay or retryDelay > 60s) so we fail fast."""
    msg = str(exc)
    if "PerDay" in msg or "GenerateRequestsPerDay" in msg:
        return True
    match = re.search(r"retryDelay['\"]?:\s*['\"]?(\d+)s", msg)
    return bool(match and int(match.group(1)) > 60)


async def run_brief(brief: str) -> None:
    runner = InMemoryRunner(agent=root_agent, app_name="querity_eval_to_pr")
    session = await runner.session_service.create_session(
        app_name="querity_eval_to_pr",
        user_id="local_engineer",
    )
    content = types.Content(role="user", parts=[types.Part(text=brief)])
    try:
        async for event in runner.run_async(
            user_id="local_engineer",
            session_id=session.id,
            new_message=content,
        ):
            author = getattr(event, "author", "system")
            text_parts = []
            if getattr(event, "content", None) and getattr(event.content, "parts", None):
                for p in event.content.parts:
                    if getattr(p, "text", None):
                        text_parts.append(p.text)
            output = getattr(event, "output", None)
            if text_parts or output is not None:
                print(f"\n=== [{author}] ===")
                if text_parts:
                    print("\n".join(text_parts))
                if output is not None:
                    print(output)
    except Exception as exc:
        if _is_daily_quota_error(exc):
            print(f"\n[FAST-FAIL] Daily Gemini API quota exhausted ({exc}). Stopping immediately.")
            sys.exit(2)
        raise


def main() -> None:
    brief = " ".join(sys.argv[1:]).strip()
    if not brief:
        brief = (
            "Harden backend/sql_guard.py and expand querity_team/eval_cases.json from 6 to 25+ "
            "production & adversarial cases, add pytest regression tests, and pause at HITL."
        )
    asyncio.run(run_brief(brief))


if __name__ == "__main__":
    main()
