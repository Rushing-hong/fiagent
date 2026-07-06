"""示例 hook：在关键节点打印日志。"""

from hooks.registry import HookContext
from ui import ui


def on_session_start(ctx: HookContext) -> HookContext:
    skills = ctx.get("skills", [])
    ui.hook_log("session.start", f"已加载 {len(skills)} 个 skill")
    return ctx


def on_turn_start(ctx: HookContext) -> HookContext:
    ui.hook_log("turn.start", f"用户输入: {ctx.get('input')!r}")
    return ctx


def on_llm_before(ctx: HookContext) -> HookContext:
    ui.hook_log("llm.before", f"第 {ctx.get('round_idx')} 轮 LLM 调用")
    return ctx


def on_llm_after(ctx: HookContext) -> HookContext:
    msg = ctx.get("message")
    tool_count = len(msg.tool_calls) if msg and msg.tool_calls else 0
    ui.hook_log("llm.after", f"工具调用数: {tool_count}")
    return ctx


def on_tool_before(ctx: HookContext) -> HookContext:
    ui.hook_log("tool.before", ctx.get("name", "?"))
    return ctx


def on_tool_after(ctx: HookContext) -> HookContext:
    ui.hook_log("tool.after", ctx.get("name", "?"))
    return ctx


HANDLERS = {
    "session.start": on_session_start,
    "turn.start": on_turn_start,
    "llm.before": on_llm_before,
    "llm.after": on_llm_after,
    "tool.before": on_tool_before,
    "tool.after": on_tool_after,
}
