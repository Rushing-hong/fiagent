"""ReAct 循环：LLM 推理 + 工具调用（只读并行、写入串行）。"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from openai import OpenAI
from openai.types.chat import ChatCompletionMessage

from core.context import AgentContext
from core.stream import stream_chat_completion
from core.turn_control import TurnAborted, turn_control
from hooks.registry import HookRegistry
from ui import ui

MAX_TOOL_ROUNDS = int(os.environ.get("FIAGENT_MAX_TOOL_ROUNDS", "10"))
MAX_READONLY_WORKERS = int(os.environ.get("FIAGENT_MAX_READONLY_WORKERS", "8"))
_call_counts_lock = threading.Lock()

_REPEAT_WARNING = (
    "【提醒】工具 `{name}` 在本轮对话中已是第 {count} 次调用。"
    "重复调用往往说明上一轮结果未被充分消化，请优先基于已有结果继续推理；"
    "若确需再次调用，请说明与上次不同的目的或参数依据。\n\n"
)


def assistant_message(msg: Any) -> dict[str, Any]:
    payload = {"role": "assistant", "content": msg.content}
    reasoning = getattr(msg, "reasoning_content", None)
    if reasoning:
        payload["reasoning_content"] = reasoning
    if msg.tool_calls:
        payload["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in msg.tool_calls
        ]
    return payload


def run_tool_with_hooks(
    hooks: HookRegistry,
    ctx: AgentContext,
    name: str,
    arguments: str,
    call_counts: dict[str, int],
) -> str:
    with _call_counts_lock:
        count = call_counts.get(name, 0) + 1
        call_counts[name] = count

    warn = (
        _REPEAT_WARNING.format(name=name, count=count)
        if not ctx.is_repeatable_tool(name) and count > 1
        else ""
    )

    before = hooks.emit("tool.before", {"name": name, "arguments": arguments})
    if before.cancel:
        return before.get("result", "工具调用被 hook 拦截")

    name = before.get("name", name)
    arguments = before.get("arguments", arguments)
    result = ctx.execute_tool(name, arguments)

    after = hooks.emit("tool.after", {
        "name": name,
        "arguments": arguments,
        "result": result,
    })
    result = after.get("result", result)
    if warn:
        result = warn + result
    return result


def _execute_tool_calls(
    tool_calls: list[Any],
    hooks: HookRegistry,
    ctx: AgentContext,
    call_counts: dict[str, int],
) -> dict[str, str]:
    readonly = []
    writes = []
    for tc in tool_calls:
        if ctx.is_readonly_tool(tc.function.name):
            readonly.append(tc)
        else:
            writes.append(tc)

    results: dict[str, str] = {}

    if readonly:
        workers = min(MAX_READONLY_WORKERS, len(readonly))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    run_tool_with_hooks,
                    hooks,
                    ctx,
                    tc.function.name,
                    tc.function.arguments,
                    call_counts,
                ): tc
                for tc in readonly
            }
            for future in as_completed(futures):
                tc = futures[future]
                results[tc.id] = future.result()

    for tc in writes:
        results[tc.id] = run_tool_with_hooks(
            hooks, ctx, tc.function.name, tc.function.arguments, call_counts
        )

    return results


def run_agent_turn(
    client: OpenAI,
    messages: list[dict[str, Any]],
    ctx: AgentContext,
    hooks: HookRegistry,
) -> None:
    call_counts: dict[str, int] = {}
    turn_control.start()
    ui.begin_turn()
    if not turn_control.tui_mode:
        ui.info("运行中: e/1-9 展开  list 列表  Esc 暂停")

    try:
        for round_idx in range(MAX_TOOL_ROUNDS):
            turn_control.checkpoint(f"第 {round_idx + 1} 轮开始前")
            ctx.refresh()
            ctx.sync_system_message(messages)
            tools = ctx.build_openai_tools()

            llm_before = hooks.emit("llm.before", {
                "messages": messages,
                "tools": tools,
                "round_idx": round_idx + 1,
            })
            if llm_before.cancel:
                ui.hook_blocked("LLM 调用被 hook 拦截")
                return

            req_messages = llm_before.get("messages", messages)
            req_tools = llm_before.get("tools", tools)

            msg = stream_chat_completion(
                client,
                messages=req_messages,
                tools=req_tools,
                round_idx=round_idx + 1,
            )

            turn_control.checkpoint(f"第 {round_idx + 1} 轮推理后")
            hooks.emit("llm.after", {"message": msg, "round_idx": round_idx + 1})
            messages.append(assistant_message(msg))

            if msg.tool_calls:
                ui.show_tool_round(round_idx + 1, msg)
                turn_control.checkpoint(f"第 {round_idx + 1} 轮工具执行前")
                results = _execute_tool_calls(msg.tool_calls, hooks, ctx, call_counts)
                for tc in msg.tool_calls:
                    turn_control.checkpoint(f"工具 {tc.function.name} 结果展示前")
                    result = results[tc.id]
                    ui.show_tool_result(tc.function.name, result)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                continue

            if getattr(msg, "reasoning_content", None) and not ui.thinking_was_shown:
                ui.show_thinking(msg.reasoning_content)
            if msg.content and not ui.reply_was_streamed:
                ui.show_reply(msg.content)
            ui.clear_reply_streamed()
            ui.clear_thinking_shown()
            return

        ui.error("工具调用轮次超过上限")
    finally:
        turn_control.stop()
