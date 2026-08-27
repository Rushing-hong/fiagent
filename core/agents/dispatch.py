"""Route user turns to Fast / Research / Committee / Trade Review paths."""

from __future__ import annotations

import os
from typing import Any

from openai import OpenAI

from core.agents.orchestrator import ResearchOrchestrator
from core.agents.router import AgentMode
from core.agents.trade_review import TradeReviewOrchestrator
from core.context import AgentContext
from core.loop import run_agent_turn
from evals.metrics import EvalRecorder, EvalTimer, RunMetrics
from evals.tracker import begin_turn_eval, end_turn_eval, eval_tracking_enabled
from hooks.registry import HookRegistry
from ui import ui


def _parse_mode_prefix(text: str) -> tuple[AgentMode | None, str]:
    q = text.strip()
    lower = q.lower()
    if lower.startswith("/research "):
        return AgentMode.RESEARCH, q[len("/research "):].strip()
    if lower.startswith("/committee "):
        return AgentMode.COMMITTEE, q[len("/committee "):].strip()
    if lower.startswith("/review "):
        return AgentMode.TRADE_REVIEW, q[len("/review "):].strip()
    if lower in ("/research", "/committee", "/review"):
        return None, ""
    return None, q


def _resolve_turn_mode(
    user_input: str,
    mode_override: AgentMode | None = None,
) -> tuple[AgentMode, str]:
    forced, query = _parse_mode_prefix(user_input)
    return forced or mode_override or AgentMode.FAST, query


def _eval_enabled() -> bool:
    return eval_tracking_enabled()


def _variant_label(mode: AgentMode) -> str:
    if mode == AgentMode.RESEARCH:
        import os
        if os.getenv("FIAGENT_RESEARCH_RED_TEAM", "1").strip() in ("0", "false", "no"):
            return "B"
        return "C"
    return {
        "fast": "A",
        "committee": "D",
        "trade_review": "R",
    }.get(mode.value, mode.value)


def dispatch_turn(
    client: OpenAI,
    messages: list[dict[str, Any]],
    ctx: AgentContext,
    hooks: HookRegistry,
    user_input: str,
    *,
    mode_override: AgentMode | None = None,
) -> None:
    """Single entry: Fast / Research / Committee / Trade Review."""
    mode, query = _resolve_turn_mode(user_input, mode_override)
    if query == "":
        ui.warn("请附加问题，例如: /research 深度分析贵州茅台 或 /review uploads/trades.csv")
        return

    # Normal conversation is always the main agent. Multi-agent work is a
    # one-shot delegation selected by the UI (or an explicit slash command),
    # never a sticky environment inferred from the wording of later turns.
    timer = EvalTimer() if _eval_enabled() else None
    recorder = EvalRecorder() if _eval_enabled() else None
    begin_turn_eval()
    success = True
    extra: dict = {}

    try:
        if mode == AgentMode.FAST:
            run_agent_turn(client, messages, ctx, hooks)
            return

        if mode == AgentMode.TRADE_REVIEW:
            ui.info("交易复盘模式")
            TradeReviewOrchestrator(ctx.root, client, hooks).run(query, messages, ctx)
            return

        ui.info(f"协作任务已启动: {mode.value}")
        ResearchOrchestrator(ctx.root, client, hooks).run(
            query, messages, ctx, mode=mode,
        )
    except Exception:
        success = False
        raise
    finally:
        stats = end_turn_eval()
        if stats:
            extra = stats.to_extra()
        if timer and recorder:
            recorder.record(RunMetrics(
                variant=_variant_label(mode),
                query=query[:500],
                latency_ms=timer.elapsed_ms(),
                success=success,
                extra=extra,
            ))
