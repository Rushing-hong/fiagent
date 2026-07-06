"""UI 偏好持久化（借鉴 OpenCode KV：thinking_mode 等）。"""

from __future__ import annotations

import json
from typing import Literal

from paths import DATA_DIR

PREFS_PATH = DATA_DIR / "ui_prefs.json"

ThinkingMode = Literal["show", "hide"]
UIMode = Literal["tui", "plain"]


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
