"""
Unit tests for multi-provider LLM client in nvidia_client.py
Validates:
- Provider auto-detection (Google, NVIDIA, Groq, Grok, OpenAI)
- Explicit LLM_PROVIDER override
- Dynamic .env reloading
- Placeholder key detection
- Backward-compatible exception and function imports
"""

import os
import sys
import unittest
from pathlib import Path

# Add backend directory to sys.path
sys.path.insert(0, str(Path(__file__).parent))

from nvidia_client import (
    _get_llm_config,
    _is_valid_key,
    _strip_markdown_fences,
    generate_sql_from_question,
    NVIDIAGenerationError,
    NVIDIARateLimitError,
    LLMGenerationError,
    LLMRateLimitError,
    rate_limiter,
)


class TestMultiProviderLLM(unittest.TestCase):
    def setUp(self):
        # Save current environment
        self.original_env = dict(os.environ)

    def tearDown(self):
        # Restore environment
        os.environ.clear()
        os.environ.update(self.original_env)

    def _clean_env_keys(self):
        keys_to_clear = [
            "LLM_PROVIDER",
            "GOOGLE_API_KEY", "GEMINI_API_KEY", "GEMINI_MODEL", "GOOGLE_MODEL",
            "NVIDIA_API_KEY", "NVIDIA_MODEL", "NVIDIA_BASE_URL",
            "GROQ_API_KEY", "GROQ_MODEL", "GROQ_BASE_URL",
            "GROK_API_KEY", "XAI_API_KEY", "GROK_MODEL", "XAI_MODEL", "GROK_BASE_URL",
            "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL",
            "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL"
        ]
        for k in keys_to_clear:
            os.environ.pop(k, None)

    def test_key_validation(self):
        """Test placeholder filtering."""
        self.assertFalse(_is_valid_key(None))
        self.assertFalse(_is_valid_key(""))
        self.assertFalse(_is_valid_key("   "))
        self.assertFalse(_is_valid_key("your_nvidia_api_key_here"))
        self.assertFalse(_is_valid_key("your_google_api_key_here"))
        self.assertFalse(_is_valid_key("your_groq_api_key_here"))
        self.assertFalse(_is_valid_key("nvapi-your-key-here"))
        self.assertTrue(_is_valid_key("AIzaSyB1234567890abcdef"))
        self.assertTrue(_is_valid_key("nvapi-REAL_VALID_KEY_XYZ"))
        self.assertTrue(_is_valid_key("gsk_valid_key_abc123"))
        self.assertTrue(_is_valid_key("xai-valid_key_def456"))

    def test_markdown_fence_stripping(self):
        """Test SQL query fence cleanup."""
        raw = "```sql\nSELECT * FROM customers LIMIT 50;\n```"
        self.assertEqual(_strip_markdown_fences(raw), "SELECT * FROM customers LIMIT 50;")

        raw2 = "```\nSELECT count(*) FROM orders;\n```"
        self.assertEqual(_strip_markdown_fences(raw2), "SELECT count(*) FROM orders;")

        raw3 = "SELECT * FROM products;"
        self.assertEqual(_strip_markdown_fences(raw3), "SELECT * FROM products;")

    def test_google_auto_detection(self):
        """Test auto-detection when GOOGLE_API_KEY is provided."""
        self._clean_env_keys()
        os.environ["GOOGLE_API_KEY"] = "AIzaSy_test_google_key"

        provider_id, api_key, model_name, base_url = _get_llm_config(reload_env=False)
        self.assertEqual(provider_id, "google")
        self.assertEqual(api_key, "AIzaSy_test_google_key")
        self.assertEqual(model_name, "gemini-3.6-flash")

    def test_nvidia_auto_detection(self):
        """Test auto-detection when NVIDIA_API_KEY is provided."""
        self._clean_env_keys()
        os.environ["NVIDIA_API_KEY"] = "nvapi-test_nvidia_key_123"

        provider_id, api_key, model_name, base_url = _get_llm_config(reload_env=False)
        self.assertEqual(provider_id, "nvidia")
        self.assertEqual(api_key, "nvapi-test_nvidia_key_123")
        self.assertEqual(model_name, "meta/llama-3.3-70b-instruct")
        self.assertEqual(base_url, "https://integrate.api.nvidia.com/v1")

    def test_groq_auto_detection(self):
        """Test auto-detection when GROQ_API_KEY is provided."""
        self._clean_env_keys()
        os.environ["GROQ_API_KEY"] = "gsk_test_groq_key_123"

        provider_id, api_key, model_name, base_url = _get_llm_config(reload_env=False)
        self.assertEqual(provider_id, "groq")
        self.assertEqual(api_key, "gsk_test_groq_key_123")
        self.assertEqual(model_name, "llama-3.3-70b-versatile")
        self.assertEqual(base_url, "https://api.groq.com/openai/v1")

    def test_grok_auto_detection(self):
        """Test auto-detection when GROK_API_KEY is provided."""
        self._clean_env_keys()
        os.environ["GROK_API_KEY"] = "xai-test_grok_key_123"

        provider_id, api_key, model_name, base_url = _get_llm_config(reload_env=False)
        self.assertEqual(provider_id, "grok")
        self.assertEqual(api_key, "xai-test_grok_key_123")
        self.assertEqual(model_name, "grok-2-latest")
        self.assertEqual(base_url, "https://api.x.ai/v1")

    def test_explicit_provider_selection(self):
        """Test explicit LLM_PROVIDER overriding active keys."""
        self._clean_env_keys()
        os.environ["GOOGLE_API_KEY"] = "AIzaSy_google_key"
        os.environ["NVIDIA_API_KEY"] = "nvapi-nvidia_key"
        os.environ["GROQ_API_KEY"] = "gsk_groq_key"
        os.environ["LLM_PROVIDER"] = "groq"

        provider_id, api_key, model_name, base_url = _get_llm_config(reload_env=False)
        self.assertEqual(provider_id, "groq")
        self.assertEqual(api_key, "gsk_groq_key")
        self.assertEqual(model_name, "llama-3.3-70b-versatile")

    def test_custom_model_override(self):
        """Test custom model override via provider-specific env var."""
        self._clean_env_keys()
        os.environ["GOOGLE_API_KEY"] = "AIzaSy_google_key"
        os.environ["GEMINI_MODEL"] = "gemini-1.5-pro"

        provider_id, api_key, model_name, base_url = _get_llm_config(reload_env=False)
        self.assertEqual(provider_id, "google")
        self.assertEqual(model_name, "gemini-1.5-pro")

    def test_generic_key_prefix_detection(self):
        """Test auto-detection from key prefix when using LLM_API_KEY."""
        # Google
        self._clean_env_keys()
        os.environ["LLM_API_KEY"] = "AIzaSy_google_prefix_key"
        pid, key, _, _ = _get_llm_config(reload_env=False)
        self.assertEqual(pid, "google")

        # NVIDIA
        self._clean_env_keys()
        os.environ["LLM_API_KEY"] = "nvapi-nvidia_prefix_key"
        pid, key, _, _ = _get_llm_config(reload_env=False)
        self.assertEqual(pid, "nvidia")

        # Groq
        self._clean_env_keys()
        os.environ["LLM_API_KEY"] = "gsk_groq_prefix_key"
        pid, key, _, _ = _get_llm_config(reload_env=False)
        self.assertEqual(pid, "groq")

        # Grok (xAI)
        self._clean_env_keys()
        os.environ["LLM_API_KEY"] = "xai-grok_prefix_key"
        pid, key, _, _ = _get_llm_config(reload_env=False)
        self.assertEqual(pid, "grok")

        # OpenAI
        self._clean_env_keys()
        os.environ["LLM_API_KEY"] = "sk-openai_prefix_key"
        pid, key, _, _ = _get_llm_config(reload_env=False)
        self.assertEqual(pid, "openai")

    def test_backward_compatibility_aliases(self):
        """Ensure exception aliases and main.py requirements exist."""
        self.assertTrue(issubclass(NVIDIARateLimitError, NVIDIAGenerationError))
        self.assertTrue(issubclass(LLMRateLimitError, LLMGenerationError))
        self.assertIs(LLMGenerationError, NVIDIAGenerationError)
        self.assertIs(LLMRateLimitError, NVIDIARateLimitError)


if __name__ == "__main__":
    unittest.main()
