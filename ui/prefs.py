"""UI 偏好持久化（借鉴 OpenCode KV：thinking_mode 等）。"""

from __future__ import annotations

import json
from typing import Literal

from paths import DATA_DIR

PREFS_PATH = DATA_DIR / "ui_prefs.json"

ThinkingMode = Literal["show", "hide"]
UIMode = Literal["tui", "plain"]
ModelId = Literal["deepseek-v4-pro", "deepseek-v4-flash"]
ReasoningEffort = Literal["high", "max", "off"]

AVAILABLE_MODELS: tuple[ModelId, ...] = ("deepseek-v4-pro", "deepseek-v4-flash")
AVAILABLE_EFFORTS: tuple[ReasoningEffort, ...] = ("high", "max", "off")

MODEL_LABELS: dict[str, str] = {
    "deepseek-v4-pro": "Pro",
    "deepseek-v4-flash": "Flash",
}
EFFORT_LABELS: dict[str, str] = {
    "high": "High",
    "max": "Max",
    "off": "关闭思考",
}


def load_prefs() -> dict:
    if not PREFS_PATH.exists():
        return {}
    try:
        return json.loads(PREFS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_prefs(prefs: dict) -> None:
    PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREFS_PATH.write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")


def get_thinking_mode() -> ThinkingMode:
    mode = load_prefs().get("thinking_mode", "hide")
    return mode if mode in ("show", "hide") else "hide"


def set_thinking_mode(mode: ThinkingMode) -> ThinkingMode:
    prefs = load_prefs()
    prefs["thinking_mode"] = mode
    save_prefs(prefs)
    return mode


def toggle_thinking_mode() -> ThinkingMode:
    nxt = "show" if get_thinking_mode() == "hide" else "hide"
    return set_thinking_mode(nxt)


def get_ui_mode() -> UIMode:
    mode = load_prefs().get("ui_mode", "tui")
    return mode if mode in ("tui", "plain") else "tui"


def set_ui_mode(mode: UIMode) -> UIMode:
    prefs = load_prefs()
    prefs["ui_mode"] = mode
    save_prefs(prefs)
    return mode


def ui_mode_label(mode: UIMode | None = None) -> str:
    m = mode or get_ui_mode()
    return "TUI" if m == "tui" else "纯终端"


def get_model() -> ModelId:
    model = load_prefs().get("model", "deepseek-v4-pro")
    return model if model in AVAILABLE_MODELS else "deepseek-v4-pro"


def set_model(model: str) -> ModelId:
    if model not in AVAILABLE_MODELS:
        raise ValueError(f"未知模型: {model}")
    prefs = load_prefs()
    prefs["model"] = model
    save_prefs(prefs)
    return model  # type: ignore[return-value]


def model_label(model: str | None = None) -> str:
    m = model or get_model()
    return MODEL_LABELS.get(m, m)


def get_reasoning_effort() -> ReasoningEffort:
    effort = load_prefs().get("reasoning_effort", "high")
    return effort if effort in AVAILABLE_EFFORTS else "high"


def set_reasoning_effort(effort: str) -> ReasoningEffort:
    if effort not in AVAILABLE_EFFORTS:
        raise ValueError(f"未知思考强度: {effort}")
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
    "run_python",
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
