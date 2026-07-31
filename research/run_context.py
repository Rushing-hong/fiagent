"""Thread-local research run context for PIT gates and tool-call logging."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from research.evidence_store import EvidenceStore

PIT_SENSITIVE_TOOLS = frozenset({
    "run_backtest",
    "factor_analysis",
    "pattern",
    "build_event_signals",
    "blend_black_litterman",
    "analyze_portfolio_risk",
    "simulate_execution",
})

_local = threading.local()


@dataclass
class ResearchRunContext:
    run_id: str
    store: EvidenceStore
    agent_name: str = ""
    pit_safe_for_backtest: bool = False
    evidence_items: list[dict[str, Any]] = field(default_factory=list)


def set_run_context(ctx: ResearchRunContext | None) -> None:
    _local.ctx = ctx


def get_run_context() -> ResearchRunContext | None:
    return getattr(_local, "ctx", None)


def set_research_run_active(active: bool = True) -> None:
    """Mark orchestrator research pipeline active (thread-local)."""
    _local.research_active = bool(active)


def is_research_run_active() -> bool:
    return bool(getattr(_local, "research_active", False))


def suppress_main_chat_ui() -> bool:
    """Sub-agents and pre-CIO orchestrator stages should not flood main chat."""
    rc = get_run_context()
    if rc and rc.agent_name:
        return rc.agent_name != "orchestrator"
    return is_research_run_active()


def pit_gate_block_message(tool_name: str) -> str | None:
    rc = get_run_context()
    if rc is None or tool_name not in PIT_SENSITIVE_TOOLS:
        return None
    if rc.pit_safe_for_backtest:
        return None
    return json.dumps({
        "status": "error",
        "error": "PIT_GATE_BLOCKED",
        "message": (
            f"工具 `{tool_name}` 被 PIT 硬门拦截：本 run 尚无 pit_safe=true 的证据快照。"
            "请先由 Data Guardian 完成取证，或仅做实时/当日分析。"
        ),
        "pit_safe_for_backtest": False,
    }, ensure_ascii=False)


def log_tool_call(
    tool_name: str,
    arguments: str,
    result: str,
    *,
    success: bool = True,
) -> None:
    rc = get_run_context()
    if rc is None:
        return
    try:
        rc.store.log_tool_call(
            rc.run_id,
            rc.agent_name,
            tool_name,
            arguments,
            result[:4000],
            success=success,
        )
    except Exception:
        pass
    if rc.agent_name:
        try:
            from ui.web.agent_progress import emit_agent_progress
            emit_agent_progress({
                "phase": "agent_tool",
                "run_id": rc.run_id,
                "agent": rc.agent_name,
                "tool": tool_name,
                "args_preview": (arguments or "")[:160],
                "success": success,
            })
        except Exception:
            pass
