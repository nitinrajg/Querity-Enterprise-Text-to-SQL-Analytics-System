"""
Google Gemini Text-to-SQL Engine.
Converts natural language questions into deterministic, read-only PostgreSQL queries.

Powered by Google GenAI (Gemini 2.5 / 3.5 Flash & Flash-Lite).
Provides backward-compatible interface for main.py.
"""

import os
import re
import time
import logging
import asyncio
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

from dotenv import load_dotenv

# Optional native Google GenAI SDK
try:
    from google import genai
    from google.genai import types as genai_types
    HAS_GOOGLE_GENAI = True
except ImportError:
    HAS_GOOGLE_GENAI = False

# Load environment variables
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    load_dotenv(dotenv_path=_env_file, override=True)
else:
    load_dotenv(override=True)

logger = logging.getLogger("text2sql")

# System Instruction Template with Strict Schema & Output Rules + Few-Shot Examples
SYSTEM_INSTRUCTION_TEMPLATE = """You are an elite PostgreSQL Data Engineer and Text-to-SQL Specialist.
Your task is to convert the user's natural language question into an accurate, highly optimized, deterministic, read-only PostgreSQL query.

Database Schema & Relationships:
{schema}

Execution Guidelines:
1. PROJECTION & COLUMN SELECTION:
   - Only select the columns specifically requested or directly relevant to the question.
   - When asked "Which customers..." or "List customers...", select their identifier and name (`c.id, c.name` or `c.name`) plus any requested metric or filter column. Do NOT select `*` or all table columns unless "all details" is explicitly requested.
   - When the user asks for specific attributes (e.g. "customer name and order date"), project ONLY those requested attributes: `c.name, o.order_date`.
   - Explicitly qualify EVERY column with its table alias (e.g. `c.name`, `o.total_amount`).
   - If filtering strings, use case-insensitive matching: `column ILIKE '%term%'` or `LOWER(column) = LOWER('term')`.

2. MULTI-TABLE JOINS:
   - Join tables using primary and foreign keys provided in the schema.
   - Use `INNER JOIN` when records must exist in both tables; use `LEFT JOIN` when retrieving all primary records even if related rows are missing.

3. AGGREGATIONS & ORDERING:
   - Include ALL non-aggregated SELECT columns in the `GROUP BY` clause.
   - For superlative queries ("top", "highest", "most"): order by the relevant metric `DESC` and apply appropriate `LIMIT`.
   - Default to `LIMIT 50` unless the question specifies a different limit or is a single-value scalar aggregate (e.g. `COUNT(*)`).

4. WINDOW FUNCTIONS & CTEs:
   - For ranking or running totals across categories, use `OVER (PARTITION BY ... ORDER BY ...)`.
   - For complex multi-step aggregations, use Common Table Expressions: `WITH cte_name AS (...) SELECT ...`.

5. SAFETY & CONSTRAINTS:
   - NEVER emit `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `GRANT`, `REVOKE`, or stacked queries with semicolons.
   - Return ONLY the raw SQL query. No explanations, no markdown formatting, no code fences.

Few-Shot Examples:
Q: "Show the customer name and order date for every order"
A: SELECT c.name, o.order_date FROM customers c JOIN orders o ON c.id = o.customer_id ORDER BY o.id ASC LIMIT 50;

Q: "Which customers signed up before March 2024?"
A: SELECT c.id, c.name, c.signed_up_at FROM customers c WHERE c.signed_up_at < '2024-03-01' ORDER BY c.signed_up_at ASC LIMIT 50;

Q: "Find top 5 customers by total order spend"
A: SELECT c.id, c.name, SUM(o.total_amount) AS total_spent FROM customers c JOIN orders o ON c.id = o.customer_id GROUP BY c.id, c.name ORDER BY total_spent DESC LIMIT 5;

Q: "Show each product alongside the average price of products in its category"
A: SELECT p.id, p.name, p.category, p.price, AVG(p.price) OVER (PARTITION BY p.category) AS avg_category_price FROM products p ORDER BY p.category, p.name LIMIT 50;

Q: "Find products with price above the overall average product price"
A: SELECT p.id, p.name, p.price FROM products p WHERE p.price > (SELECT AVG(price) FROM products) ORDER BY p.price DESC LIMIT 50;
"""

# ---------------------------------------------------------------------------
# Exception classes — kept backward-compatible with main.py
# ---------------------------------------------------------------------------
class NVIDIAGenerationError(Exception):
    """Raised when an LLM API call fails or returns an unparseable response."""


class NVIDIARateLimitError(NVIDIAGenerationError):
    """Raised when request would exceed rate limits or upstream quota is hit."""


LLMGenerationError = NVIDIAGenerationError
LLMRateLimitError = NVIDIARateLimitError


# ---------------------------------------------------------------------------
# Rate Limiter (15 RPM / 1000 RPD)
# ---------------------------------------------------------------------------
class RateLimiter:
    """In-memory rate limiter to pace requests within Google AI Studio limits."""
    def __init__(self, max_rpm: int = 15, max_rpd: int = 1000):
        self.max_rpm = max_rpm
        self.max_rpd = max_rpd
        self._minute_window: deque[float] = deque()
        self._daily_count = 0
        self._current_day = datetime.now(timezone.utc).date()
        self._lock = asyncio.Lock()

    async def acquire(self) -> dict[str, int]:
        async with self._lock:
            now = time.time()
            today = datetime.now(timezone.utc).date()

            if today != self._current_day:
                self._current_day = today
                self._daily_count = 0

            if self._daily_count >= self.max_rpd:
                raise LLMRateLimitError(
                    f"Daily Gemini limit reached ({self.max_rpd} requests/day). Resets at midnight UTC."
                )

            cutoff = now - 60.0
            while self._minute_window and self._minute_window[0] < cutoff:
                self._minute_window.popleft()

            if len(self._minute_window) >= self.max_rpm:
                oldest = self._minute_window[0]
                wait_time = max(0.1, 60.0 - (now - oldest) + 0.1)
                logger.warning("Gemini RPM ceiling hit. Pacing request by %.1fs...", wait_time)
                await asyncio.sleep(wait_time)
                now = time.time()
                while self._minute_window and self._minute_window[0] < (now - 60.0):
                    self._minute_window.popleft()

            self._minute_window.append(now)
            self._daily_count += 1

            return {
                "rpm_current": len(self._minute_window),
                "rpm_limit": self.max_rpm,
                "rpd_current": self._daily_count,
                "rpd_limit": self.max_rpd,
            }


_rate_limiter = RateLimiter(max_rpm=15, max_rpd=1000)


def _get_api_key() -> str:
    key = os.environ.get("GOOGLE_API_KEY", "").strip()
    return key


def _get_model_name() -> str:
    return os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()


def _strip_markdown_fences(text: str) -> str:
    """Removes ```sql ... ``` fences and whitespace."""
    cleaned = text.strip()
    match = re.search(r"```(?:sql)?\s*([\s\S]*?)\s*```", cleaned, re.IGNORECASE)
    if match:
        cleaned = match.group(1).strip()
    lines = [line for line in cleaned.splitlines() if not line.strip().startswith("```")]
    return "\n".join(lines).strip()


def get_active_provider_info() -> Dict[str, Any]:
    """Inspect active Gemini configuration."""
    api_key = _get_api_key()
    model = _get_model_name()
    return {
        "provider_id": "google",
        "provider_name": "Google Gemini",
        "model_name": model,
        "base_url": "https://generativelanguage.googleapis.com",
        "has_valid_key": bool(api_key and len(api_key) > 10 and not api_key.startswith("your_")),
    }


async def generate_sql_from_question(
    question: str,
    schema: str,
    max_retries: int = 3
) -> str:
    """
    Converts a natural language question into a read-only PostgreSQL query using Google Gemini.
    """
    api_key = _get_api_key()
    if not api_key or api_key.startswith("your_"):
        raise LLMGenerationError(
            "GOOGLE_API_KEY is not configured in backend/.env. Please provide a valid Gemini API key."
        )

    model_name = _get_model_name()
    system_instruction = SYSTEM_INSTRUCTION_TEMPLATE.format(schema=schema)

    # Acquire rate limit slot
    await _rate_limiter.acquire()

    if not HAS_GOOGLE_GENAI:
        raise LLMGenerationError("google-genai SDK is not installed. Please run: pip install google-genai")

    client = genai.Client(api_key=api_key)
    config = genai_types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=0.0,
        max_output_tokens=1024,
    )

    backoff = [1.5, 3.0, 5.0]
    last_exc = None

    for attempt in range(max_retries):
        try:
            logger.info("Generating SQL with Gemini model '%s' (attempt %d/%d)", model_name, attempt + 1, max_retries)
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=question,
                config=config,
            )
            raw_text = response.text if response.text else ""
            sql = _strip_markdown_fences(raw_text)

            if not sql:
                raise LLMGenerationError("Gemini returned an empty response.")

            return sql

        except Exception as exc:
            err_msg = str(exc)
            last_exc = exc

            # Rate limit or quota error
            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg or "quota" in err_msg.lower():
                if attempt < max_retries - 1:
                    delay = backoff[min(attempt, len(backoff) - 1)]
                    logger.warning("Gemini 429 quota/rate limit. Retrying in %.1fs...", delay)
                    await asyncio.sleep(delay)
                    continue
                raise LLMRateLimitError("Gemini API daily or RPM quota reached. Please retry later.") from exc

            # Transient 503 high demand
            if "503" in err_msg or "UNAVAILABLE" in err_msg:
                if attempt < max_retries - 1:
                    delay = backoff[min(attempt, len(backoff) - 1)]
                    logger.warning("Gemini 503 high demand. Retrying in %.1fs...", delay)
                    await asyncio.sleep(delay)
                    continue

            # Other errors
            if attempt < max_retries - 1:
                delay = backoff[min(attempt, len(backoff) - 1)]
                logger.warning("Gemini call error: %s. Retrying in %.1fs...", err_msg[:120], delay)
                await asyncio.sleep(delay)
                continue

    raise LLMGenerationError(f"Failed to generate SQL after {max_retries} attempts: {last_exc}") from last_exc
