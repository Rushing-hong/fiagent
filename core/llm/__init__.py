"""Multi-provider LLM catalog and OpenAI-compatible clients."""

from core.llm.catalog import (
    DEFAULT_MODEL_ID,
    MODELS,
    ModelSpec,
    get_model_spec,
    list_model_ids,
    list_models,
    model_supports_thinking,
)
from core.llm.client import (
    MissingApiKeyError,
    clear_client_cache,
    get_client_for_model,
    resolve_api_key,
    resolve_base_url,
)
from core.llm.providers import PROVIDERS, Provider, get_provider

__all__ = [
    "DEFAULT_MODEL_ID",
    "MODELS",
    "ModelSpec",
    "MissingApiKeyError",
    "PROVIDERS",
    "Provider",
    "clear_client_cache",
    "get_client_for_model",
    "get_model_spec",
    "get_provider",
    "list_model_ids",
    "list_models",
    "model_supports_thinking",
    "resolve_api_key",
    "resolve_base_url",
]
