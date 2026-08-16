"""
Wraps the NVIDIA API (NVIDIA NIM / AI Foundation Endpoints) & Groq API call that converts a natural language question into SQL.
Includes:
- Automatic detection for NVIDIA (nvapi-...) and Groq (gsk_...) API keys.
- Automatic Retry with Exponential Backoff on temporary capacity spikes or rate limits (429, 503, 504).
- Built-in rate limiter for predictable throughput and quota management.
- Dynamic .env reloading for API key and model changes without requiring a server restart.
"""

import os
import re
import time
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import asyncio
import httpx
from dotenv import load_dotenv

# Load environment variables
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    load_dotenv(dotenv_path=_env_file)
else:
    load_dotenv()

logger = logging.getLogger("text2sql")

# Default Endpoints and Models
DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_NVIDIA_MODEL = "meta/llama-3.3-70b-instruct"

DEFAULT_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"

# System instruction template enforcing safety and output constraints
SYSTEM_INSTRUCTION_TEMPLATE = """You are a PostgreSQL expert. Convert the user's natural language question \
into a single, safe, read-only SQL query.

Schema:
{schema}

Rules:
- Only output SELECT (or WITH ... SELECT) statements. Never INSERT, UPDATE, DELETE, DROP, or ALTER.
- Always include a LIMIT clause (default LIMIT 50 if the user doesn't specify a number).
- Use only the tables and columns listed in the schema above.
- Return ONLY the raw SQL query. No explanation, no markdown code fences, no commentary.
"""


class NVIDIAGenerationError(Exception):
    """Raised when the AI API call fails or returns something unusable."""


class NVIDIARateLimitError(NVIDIAGenerationError):
    """Raised when request would exceed local rate limits or when upstream 429 occurs."""


# ---------------------------------------------------------------------------
# Rate Limiter (60 Requests/Min, 5000 Requests/Day by default)
# ---------------------------------------------------------------------------
class RateLimiter:
    """
    In-memory rate limiter to control request cadence and avoid hitting hard API quotas.
    """
    def __init__(self, max_rpm: int = 60, max_rpd: int = 5000):
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

            # Reset daily counter if a new UTC day has started
            if today != self._current_day:
                self._current_day = today
                self._daily_count = 0

            # 1. Check daily limit
            if self._daily_count >= self.max_rpd:
                raise NVIDIARateLimitError(
                    f"Daily request limit ({self.max_rpd} requests/day) reached. "
                    "Resets at midnight UTC."
                )

            # 2. Evict timestamps older than 60 seconds
            while self._minute_window and now - self._minute_window[0] >= 60.0:
                self._minute_window.popleft()

            # 3. Check per-minute limit
            if len(self._minute_window) >= self.max_rpm:
                oldest = self._minute_window[0]
                wait_seconds = max(1.0, 60.0 - (now - oldest))
                raise NVIDIARateLimitError(
                    f"Rate limit: Exceeded {self.max_rpm} requests/minute limit. "
                    f"Please wait {wait_seconds:.1f}s before sending your next question."
                )

            # Record this request
            self._minute_window.append(now)
            self._daily_count += 1

            return {
                "rpm_remaining": self.max_rpm - len(self._minute_window),
                "rpd_remaining": self.max_rpd - self._daily_count,
            }


# Rate limiter instance
rate_limiter = RateLimiter(max_rpm=60, max_rpd=5000)


def _strip_markdown_fences(text: str) -> str:
    """
    Strips markdown code fences that LLMs sometimes wrap around SQL output.
    Does NOT append LIMIT here — that is handled by validate_sql_safety() in sql_guard.py
    to avoid double-appending which causes syntax errors.
    """
    cleaned = text.strip()
    # Remove leading ```sql or ``` fence
    cleaned = re.sub(r"^```(?:sql)?\s*", "", cleaned, flags=re.IGNORECASE)
    # Remove trailing ``` fence
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    return cleaned


def _get_api_config() -> tuple[str | None, str, str, str]:
    """
    Dynamically retrieves API configuration from environment.
    Reloads .env so changes to NVIDIA_API_KEY, GROQ_API_KEY, NVIDIA_MODEL, etc. take effect immediately.
    """
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)
    else:
        load_dotenv(override=True)

    api_key = (
        os.environ.get("NVIDIA_API_KEY", "").strip()
        or os.environ.get("GROQ_API_KEY", "").strip()
    )
    raw_model = os.environ.get("NVIDIA_MODEL", "").strip() or os.environ.get("GROQ_MODEL", "").strip()
    raw_base_url = os.environ.get("NVIDIA_BASE_URL", "").strip().rstrip("/")

    placeholder_keys = (
        "",
        "your_nvidia_api_key_here",
        "your_groq_api_key_here",
        "your_api_key_here",
        "YOUR_API_KEY",
        "YOUR_NVIDIA_API_KEY",
        "nvapi-placeholder"
    )

    if not api_key or api_key in placeholder_keys:
        return None, raw_model or DEFAULT_NVIDIA_MODEL, raw_base_url or DEFAULT_NVIDIA_BASE_URL, "NVIDIA"

    # Auto-detection: Groq API Key (starts with gsk_)
    if api_key.startswith("gsk_"):
        provider = "Groq"
        base_url = raw_base_url if (raw_base_url and "groq" in raw_base_url) else DEFAULT_GROQ_BASE_URL
        # If user has a meta/ or nvidia/ prefix model configured for Groq, adapt to Groq's model name
        if not raw_model or "meta/" in raw_model or "nvidia/" in raw_model:
            model_name = DEFAULT_GROQ_MODEL
        else:
            model_name = raw_model
        return api_key, model_name, base_url, provider

    # NVIDIA NIM Key
    provider = "NVIDIA"
    base_url = raw_base_url if raw_base_url else DEFAULT_NVIDIA_BASE_URL
    model_name = raw_model if raw_model else DEFAULT_NVIDIA_MODEL
    return api_key, model_name, base_url, provider


async def generate_sql_from_question(question: str, schema_description: str, max_retries: int = 3) -> str:
    """
    Sends the schema description and natural language question to the LLM API (NVIDIA / Groq)
    and returns the generated PostgreSQL query string.
    """
    # 1. Check & enforce local rate limits
    limits = await rate_limiter.acquire()
    logger.info("Rate limit status - RPM remaining: %d, RPD remaining: %d", limits["rpm_remaining"], limits["rpd_remaining"])

    # 2. Get API configuration
    api_key, model_name, base_url, provider = _get_api_config()
    if api_key is None:
        raise NVIDIAGenerationError(
            "API key is missing or set to placeholder in backend/.env. "
            "Please paste your NVIDIA API key (from https://build.nvidia.com) or Groq API key (from https://console.groq.com) into backend/.env."
        )

    system_instruction = SYSTEM_INSTRUCTION_TEMPLATE.format(schema=schema_description.strip())

    endpoint = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": question}
        ],
        "temperature": 0.0,  # Deterministic output for SQL generation
        "max_tokens": 300,
    }

    last_error = None
    backoff_delays = [1.5, 3.0, 5.0]

    async with httpx.AsyncClient(timeout=120.0) as client:
        for attempt in range(max_retries):
            try:
                logger.info("Generating SQL using %s model: %s (attempt %d/%d)", provider, model_name, attempt + 1, max_retries)
                response = await client.post(endpoint, headers=headers, json=payload)

                # Check for rate limits or server errors
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else backoff_delays[min(attempt, len(backoff_delays) - 1)]
                    if attempt < max_retries - 1:
                        logger.warning("%s API 429 Rate Limit. Retrying in %.1fs (attempt %d/%d)...", provider, delay, attempt + 1, max_retries)
                        await asyncio.sleep(delay)
                        continue
                    else:
                        raise NVIDIARateLimitError(
                            f"{provider} API rate limit reached. Please wait a moment before trying again."
                        )

                if response.status_code in (500, 502, 503, 504):
                    err_text = response.text
                    if attempt < max_retries - 1:
                        delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                        logger.warning("%s API server error (%d). Retrying in %.1fs: %s", provider, response.status_code, delay, err_text)
                        await asyncio.sleep(delay)
                        continue
                    else:
                        raise NVIDIAGenerationError(f"{provider} API server error ({response.status_code}): {err_text}")

                if response.status_code == 401:
                    if provider == "NVIDIA" and api_key.startswith("gsk_"):
                        hint = " (Your key starts with 'gsk_', which is a Groq key, not an NVIDIA key)."
                    elif provider == "NVIDIA" and not api_key.startswith("nvapi-"):
                        hint = " (NVIDIA keys usually start with 'nvapi-'. Check https://build.nvidia.com)."
                    else:
                        hint = ""
                    raise NVIDIAGenerationError(f"{provider} API Authentication failed (HTTP 401). Please check your API key in backend/.env{hint}")

                if response.status_code != 200:
                    error_detail = response.text
                    try:
                        error_json = response.json()
                        error_detail = error_json.get("error", {}).get("message", error_detail)
                    except Exception:
                        pass
                    raise NVIDIAGenerationError(f"{provider} API request failed (HTTP {response.status_code}): {error_detail}")

                data = response.json()
                choices = data.get("choices", [])
                if not choices:
                    raise NVIDIAGenerationError(f"{provider} API returned an empty choices list.")

                raw_text = choices[0].get("message", {}).get("content", "")
                if not raw_text or not raw_text.strip():
                    finish_reason = choices[0].get("finish_reason", "unknown")
                    raise NVIDIAGenerationError(f"{provider} model returned an empty text response (finish_reason={finish_reason}).")

                # Strip markdown code fences
                return _strip_markdown_fences(raw_text)

            except (NVIDIAGenerationError, NVIDIARateLimitError):
                raise
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt < max_retries - 1:
                    delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                    logger.warning("%s API request timed out. Retrying in %.1fs...", provider, delay)
                    await asyncio.sleep(delay)
                    continue
                raise NVIDIAGenerationError(f"{provider} API request timed out after {max_retries} attempts.") from exc
            except Exception as exc:
                last_error = exc
                err_str = str(exc).lower()
                is_transient = any(term in err_str for term in ("connection", "reset", "timeout", "network"))
                if is_transient and attempt < max_retries - 1:
                    delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                    logger.warning("Transient error calling %s API: %s. Retrying in %.1fs...", provider, exc, delay)
                    await asyncio.sleep(delay)
                    continue
                logger.error("%s API call failed (%s): %s", provider, model_name, exc)
                raise NVIDIAGenerationError(f"{provider} API call failed: {exc}") from exc

    raise NVIDIAGenerationError(str(last_error)) if last_error else NVIDIAGenerationError("Could not generate SQL.")
