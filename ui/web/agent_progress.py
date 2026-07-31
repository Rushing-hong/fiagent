"""Live agent-team progress events for Web SSE."""

from __future__ import annotations

from typing import Any, Callable

_emit: Callable[[dict[str, Any]], None] | None = None


def set_progress_emitter(fn: Callable[[dict[str, Any]], None] | None) -> None:
    global _emit
    _emit = fn


def emit_agent_progress(payload: dict[str, Any]) -> None:
    if _emit is None:
        return
    try:
        _emit({"type": "agent_team", **payload})
    except Exception:
        pass
