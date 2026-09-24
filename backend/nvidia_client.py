"""
Unified Multi-Provider LLM client for converting natural language questions into SQL.
Supports:
- Google Gemini (gemini-2.0-flash, gemini-1.5-flash, gemini-1.5-pro)
- NVIDIA NIM (meta/llama-3.3-70b-instruct, nemotron, etc.)
- Groq (llama-3.3-70b-versatile, llama-3.1-8b-instant, etc.)
- xAI Grok (grok-2-latest, grok-beta, etc.)
- OpenAI / OpenAI-compatible endpoints

Includes:
- Automatic provider detection based on available API key in .env or key format.
- Optional explicit provider selection via LLM_PROVIDER in .env.
- Automatic retry with exponential backoff on transient errors (429, timeouts, 5xx).
- Built-in rate limiter for predictable throughput and quota management.
- Dynamic .env reloading for API key and model changes without requiring a server restart.
- 100% backward-compatible interface for main.py (generate_sql_from_question, NVIDIAGenerationError, NVIDIARateLimitError).
"""

import os
import re
import time
import logging
import asyncio
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

from dotenv import load_dotenv
import httpx

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
    load_dotenv(dotenv_path=_env_file)
else:
    load_dotenv()

logger = logging.getLogger("text2sql")

# Provider defaults
DEFAULT_PROVIDERS = {
    "google": {
        "name": "Google Gemini",
        "default_model": "gemini-3.6-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
    },
    "nvidia": {
        "name": "NVIDIA NIM",
        "default_model": "meta/llama-3.3-70b-instruct",
        "base_url": "https://integrate.api.nvidia.com/v1",
    },
    "groq": {
        "name": "Groq",
        "default_model": "llama-3.3-70b-versatile",
        "base_url": "https://api.groq.com/openai/v1",
    },
    "grok": {
        "name": "xAI Grok",
        "default_model": "grok-2-latest",
        "base_url": "https://api.x.ai/v1",
    },
    "openai": {
        "name": "OpenAI",
        "default_model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
    },
}

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

# ---------------------------------------------------------------------------
# Exception classes — kept identical so main.py needs no changes
# ---------------------------------------------------------------------------
class NVIDIAGenerationError(Exception):
    """Raised when an LLM API call fails or returns something unusable."""


class NVIDIARateLimitError(NVIDIAGenerationError):
    """Raised when request would exceed local rate limits or when upstream quota is hit."""


# Aliases for generic LLM naming
LLMGenerationError = NVIDIAGenerationError
LLMRateLimitError = NVIDIARateLimitError


# ---------------------------------------------------------------------------
# Rate Limiter (60 Requests/Min, 1500 Requests/Day)
# ---------------------------------------------------------------------------
class RateLimiter:
    """
    In-memory rate limiter to control request cadence and avoid hitting hard API quotas.
    """
    def __init__(self, max_rpm: int = 60, max_rpd: int = 1500):
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
                raise LLMRateLimitError(
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
                raise LLMRateLimitError(
                    f"Rate limit: Exceeded {self.max_rpm} requests/minute. "
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
rate_limiter = RateLimiter(max_rpm=60, max_rpd=1500)


def _strip_markdown_fences(text: str) -> str:
    """
    Strips markdown code fences that LLMs sometimes wrap around SQL output.
    Does NOT append LIMIT here — that is handled by validate_sql_safety() in sql_guard.py.
    """
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:sql)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    return cleaned


def _is_valid_key(key: Optional[str]) -> bool:
    """Checks if an API key is set and not a placeholder."""
    if not key:
        return False
    val = key.strip().lower()
    if not val:
        return False
    placeholders = (
        "your_",
        "placeholder",
        "xxx",
        "api_key_here",
        "change_me",
        "replace_me",
        "nvapi-your-key-here",
    )
    return not any(p in val for p in placeholders)


_last_env_mtime: float = 0.0


def _get_llm_config(reload_env: bool = True) -> Tuple[str, str, str, str]:
    """
    Dynamically retrieves the active LLM provider, API key, model name, and base URL from environment.
    Reloads .env when file is modified on disk so changes take effect immediately without restart.

    Returns:
        tuple of (provider_id, api_key, model_name, base_url)
    """
    global _last_env_mtime
    if reload_env:
        env_path = Path(__file__).parent / ".env"
        if env_path.exists():
            try:
                mtime = env_path.stat().st_mtime
                if mtime != _last_env_mtime:
                    load_dotenv(dotenv_path=env_path, override=True)
                    _last_env_mtime = mtime
            except OSError:
                pass

    explicit_provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if explicit_provider in ("gemini", "google_ai"):
        explicit_provider = "google"
    elif explicit_provider in ("xai",):
        explicit_provider = "grok"

    # Gather available keys
    google_key = os.environ.get("GOOGLE_API_KEY", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
    nvidia_key = os.environ.get("NVIDIA_API_KEY", "").strip()
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    grok_key = os.environ.get("GROK_API_KEY", "").strip() or os.environ.get("XAI_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    generic_key = os.environ.get("LLM_API_KEY", "").strip()

    # Determine provider and key
    provider_id = None
    api_key = None

    # Priority 1: Explicit provider specified
    if explicit_provider:
        provider_id = explicit_provider
        if provider_id == "google":
            api_key = google_key or generic_key
        elif provider_id == "nvidia":
            api_key = nvidia_key or generic_key
        elif provider_id == "groq":
            api_key = groq_key or generic_key
        elif provider_id == "grok":
            api_key = grok_key or generic_key
        elif provider_id == "openai":
            api_key = openai_key or generic_key

    # Priority 2: Auto-detect by valid specific key
    if not provider_id or not _is_valid_key(api_key):
        if _is_valid_key(google_key):
            provider_id = "google"
            api_key = google_key
        elif _is_valid_key(nvidia_key):
            provider_id = "nvidia"
            api_key = nvidia_key
        elif _is_valid_key(groq_key):
            provider_id = "groq"
            api_key = groq_key
        elif _is_valid_key(grok_key):
            provider_id = "grok"
            api_key = grok_key
        elif _is_valid_key(openai_key):
            provider_id = "openai"
            api_key = openai_key
        elif _is_valid_key(generic_key):
            # Auto-detect by key prefix format
            if generic_key.startswith("AIza"):
                provider_id = "google"
            elif generic_key.startswith("nvapi-"):
                provider_id = "nvidia"
            elif generic_key.startswith("gsk_"):
                provider_id = "groq"
            elif generic_key.startswith("xai-"):
                provider_id = "grok"
            elif generic_key.startswith("sk-"):
                provider_id = "openai"
            else:
                provider_id = "nvidia"
            api_key = generic_key

    # If still not found, return empty info with default provider
    if not provider_id:
        provider_id = "google"
    if not _is_valid_key(api_key):
        api_key = ""

    defaults = DEFAULT_PROVIDERS.get(provider_id, DEFAULT_PROVIDERS["google"])

    # Determine model
    model_env_var = {
        "google": ["GEMINI_MODEL", "GOOGLE_MODEL", "LLM_MODEL"],
        "nvidia": ["NVIDIA_MODEL", "LLM_MODEL"],
        "groq": ["GROQ_MODEL", "LLM_MODEL"],
        "grok": ["GROK_MODEL", "XAI_MODEL", "LLM_MODEL"],
        "openai": ["OPENAI_MODEL", "LLM_MODEL"],
    }.get(provider_id, ["LLM_MODEL"])

    model_name = ""
    for var in model_env_var:
        val = os.environ.get(var, "").strip()
        if val:
            model_name = val
            break
    if not model_name:
        model_name = defaults["default_model"]

    # Determine base URL
    base_url_env_var = {
        "google": ["GOOGLE_BASE_URL", "GEMINI_BASE_URL", "LLM_BASE_URL"],
        "nvidia": ["NVIDIA_BASE_URL", "LLM_BASE_URL"],
        "groq": ["GROQ_BASE_URL", "LLM_BASE_URL"],
        "grok": ["GROK_BASE_URL", "XAI_BASE_URL", "LLM_BASE_URL"],
        "openai": ["OPENAI_BASE_URL", "LLM_BASE_URL"],
    }.get(provider_id, ["LLM_BASE_URL"])

    base_url = ""
    for var in base_url_env_var:
        val = os.environ.get(var, "").strip().rstrip("/")
        if val:
            base_url = val
            break
    if not base_url:
        base_url = defaults["base_url"]

    return provider_id, api_key, model_name, base_url


def get_active_provider_info() -> Dict[str, Any]:
    """Helper to inspect active LLM provider configuration."""
    provider_id, api_key, model_name, base_url = _get_llm_config()
    provider_meta = DEFAULT_PROVIDERS.get(provider_id, DEFAULT_PROVIDERS["google"])
    return {
        "provider_id": provider_id,
        "provider_name": provider_meta["name"],
        "model_name": model_name,
        "base_url": base_url,
        "has_valid_key": _is_valid_key(api_key),
    }


async def _generate_with_google_sdk(api_key: str, model_name: str, question: str, system_instruction: str) -> str:
    """Generates SQL using the official google-genai SDK."""
    client = genai.Client(api_key=api_key)
    config = genai_types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=0.0,
        max_output_tokens=512,
    )
    response = await client.aio.models.generate_content(
        model=model_name,
        contents=question,
        config=config,
    )
    raw_text = response.text if response.text else ""
    return _strip_markdown_fences(raw_text)


async def _generate_with_openai_compatible(
    provider_name: str,
    base_url: str,
    api_key: str,
    model_name: str,
    question: str,
    system_instruction: str,
    max_retries: int = 3
) -> str:
    """
    Generates SQL using an OpenAI-compatible REST API (NVIDIA NIM, Groq, xAI Grok, OpenAI, or Google fallback).
    """
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
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
        "temperature": 0.0,
        "max_tokens": 512,
    }

    backoff_delays = [1.5, 3.0, 5.0]
    last_error = None

    async with httpx.AsyncClient(timeout=90.0) as client:
        for attempt in range(max_retries):
            try:
                logger.info(
                    "Generating SQL using %s model: %s (attempt %d/%d)",
                    provider_name, model_name, attempt + 1, max_retries
                )
                response = await client.post(endpoint, headers=headers, json=payload)

                # 429: Rate limit hit
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after and retry_after.isdigit() else backoff_delays[min(attempt, len(backoff_delays) - 1)]
                    if attempt < max_retries - 1:
                        logger.warning(
                            "%s API 429 Rate Limit. Retrying in %.1fs (attempt %d/%d)...",
                            provider_name, delay, attempt + 1, max_retries
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise LLMRateLimitError(
                        f"{provider_name} API rate limit reached. Please wait a moment before trying again."
                    )

                # 5xx: Server errors
                if response.status_code in (500, 502, 503, 504):
                    err_text = response.text
                    if attempt < max_retries - 1:
                        delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                        logger.warning(
                            "%s API server error (%d). Retrying in %.1fs...",
                            provider_name, response.status_code, delay
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise LLMGenerationError(
                        f"{provider_name} API server error ({response.status_code}): {err_text}"
                    )

                # 401 / 403: Authentication errors
                if response.status_code in (401, 403):
                    hint = ""
                    if provider_name == "NVIDIA NIM" and not api_key.startswith("nvapi-"):
                        hint = " (NVIDIA keys start with 'nvapi-'. Get one at https://build.nvidia.com)"
                    elif provider_name == "Groq" and not api_key.startswith("gsk_"):
                        hint = " (Groq keys start with 'gsk_'. Get one at https://console.groq.com)"
                    elif provider_name == "xAI Grok" and not api_key.startswith("xai-"):
                        hint = " (xAI Grok keys start with 'xai-'. Get one at https://console.x.ai)"
                    elif provider_name == "Google Gemini":
                        hint = " (Get a free key at https://aistudio.google.com/apikey)"
                    raise LLMGenerationError(
                        f"{provider_name} API Authentication failed (HTTP {response.status_code}). "
                        f"Please check your API key in backend/.env{hint}"
                    )

                if response.status_code != 200:
                    error_detail = response.text
                    try:
                        error_json = response.json()
                        error_detail = error_json.get("error", {}).get("message", error_detail)
                    except Exception:
                        pass
                    raise LLMGenerationError(
                        f"{provider_name} API request failed (HTTP {response.status_code}): {error_detail}"
                    )

                data = response.json()
                choices = data.get("choices", [])
                if not choices:
                    raise LLMGenerationError(f"{provider_name} API returned an empty choices list.")

                raw_text = choices[0].get("message", {}).get("content", "")
                if not raw_text or not raw_text.strip():
                    finish_reason = choices[0].get("finish_reason", "unknown")
                    raise LLMGenerationError(
                        f"{provider_name} model returned an empty text response (finish_reason={finish_reason})."
                    )

                return _strip_markdown_fences(raw_text)

            except (LLMGenerationError, LLMRateLimitError):
                raise
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt < max_retries - 1:
                    delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                    logger.warning("%s API request timed out. Retrying in %.1fs...", provider_name, delay)
                    await asyncio.sleep(delay)
                    continue
                raise LLMGenerationError(f"{provider_name} API request timed out after {max_retries} attempts.") from exc
            except Exception as exc:
                last_error = exc
                err_str = str(exc).lower()
                is_transient = any(t in err_str for t in ("connection", "reset", "timeout", "network", "unavailable"))
                if is_transient and attempt < max_retries - 1:
                    delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                    logger.warning("Transient error calling %s API: %s. Retrying in %.1fs...", provider_name, exc, delay)
                    await asyncio.sleep(delay)
                    continue
                logger.error("%s API call failed (%s): %s", provider_name, model_name, exc)
                raise LLMGenerationError(f"{provider_name} API call failed: {exc}") from exc

    raise LLMGenerationError(str(last_error)) if last_error else LLMGenerationError("Could not generate SQL.")


async def generate_sql_from_question(question: str, schema_description: str, max_retries: int = 3) -> str:
    """
    Unified entry point: Sends the schema description and natural language question
    to the configured LLM (Google Gemini, NVIDIA NIM, Groq, xAI Grok, or OpenAI)
    and returns the generated PostgreSQL query string.
    """
    # 1. Check & enforce rate limits
    limits = await rate_limiter.acquire()
    logger.info(
        "Rate limit status — RPM remaining: %d, RPD remaining: %d",
        limits["rpm_remaining"], limits["rpd_remaining"]
    )

    # 2. Identify active provider & credentials
    provider_id, api_key, model_name, base_url = _get_llm_config()
    provider_info = DEFAULT_PROVIDERS.get(provider_id, DEFAULT_PROVIDERS["google"])
    provider_name = provider_info["name"]

    if not _is_valid_key(api_key):
        raise LLMGenerationError(
            "No valid LLM API key found in backend/.env.\n\n"
            "Please configure ONE of the following in backend/.env:\n"
            "  • Google Gemini: GOOGLE_API_KEY=AIza... (Free at https://aistudio.google.com/apikey)\n"
            "  • NVIDIA NIM:    NVIDIA_API_KEY=nvapi-... (Free credits at https://build.nvidia.com)\n"
            "  • Groq:          GROQ_API_KEY=gsk_... (Fast & free at https://console.groq.com)\n"
            "  • xAI Grok:      GROK_API_KEY=xai-... (At https://console.x.ai)\n"
            "  • OpenAI:        OPENAI_API_KEY=sk-... (At https://platform.openai.com)"
        )

    system_instruction = SYSTEM_INSTRUCTION_TEMPLATE.format(schema=schema_description.strip())

    # 3. Provider-specific dispatch
    if provider_id == "google" and HAS_GOOGLE_GENAI:
        backoff_delays = [1.5, 3.0, 5.0]
        last_error = None
        for attempt in range(max_retries):
            try:
                logger.info(
                    "Generating SQL using Google Gemini model: %s (attempt %d/%d)",
                    model_name, attempt + 1, max_retries
                )
                return await _generate_with_google_sdk(api_key, model_name, question, system_instruction)
            except Exception as exc:
                last_error = exc
                err_str = str(exc).lower()

                if "quota" in err_str or "resource_exhausted" in err_str or "429" in err_str:
                    if attempt < max_retries - 1:
                        delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                        logger.warning("Gemini quota/rate limit hit. Retrying in %.1fs...", delay)
                        await asyncio.sleep(delay)
                        continue
                    raise LLMRateLimitError("Gemini API quota exceeded. Please wait a moment and try again.") from exc

                if any(t in err_str for t in ("api_key", "invalid", "permission", "403")):
                    raise LLMGenerationError(
                        "Google Gemini authentication failed. Please check GOOGLE_API_KEY in backend/.env "
                        "(Get a key at https://aistudio.google.com/apikey)"
                    ) from exc

                is_transient = any(t in err_str for t in ("connection", "reset", "timeout", "network", "unavailable"))
                if is_transient and attempt < max_retries - 1:
                    delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                    logger.warning("Transient Gemini error: %s. Retrying in %.1fs...", exc, delay)
                    await asyncio.sleep(delay)
                    continue

                # Fallback to OpenAI-compatible REST endpoint if SDK failed unexpectedly
                logger.warning("Google GenAI SDK call failed (%s), attempting OpenAI endpoint fallback: %s", model_name, exc)
                try:
                    return await _generate_with_openai_compatible(
                        provider_name="Google Gemini",
                        base_url=base_url,
                        api_key=api_key,
                        model_name=model_name,
                        question=question,
                        system_instruction=system_instruction,
                        max_retries=1
                    )
                except Exception:
                    pass

                raise LLMGenerationError(f"Google Gemini API call failed: {exc}") from exc

        raise LLMGenerationError(str(last_error)) if last_error else LLMGenerationError("Could not generate SQL.")

    # Standard OpenAI-compatible dispatch for NVIDIA NIM, Groq, xAI Grok, OpenAI, or Google REST fallback
    return await _generate_with_openai_compatible(
        provider_name=provider_name,
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
        question=question,
        system_instruction=system_instruction,
        max_retries=max_retries
    )
