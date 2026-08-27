"""Live collaboration-task events for the Web SSE stream."""

from __future__ import annotations

from typing import Any, Callable

_emit: Callable[[dict[str, Any]], None] | None = None


def set_progress_emitter(fn: Callable[[dict[str, Any]], None] | None) -> None:
    global _emit
    _emit = fn


def emit_collaboration_progress(payload: dict[str, Any]) -> None:
    if _emit is None:
        return
    try:
        _emit({"type": "collaboration", **payload})
    except Exception:
        pass
