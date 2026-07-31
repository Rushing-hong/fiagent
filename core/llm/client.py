"""OpenAI-compatible client factory with per-provider cache."""

from __future__ import annotations

import os
import threading
from typing import Any

import httpx
from openai import OpenAI

from core.llm.catalog import get_model_spec
from core.llm.providers import PROVIDERS, Provider, get_provider


class MissingApiKeyError(RuntimeError):
    def __init__(self, provider: Provider):
        self.provider = provider
        super().__init__(
            f"缺少 {provider.label} API Key：请设置环境变量 {provider.env_key}"
            f"（或写入 .env）"
        )


_lock = threading.Lock()
_cache: dict[tuple[str, str, str], OpenAI] = {}


def resolve_base_url(provider: Provider | str) -> str:
    p = provider if isinstance(provider, Provider) else get_provider(provider)
    for key in (
        p.base_url_env,
        f"FIAGENT_{p.id.upper()}_BASE_URL",
    ):
        raw = os.getenv(key, "").strip()
        if raw:
            return raw.rstrip("/")
    return p.default_base_url.rstrip("/")


def resolve_api_key(provider: Provider | str) -> str:
    p = provider if isinstance(provider, Provider) else get_provider(provider)
    return os.getenv(p.env_key, "").strip()


def clear_client_cache() -> None:
    with _lock:
        _cache.clear()


def llm_http_timeout() -> httpx.Timeout:
    """Streaming + thinking can idle a long time before the first chunk."""
    connect = float(os.getenv("FIAGENT_LLM_CONNECT_TIMEOUT", "30"))
    read = float(os.getenv("FIAGENT_LLM_READ_TIMEOUT", "600"))
    write = float(os.getenv("FIAGENT_LLM_WRITE_TIMEOUT", "120"))
    return httpx.Timeout(connect=connect, read=read, write=write, pool=connect)


def get_client_for_provider(provider_id: str) -> OpenAI:
    provider = get_provider(provider_id)
    api_key = resolve_api_key(provider)
    if not api_key:
        raise MissingApiKeyError(provider)
    base_url = resolve_base_url(provider)
    cache_key = (provider.id, base_url, api_key)
    with _lock:
        hit = _cache.get(cache_key)
        if hit is not None:
            return hit
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=llm_http_timeout(),
            max_retries=0,
        )
        _cache[cache_key] = client
        return client


def get_client_for_model(model_id: str) -> OpenAI:
    spec = get_model_spec(model_id)
    return get_client_for_provider(spec.provider)


def ensure_key_for_model(model_id: str) -> Provider:
    """Return provider if key present; raise MissingApiKeyError otherwise."""
    spec = get_model_spec(model_id)
    provider = get_provider(spec.provider)
    if not resolve_api_key(provider):
        raise MissingApiKeyError(provider)
    return provider


def first_configured_provider() -> Provider | None:
    """Prefer DeepSeek, then any provider that has a key set."""
    preferred = ("deepseek", "openai", "zhipu", "moonshot", "xai", "gemini", "anthropic")
    for pid in preferred:
        p = PROVIDERS[pid]
        if resolve_api_key(p):
            return p
    return None


def _has_custom_base_url(provider: Provider) -> bool:
    for key in (provider.base_url_env, f"FIAGENT_{provider.id.upper()}_BASE_URL"):
        if os.getenv(key, "").strip():
            return True
    return False


def provider_status(provider: Provider | str) -> dict[str, Any]:
    """Honest readiness for UI: key presence + Claude gateway requirement.

    Claude uses the OpenAI client; official Anthropic Messages API is not
    compatible unless ANTHROPIC_BASE_URL points at an OpenAI-shaped gateway.
    """
    p = provider if isinstance(provider, Provider) else get_provider(provider)
    has_key = bool(resolve_api_key(p))
    if p.id == "anthropic":
        has_gateway = _has_custom_base_url(p)
        if not has_key:
            return {
                "ready": False,
                "has_key": False,
                "has_gateway": False,
                "note": f"未设置 {p.env_key}",
            }
        if not has_gateway:
            return {
                "ready": False,
                "has_key": True,
                "has_gateway": False,
                "note": "需 ANTHROPIC_BASE_URL（OpenAI 兼容网关）",
            }
        return {
            "ready": True,
            "has_key": True,
            "has_gateway": True,
            "note": "Key ✓ · 网关 ✓",
        }
    if not has_key:
        return {
            "ready": False,
            "has_key": False,
            "has_gateway": True,
            "note": f"未设置 {p.env_key}",
        }
    return {
        "ready": True,
        "has_key": True,
        "has_gateway": True,
        "note": "Key ✓",
    }


def build_thinking_kwargs(
    thinking: str,
    effort: str,
) -> dict[str, Any]:
    """Request kwargs for thinking / reasoning, keyed by catalog thinking style."""
    if thinking == "none":
        return {}
    if thinking == "deepseek":
        if effort == "off":
            return {"extra_body": {"thinking": {"type": "disabled"}}}
        return {
            "reasoning_effort": effort,
            "extra_body": {"thinking": {"type": "enabled"}},
        }
    if thinking == "openai":
        if effort == "off":
            return {}
        # o-series style; unsupported models ignore or error — gated by catalog
        return {"reasoning_effort": effort}
    return {}
