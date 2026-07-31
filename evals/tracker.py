"""Per-turn eval counters (tool calls, PIT hints) when FIAGENT_EVAL=1."""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field

_PIT_UNSAFE_RE = re.compile(r"pit_safe[\"']?\s*:\s*false", re.I)


def eval_tracking_enabled() -> bool:
    return os.getenv("FIAGENT_EVAL", "").strip().lower() in ("1", "true", "yes")


@dataclass
class TurnEvalStats:
    tool_calls: int = 0
    tool_names: list[str] = field(default_factory=list)
    pit_unsafe_hits: int = 0
    tool_errors: int = 0

    def record_tool(self, name: str, result: str) -> None:
        self.tool_calls += 1
        self.tool_names.append(name)
        text = result or ""
        if _PIT_UNSAFE_RE.search(text):
            self.pit_unsafe_hits += 1
        if text.startswith("工具执行异常") or '"status": "error"' in text[:200]:
            self.tool_errors += 1

    def unique_tools(self) -> int:
        return len(set(self.tool_names))

    def to_extra(self) -> dict:
        return {
            "tool_calls": self.tool_calls,
            "unique_tools": self.unique_tools(),
            "pit_unsafe_hits": self.pit_unsafe_hits,
            "tool_errors": self.tool_errors,
            "tool_sequence": self.tool_names[:40],
        }


_lock = threading.Lock()
_current: TurnEvalStats | None = None


def begin_turn_eval() -> TurnEvalStats | None:
    global _current
    if not eval_tracking_enabled():
        return None
    with _lock:
        _current = TurnEvalStats()
        return _current


def get_turn_eval() -> TurnEvalStats | None:
    with _lock:
        return _current


def end_turn_eval() -> TurnEvalStats | None:
    global _current
    with _lock:
        stats = _current
        _current = None
        return stats
