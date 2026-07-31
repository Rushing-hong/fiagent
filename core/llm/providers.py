"""LLM provider definitions (OpenAI-compatible endpoints)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    env_key: str
    default_base_url: str
    # Optional overrides: PROVIDER_BASE_URL then FIAGENT_PROVIDER_BASE_URL
    base_url_env: str


PROVIDERS: dict[str, Provider] = {
    "deepseek": Provider(
        id="deepseek",
        label="DeepSeek",
        env_key="DEEPSEEK_API_KEY",
        default_base_url="https://api.deepseek.com",
        base_url_env="DEEPSEEK_BASE_URL",
    ),
    "zhipu": Provider(
        id="zhipu",
        label="GLM",
        env_key="ZHIPU_API_KEY",
        default_base_url="https://open.bigmodel.cn/api/paas/v4",
        base_url_env="ZHIPU_BASE_URL",
    ),
    "moonshot": Provider(
        id="moonshot",
        label="Kimi",
        env_key="MOONSHOT_API_KEY",
        # Official Kimi platform (OpenAI-compatible). CN mirror: https://api.moonshot.cn/v1
        default_base_url="https://api.moonshot.ai/v1",
        base_url_env="MOONSHOT_BASE_URL",
    ),
    "xai": Provider(
        id="xai",
        label="Grok",
        env_key="XAI_API_KEY",
        default_base_url="https://api.x.ai/v1",
        base_url_env="XAI_BASE_URL",
    ),
    "openai": Provider(
        id="openai",
        label="OpenAI",
        env_key="OPENAI_API_KEY",
        default_base_url="https://api.openai.com/v1",
        base_url_env="OPENAI_BASE_URL",
    ),
    "gemini": Provider(
        id="gemini",
        label="Gemini",
        env_key="GEMINI_API_KEY",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        base_url_env="GEMINI_BASE_URL",
    ),
    "anthropic": Provider(
        id="anthropic",
        label="Claude",
        env_key="ANTHROPIC_API_KEY",
        # Official Messages API is not OpenAI-shaped; set ANTHROPIC_BASE_URL
        # to an OpenAI-compatible gateway / Anthropic compatible endpoint.
        default_base_url="https://api.anthropic.com/v1",
        base_url_env="ANTHROPIC_BASE_URL",
    ),
}


def get_provider(provider_id: str) -> Provider:
    try:
        return PROVIDERS[provider_id]
    except KeyError as exc:
        raise KeyError(f"未知厂商: {provider_id}") from exc
