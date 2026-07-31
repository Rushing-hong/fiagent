"""Model catalog: display id → provider + API model name + thinking style.

IDs follow current vendor docs (as of 2026-07). Prefer official API slugs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ThinkingStyle = Literal["deepseek", "openai", "none"]

DEFAULT_MODEL_ID = "deepseek-v4-pro"


@dataclass(frozen=True)
class ModelSpec:
    id: str
    provider: str
    api_model: str
    label: str
    group: str
    thinking: ThinkingStyle

    @property
    def supports_thinking(self) -> bool:
        return self.thinking != "none"


MODELS: tuple[ModelSpec, ...] = (
    # DeepSeek — https://api-docs.deepseek.com/
    ModelSpec(
        id="deepseek-v4-pro",
        provider="deepseek",
        api_model="deepseek-v4-pro",
        label="DeepSeek V4 Pro",
        group="DeepSeek",
        thinking="deepseek",
    ),
    ModelSpec(
        id="deepseek-v4-flash",
        provider="deepseek",
        api_model="deepseek-v4-flash",
        label="DeepSeek V4 Flash",
        group="DeepSeek",
        thinking="deepseek",
    ),
    # GLM / Zhipu — https://docs.bigmodel.cn/
    ModelSpec(
        id="glm-5.2",
        provider="zhipu",
        api_model="glm-5.2",
        label="GLM-5.2",
        group="GLM",
        thinking="deepseek",
    ),
    ModelSpec(
        id="glm-5",
        provider="zhipu",
        api_model="glm-5",
        label="GLM-5",
        group="GLM",
        thinking="deepseek",
    ),
    ModelSpec(
        id="glm-4.6",
        provider="zhipu",
        api_model="glm-4.6",
        label="GLM-4.6",
        group="GLM",
        thinking="deepseek",
    ),
    # Kimi / Moonshot — https://platform.kimi.ai/docs/models
    ModelSpec(
        id="kimi-k3",
        provider="moonshot",
        api_model="kimi-k3",
        label="Kimi K3",
        group="Kimi",
        thinking="openai",
    ),
    ModelSpec(
        id="kimi-k2.7-code",
        provider="moonshot",
        api_model="kimi-k2.7-code",
        label="Kimi K2.7 Code",
        group="Kimi",
        thinking="openai",
    ),
    ModelSpec(
        id="kimi-k2.7-code-highspeed",
        provider="moonshot",
        api_model="kimi-k2.7-code-highspeed",
        label="Kimi K2.7 Code Fast",
        group="Kimi",
        thinking="openai",
    ),
    ModelSpec(
        id="kimi-k2.6",
        provider="moonshot",
        api_model="kimi-k2.6",
        label="Kimi K2.6",
        group="Kimi",
        thinking="openai",
    ),
    # Grok / xAI — https://docs.x.ai/developers/models
    ModelSpec(
        id="grok-4.5",
        provider="xai",
        api_model="grok-4.5",
        label="Grok 4.5",
        group="Grok",
        thinking="openai",
    ),
    ModelSpec(
        id="grok-4.3",
        provider="xai",
        api_model="grok-4.3",
        label="Grok 4.3",
        group="Grok",
        thinking="openai",
    ),
    ModelSpec(
        id="grok-build-0.1",
        provider="xai",
        api_model="grok-build-0.1",
        label="Grok Build 0.1",
        group="Grok",
        thinking="openai",
    ),
    # OpenAI — https://developers.openai.com/api/docs/models
    ModelSpec(
        id="gpt-5.6-sol",
        provider="openai",
        api_model="gpt-5.6-sol",
        label="GPT-5.6 Sol",
        group="OpenAI",
        thinking="openai",
    ),
    ModelSpec(
        id="gpt-5.6-terra",
        provider="openai",
        api_model="gpt-5.6-terra",
        label="GPT-5.6 Terra",
        group="OpenAI",
        thinking="openai",
    ),
    ModelSpec(
        id="gpt-5.6-luna",
        provider="openai",
        api_model="gpt-5.6-luna",
        label="GPT-5.6 Luna",
        group="OpenAI",
        thinking="openai",
    ),
    ModelSpec(
        id="gpt-5.5",
        provider="openai",
        api_model="gpt-5.5",
        label="GPT-5.5",
        group="OpenAI",
        thinking="openai",
    ),
    ModelSpec(
        id="gpt-5.4-mini",
        provider="openai",
        api_model="gpt-5.4-mini",
        label="GPT-5.4 Mini",
        group="OpenAI",
        thinking="openai",
    ),
    # Gemini — https://ai.google.dev/gemini-api/docs/models
    ModelSpec(
        id="gemini-3.5-flash",
        provider="gemini",
        api_model="gemini-3.5-flash",
        label="Gemini 3.5 Flash",
        group="Gemini",
        thinking="openai",
    ),
    ModelSpec(
        id="gemini-3.1-pro-preview",
        provider="gemini",
        api_model="gemini-3.1-pro-preview",
        label="Gemini 3.1 Pro",
        group="Gemini",
        thinking="openai",
    ),
    ModelSpec(
        id="gemini-3.1-flash-lite",
        provider="gemini",
        api_model="gemini-3.1-flash-lite",
        label="Gemini 3.1 Flash-Lite",
        group="Gemini",
        thinking="none",
    ),
    # Claude — https://platform.claude.com/docs/en/about-claude/models/overview
    # Needs OpenAI-compatible ANTHROPIC_BASE_URL / gateway
    ModelSpec(
        id="claude-fable-5",
        provider="anthropic",
        api_model="claude-fable-5",
        label="Claude Fable 5",
        group="Claude",
        thinking="openai",
    ),
    ModelSpec(
        id="claude-opus-4-8",
        provider="anthropic",
        api_model="claude-opus-4-8",
        label="Claude Opus 4.8",
        group="Claude",
        thinking="openai",
    ),
    ModelSpec(
        id="claude-sonnet-5",
        provider="anthropic",
        api_model="claude-sonnet-5",
        label="Claude Sonnet 5",
        group="Claude",
        thinking="openai",
    ),
    ModelSpec(
        id="claude-haiku-4-5",
        provider="anthropic",
        api_model="claude-haiku-4-5-20251001",
        label="Claude Haiku 4.5",
        group="Claude",
        thinking="openai",
    ),
)

# Old catalog / prefs IDs → current catalog id
LEGACY_MODEL_IDS: dict[str, str] = {
    "glm-4.5": "glm-5.2",
    "glm-4.5-air": "glm-4.6",
    "glm-4-flash": "glm-4.6",
    "kimi-k2": "kimi-k3",
    "moonshot-v1-auto": "kimi-k3",
    "grok-3": "grok-4.5",
    "grok-3-mini": "grok-4.3",
    "gpt-4.1": "gpt-5.6-terra",
    "gpt-4.1-mini": "gpt-5.6-luna",
    "gpt-4o": "gpt-5.6-terra",
    "o4-mini": "gpt-5.6-luna",
    "gpt-5.6": "gpt-5.6-sol",
    "gemini-2.5-pro": "gemini-3.1-pro-preview",
    "gemini-2.5-flash": "gemini-3.5-flash",
    "claude-sonnet-4": "claude-sonnet-5",
    "claude-opus-4": "claude-opus-4-8",
    "claude-sonnet-4-20250514": "claude-sonnet-5",
    "claude-opus-4-20250514": "claude-opus-4-8",
}

_BY_ID: dict[str, ModelSpec] = {m.id: m for m in MODELS}


def list_models() -> tuple[ModelSpec, ...]:
    return MODELS


def list_model_ids() -> tuple[str, ...]:
    return tuple(m.id for m in MODELS)


def get_model_spec(model_id: str) -> ModelSpec:
    mid = LEGACY_MODEL_IDS.get(model_id, model_id)
    try:
        return _BY_ID[mid]
    except KeyError as exc:
        raise KeyError(f"未知模型: {model_id}") from exc


def model_supports_thinking(model_id: str) -> bool:
    mid = LEGACY_MODEL_IDS.get(model_id, model_id)
    spec = _BY_ID.get(mid)
    if spec is None:
        return False
    return spec.supports_thinking
