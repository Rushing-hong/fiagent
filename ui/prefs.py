"""UI 偏好持久化（借鉴 OpenCode KV：thinking_mode 等）。"""

from __future__ import annotations

import json
import os
import threading
from typing import Literal

from core.llm.catalog import (
    DEFAULT_MODEL_ID,
    get_model_spec,
    list_model_ids,
    list_models,
    model_supports_thinking,
)
from paths import DATA_DIR

PREFS_PATH = DATA_DIR / "ui_prefs.json"
_prefs_lock = threading.RLock()
_prefs_cache_key: tuple[str, int, int] | tuple[str, None, None] | None = None
_prefs_cache: dict | None = None

ThinkingMode = Literal["show", "hide"]
UIMode = Literal["tui", "plain", "web"]
ModelId = str
ReasoningEffort = Literal["high", "max", "off"]

AVAILABLE_MODELS: tuple[str, ...] = list_model_ids()
AVAILABLE_EFFORTS: tuple[ReasoningEffort, ...] = ("high", "max", "off")

MODEL_LABELS: dict[str, str] = {m.id: m.label for m in list_models()}
EFFORT_LABELS: dict[str, str] = {
    "high": "High",
    "max": "Max",
    "off": "关闭思考",
}

# Short aliases for /model (+ legacy ids remapped in catalog.LEGACY_MODEL_IDS)
MODEL_ALIASES: dict[str, str] = {
    "pro": "deepseek-v4-pro",
    "flash": "deepseek-v4-flash",
    "ds-pro": "deepseek-v4-pro",
    "ds-flash": "deepseek-v4-flash",
    "sol": "gpt-5.6-sol",
    "terra": "gpt-5.6-terra",
    "luna": "gpt-5.6-luna",
    "k3": "kimi-k3",
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-5",
    "fable": "claude-fable-5",
}


def load_prefs() -> dict:
    """Load preferences with mtime invalidation and defensive copies."""
    global _prefs_cache_key, _prefs_cache
    path_key = str(PREFS_PATH.resolve(strict=False))
    with _prefs_lock:
        try:
            stat = PREFS_PATH.stat()
            cache_key: tuple[str, int, int] | tuple[str, None, None] = (
                path_key,
                stat.st_mtime_ns,
                stat.st_size,
            )
        except OSError:
            cache_key = (path_key, None, None)
        if _prefs_cache_key == cache_key and _prefs_cache is not None:
            return dict(_prefs_cache)
        try:
            raw = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
            prefs = raw if isinstance(raw, dict) else {}
        except (json.JSONDecodeError, OSError):
            prefs = {}
        _prefs_cache_key = cache_key
        _prefs_cache = dict(prefs)
        return dict(prefs)


def save_prefs(prefs: dict) -> None:
    """Atomically persist preferences so readers never observe partial JSON."""
    global _prefs_cache_key, _prefs_cache
    with _prefs_lock:
        PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = PREFS_PATH.with_name(
            f".{PREFS_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temp_path.write_text(
                json.dumps(prefs, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp_path, PREFS_PATH)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        stat = PREFS_PATH.stat()
        _prefs_cache_key = (
            str(PREFS_PATH.resolve(strict=False)),
            stat.st_mtime_ns,
            stat.st_size,
        )
        _prefs_cache = dict(prefs)


def get_thinking_mode() -> ThinkingMode:
    mode = load_prefs().get("thinking_mode", "hide")
    return mode if mode in ("show", "hide") else "hide"


def set_thinking_mode(mode: ThinkingMode) -> ThinkingMode:
    with _prefs_lock:
        prefs = load_prefs()
        prefs["thinking_mode"] = mode
        save_prefs(prefs)
    return mode


def toggle_thinking_mode() -> ThinkingMode:
    nxt = "show" if get_thinking_mode() == "hide" else "hide"
    return set_thinking_mode(nxt)


def get_ui_mode() -> UIMode:
    mode = load_prefs().get("ui_mode", "tui")
    return mode if mode in ("tui", "plain", "web") else "tui"


def set_ui_mode(mode: UIMode) -> UIMode:
    if mode not in ("tui", "plain", "web"):
        raise ValueError(f"未知界面模式: {mode}")
    with _prefs_lock:
        prefs = load_prefs()
        prefs["ui_mode"] = mode
        save_prefs(prefs)
    return mode


def ui_mode_label(mode: UIMode | str | None = None) -> str:
    m = mode or get_ui_mode()
    return {"tui": "TUI", "plain": "纯终端", "web": "网页"}.get(m, str(m))


def resolve_model_id(raw: str) -> str | None:
    """Resolve alias, legacy id, or full model id; None if unknown."""
    from core.llm.catalog import LEGACY_MODEL_IDS

    key = (raw or "").strip()
    if not key:
        return None
    low = key.lower()
    if low in MODEL_ALIASES:
        return MODEL_ALIASES[low]
    if key in LEGACY_MODEL_IDS:
        return LEGACY_MODEL_IDS[key]
    if low in LEGACY_MODEL_IDS:
        return LEGACY_MODEL_IDS[low]
    if key in AVAILABLE_MODELS:
        return key
    if low in AVAILABLE_MODELS:
        return low
    return None


def get_model() -> ModelId:
    from core.llm.catalog import LEGACY_MODEL_IDS

    model = load_prefs().get("model", DEFAULT_MODEL_ID)
    if model in AVAILABLE_MODELS:
        return model
    if model in LEGACY_MODEL_IDS:
        return LEGACY_MODEL_IDS[model]
    return DEFAULT_MODEL_ID


def set_model(model: str) -> ModelId:
    resolved = resolve_model_id(model)
    if resolved is None:
        raise ValueError(f"未知模型: {model}")
    with _prefs_lock:
        prefs = load_prefs()
        prefs["model"] = resolved
        save_prefs(prefs)
    return resolved


def model_label(model: str | None = None) -> str:
    m = model or get_model()
    return MODEL_LABELS.get(m, m)


def model_group(model: str | None = None) -> str:
    m = model or get_model()
    try:
        return get_model_spec(m).group
    except KeyError:
        return ""


def current_model_supports_thinking() -> bool:
    return model_supports_thinking(get_model())


def get_reasoning_effort() -> ReasoningEffort:
    effort = load_prefs().get("reasoning_effort", "high")
    return effort if effort in AVAILABLE_EFFORTS else "high"


def set_reasoning_effort(effort: str) -> ReasoningEffort:
    if effort not in AVAILABLE_EFFORTS:
        raise ValueError(f"未知思考强度: {effort}")
    with _prefs_lock:
        prefs = load_prefs()
        prefs["reasoning_effort"] = effort
        save_prefs(prefs)
    return effort  # type: ignore[return-value]


def effort_label(effort: str | None = None) -> str:
    e = effort or get_reasoning_effort()
    return EFFORT_LABELS.get(e, e)


def get_last_session_id() -> str | None:
    value = load_prefs().get("last_session_id")
    return value if isinstance(value, str) and value.strip() else None


def set_last_session_id(session_id: str | None) -> None:
    with _prefs_lock:
        prefs = load_prefs()
        if session_id:
            prefs["last_session_id"] = session_id
        else:
            prefs.pop("last_session_id", None)
        save_prefs(prefs)


# --- tools / skills enable toggles（opt-out）---

# 元工具不可关闭，否则无法管理 skill
ALWAYS_ON_TOOLS: frozenset[str] = frozenset({
    "load_skill",
    "save_skill",
    "patch_skill",
    "delete_skill",
    "read",
    "grep",
    "write",
    "edit",
    "get_current_time",
})


def _as_str_set(raw: object) -> set[str]:
    if not isinstance(raw, list):
        return set()
    return {str(x).strip() for x in raw if str(x).strip()}


def get_disabled_tools() -> set[str]:
    return _as_str_set(load_prefs().get("disabled_tools"))


def get_disabled_skills() -> set[str]:
    return _as_str_set(load_prefs().get("disabled_skills"))


def is_tool_enabled(name: str) -> bool:
    if name in ALWAYS_ON_TOOLS:
        return True
    return name not in get_disabled_tools()


def is_skill_enabled(name: str) -> bool:
    return name not in get_disabled_skills()


def set_tool_enabled(name: str, enabled: bool) -> bool:
    """启用/禁用工具；返回最终是否启用。元工具始终启用。"""
    if name in ALWAYS_ON_TOOLS:
        return True
    with _prefs_lock:
        prefs = load_prefs()
        disabled = _as_str_set(prefs.get("disabled_tools"))
        if enabled:
            disabled.discard(name)
        else:
            disabled.add(name)
        prefs["disabled_tools"] = sorted(disabled)
        save_prefs(prefs)
    return enabled


def set_skill_enabled(name: str, enabled: bool) -> bool:
    with _prefs_lock:
        prefs = load_prefs()
        disabled = _as_str_set(prefs.get("disabled_skills"))
        if enabled:
            disabled.discard(name)
        else:
            disabled.add(name)
        prefs["disabled_skills"] = sorted(disabled)
        save_prefs(prefs)
    return enabled


def toggle_tool(name: str) -> bool:
    """翻转工具开关，返回翻转后是否启用。"""
    return set_tool_enabled(name, not is_tool_enabled(name))


def toggle_skill(name: str) -> bool:
    return set_skill_enabled(name, not is_skill_enabled(name))


# --- MCP tool toggles（opt-out；server 开关写在 mcps/mcp.json）---


def get_disabled_mcp_tools() -> set[str]:
    return _as_str_set(load_prefs().get("disabled_mcp_tools"))


def is_mcp_tool_enabled(name: str) -> bool:
    return name not in get_disabled_mcp_tools()


def set_mcp_tool_enabled(name: str, enabled: bool) -> bool:
    with _prefs_lock:
        prefs = load_prefs()
        disabled = _as_str_set(prefs.get("disabled_mcp_tools"))
        if enabled:
            disabled.discard(name)
        else:
            disabled.add(name)
        prefs["disabled_mcp_tools"] = sorted(disabled)
        save_prefs(prefs)
    return enabled


def toggle_mcp_tool(name: str) -> bool:
    return set_mcp_tool_enabled(name, not is_mcp_tool_enabled(name))
