"""Agent 核心运行时：ReAct 循环、流式 LLM、上下文、暂停控制。"""

from __future__ import annotations

from typing import Any

__all__ = ["AgentContext", "run_agent_turn", "TurnAborted", "turn_control"]


def __getattr__(name: str) -> Any:
    # Lazy exports avoid circular imports (ui.prefs ↔ core.context via core.__init__).
    if name == "AgentContext":
        from core.context import AgentContext

        return AgentContext
    if name == "run_agent_turn":
        from core.loop import run_agent_turn

        return run_agent_turn
    if name == "TurnAborted":
        from core.turn_control import TurnAborted

        return TurnAborted
    if name == "turn_control":
        from core.turn_control import turn_control

        return turn_control
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
