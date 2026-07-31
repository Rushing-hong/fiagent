"""Unit tests for multi-provider LLM catalog / client helpers."""

from __future__ import annotations

import pytest

from core.llm.catalog import (
    DEFAULT_MODEL_ID,
    LEGACY_MODEL_IDS,
    get_model_spec,
    list_model_ids,
    model_supports_thinking,
)
from core.llm.client import (
    MissingApiKeyError,
    build_thinking_kwargs,
    clear_client_cache,
    get_client_for_model,
    provider_status,
    resolve_api_key,
    resolve_base_url,
)
from core.llm.providers import get_provider
from ui.prefs import resolve_model_id


def test_catalog_has_default_and_current_vendors():
    ids = list_model_ids()
    assert DEFAULT_MODEL_ID in ids
    assert "deepseek-v4-pro" in ids
    assert "glm-5.2" in ids
    assert "kimi-k3" in ids
    assert "grok-4.5" in ids
    assert "gpt-5.6-sol" in ids
    assert "gemini-3.5-flash" in ids
    assert "claude-sonnet-5" in ids
    assert "claude-opus-4-8" in ids
    assert "claude-fable-5" in ids


def test_legacy_ids_remap():
    assert LEGACY_MODEL_IDS["gpt-4.1"] == "gpt-5.6-terra"
    assert LEGACY_MODEL_IDS["kimi-k2"] == "kimi-k3"
    assert LEGACY_MODEL_IDS["claude-sonnet-4"] == "claude-sonnet-5"
    assert get_model_spec("gpt-4.1").id == "gpt-5.6-terra"
    assert resolve_model_id("gpt-4.1") == "gpt-5.6-terra"
    assert resolve_model_id("k3") == "kimi-k3"


def test_thinking_flags_for_current_models():
    assert model_supports_thinking("deepseek-v4-pro")
    assert model_supports_thinking("gpt-5.6-sol")
    assert model_supports_thinking("glm-5.2")
    assert model_supports_thinking("kimi-k3")
    assert model_supports_thinking("grok-4.5")
    assert not model_supports_thinking("gemini-3.1-flash-lite")


def test_resolve_model_aliases():
    assert resolve_model_id("pro") == "deepseek-v4-pro"
    assert resolve_model_id("flash") == "deepseek-v4-flash"
    assert resolve_model_id("sol") == "gpt-5.6-sol"
    assert resolve_model_id("gpt-5.6-terra") == "gpt-5.6-terra"
    assert resolve_model_id("nope") is None


def test_thinking_kwargs_deepseek_and_none():
    assert build_thinking_kwargs("none", "high") == {}
    off = build_thinking_kwargs("deepseek", "off")
    assert off["extra_body"]["thinking"]["type"] == "disabled"
    on = build_thinking_kwargs("deepseek", "high")
    assert on["reasoning_effort"] == "high"
    assert on["extra_body"]["thinking"]["type"] == "enabled"
    oai = build_thinking_kwargs("openai", "max")
    assert oai == {"reasoning_effort": "max"}


def test_base_url_override(monkeypatch: pytest.MonkeyPatch):
    p = get_provider("openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1/")
    assert resolve_base_url(p) == "https://example.com/v1"
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("FIAGENT_OPENAI_BASE_URL", "https://fiagent.example/v1")
    assert resolve_base_url(p) == "https://fiagent.example/v1"


def test_missing_key_raises(monkeypatch: pytest.MonkeyPatch):
    clear_client_cache()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(MissingApiKeyError):
        get_client_for_model("gpt-5.6-sol")


def test_get_client_with_key(monkeypatch: pytest.MonkeyPatch):
    clear_client_cache()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek")
    client = get_client_for_model("deepseek-v4-flash")
    assert client is not None
    assert resolve_api_key("deepseek") == "sk-test-deepseek"
    spec = get_model_spec("deepseek-v4-flash")
    assert spec.api_model == "deepseek-v4-flash"


def test_provider_status_claude_needs_gateway(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("FIAGENT_ANTHROPIC_BASE_URL", raising=False)
    st = provider_status("anthropic")
    assert st["ready"] is False
    assert "ANTHROPIC_API_KEY" in st["note"]

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    st2 = provider_status("anthropic")
    assert st2["has_key"] is True
    assert st2["ready"] is False
    assert "ANTHROPIC_BASE_URL" in st2["note"]

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example/v1")
    st3 = provider_status("anthropic")
    assert st3["ready"] is True


def test_provider_status_openai_key_only(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert provider_status("openai")["ready"] is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert provider_status("openai")["ready"] is True
    assert provider_status("openai")["note"] == "Key ✓"


def test_moonshot_default_base_is_global():
    assert get_provider("moonshot").default_base_url.startswith("https://api.moonshot.ai")
