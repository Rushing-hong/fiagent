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

_NON_EVIDENCE_TOOLS = frozenset({
    "get_current_time",
    "grep",
    "list_run_evidence",
    "load_skill",
    "read",
    "read_agent_report",
})


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


def run_with_context(
    ctx: ResearchRunContext | None,
    research_active: bool,
    fn,
    /,
    *args,
    **kwargs,
):
    """Run work in a child thread with the caller's research context bound.

    ``threading.local`` values are not inherited by ``ThreadPoolExecutor``
    workers.  Tool calls need the context for PIT gates, audit logging and UI
    suppression, so bind it explicitly and always release the worker's SQLite
    connection before the thread is returned to the pool.
    """
    previous_ctx = get_run_context()
    previous_active = is_research_run_active()
    set_run_context(ctx)
    set_research_run_active(research_active)
    try:
        return fn(*args, **kwargs)
    finally:
        if ctx is not None:
            try:
                ctx.store.close_thread()
            except Exception:
                pass
        set_run_context(previous_ctx)
        set_research_run_active(previous_active)


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
) -> str | None:
    rc = get_run_context()
    if rc is None:
        return None
    evidence_id: str | None = None
    try:
        tool_call_id = rc.store.log_tool_call(
            rc.run_id,
            rc.agent_name,
            tool_name,
            arguments,
            result[:4000],
            success=success,
        )
        if success and rc.agent_name and tool_name not in _NON_EVIDENCE_TOOLS:
            evidence_id = _register_tool_evidence(
                rc,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=arguments,
                result=result,
            )
    except Exception:
        pass
    if rc.agent_name:
        try:
            from ui.web.collaboration_progress import emit_collaboration_progress
            emit_collaboration_progress({
                "phase": "agent_tool",
                "run_id": rc.run_id,
                "agent": rc.agent_name,
                "tool": tool_name,
                "args_preview": (arguments or "")[:160],
                "success": success,
            })
        except Exception:
            pass
    return evidence_id


def _json_dict(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _first_symbol(arguments: dict[str, Any], result: dict[str, Any]) -> str:
    for obj in (arguments, result.get("data"), result):
        if not isinstance(obj, dict):
            continue
        for key in ("symbol", "code"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().upper()
        codes = obj.get("codes")
        if isinstance(codes, list) and codes and isinstance(codes[0], str):
            return codes[0].strip().upper()
    return ""


def _register_tool_evidence(
    rc: ResearchRunContext,
    *,
    tool_call_id: str,
    tool_name: str,
    arguments: str,
    result: str,
) -> str | None:
    """Create a canonical evidence record for a successful research tool call."""
    result_obj = _json_dict(result)
    argument_obj = _json_dict(arguments)
    meta = result_obj.get("_meta")
    if not isinstance(meta, dict):
        meta = {}

    source = str(result_obj.get("source") or meta.get("source") or tool_name)
    as_of = str(
        result_obj.get("as_of_time")
        or result_obj.get("as_of")
        or meta.get("as_of_time")
        or meta.get("as_of")
        or ""
    )
    quality = str(result_obj.get("quality") or meta.get("quality") or "unknown")
    pit_safe = result_obj.get("pit_safe") is True or meta.get("pit_safe") is True
    record = rc.store.add_evidence(
        rc.run_id,
        symbol=_first_symbol(argument_obj, result_obj),
        source=source,
        as_of_time=as_of,
        pit_safe=pit_safe,
        quality=quality,
        extra={
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
        },
    )
    return record.evidence_id
