"""ReAct 循环：LLM 推理 + 工具调用（只读并行、写入串行）。

停止机制对齐 OpenCode（软步骤上限 + doom_loop），而非硬塞「已达上限」假回复：
- 默认步数内正常跑工具；最后一步关闭 tools、注入总结提示，让模型用文本收尾
- 连续 3 次同名+同参工具 → doom_loop 拒绝该次调用（可换参继续），不整轮掐死
- 用户 Esc/abort 仍走 turn_control
"""

from __future__ import annotations

import json
import os
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from openai import OpenAI

from core.context import AgentContext
from core.context_budget import estimate_context_usage
from core.message_sanitize import slim_messages_for_api
from core.stream import stream_chat_completion
from core.turn_control import TurnAborted, turn_control
from hooks.registry import HookRegistry
from ui import ui

# 可选软上限（最后一步关工具做总结）；默认偏大，避免排行失败后立刻被掐
MAX_TOOL_ROUNDS = int(os.environ.get("FIAGENT_MAX_TOOL_ROUNDS", "40"))
MAX_READONLY_WORKERS = int(os.environ.get("FIAGENT_MAX_READONLY_WORKERS", "8"))
# 连续相同 name+args 次数达到阈值 → 拒绝该次（OpenCode doom_loop=3）
DOOM_LOOP_AT = int(os.environ.get("FIAGENT_DOOM_LOOP_AT", "3"))
# 末步若模型仍死磕工具，最多再拒几次后强制结束
_POST_LIMIT_REFUSALS = int(os.environ.get("FIAGENT_POST_LIMIT_REFUSALS", "2"))

_call_counts_lock = threading.Lock()

_REPEAT_WARNING = (
    "【提醒】工具 `{name}` 在本轮对话中已是第 {count} 次调用。"
    "请优先基于已有结果继续；若确需再调，请换参数或换工具。\n\n"
)

# 对齐 OpenCode MAX_STEPS_PROMPT：末步只许文本总结
_MAX_STEPS_PROMPT = """【关键】已达本轮最大步骤数

工具已暂时禁用，直到用户下一条输入。请只回复文本，不要再发起任何工具调用。

必须说明：
1. 已完成的工作摘要
2. 尚未完成的事项
3. 建议用户下一步怎么做（可缩小范围后重试）

此约束优先于其它指令。只用文字回复。"""

_DOOM_LOOP_MSG = (
    "【doom_loop】已连续 {n} 次以相同参数调用 `{name}`，已拒绝本次执行。"
    "请改用不同参数、换工具，或基于已有结果直接给出结论。"
    "（阈值 FIAGENT_DOOM_LOOP_AT={n}）"
)

_TOOLS_DISABLED_MSG = (
    "【步骤上限】工具已禁用。请用纯文本总结已完成与未完成事项，勿再请求工具。"
)


def assistant_message(msg: Any) -> dict[str, Any]:
    content = msg.content
    tool_calls = msg.tool_calls
    reasoning = getattr(msg, "reasoning_content", None)

    # 无 tool_calls 时 content 不能为空（DeepSeek 400）
    if not tool_calls:
        text = (content or "").strip()
        if not text and isinstance(reasoning, str) and reasoning.strip():
            text = reasoning.strip()
        if not text:
            text = "…"  # 极端兜底，避免写入非法 assistant
        content = text

    payload: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning:
        payload["reasoning_content"] = reasoning
    if tool_calls:
        payload["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in tool_calls
        ]
        if not (isinstance(content, str) and content.strip()):
            payload["content"] = None
    return payload


def _promote_empty_reply(msg: Any) -> Any:
    """Thinking 模型有时只填 reasoning、正文为空：收尾时提升为正式回复。"""
    if msg.tool_calls:
        return msg
    content = (msg.content or "").strip()
    reasoning = (getattr(msg, "reasoning_content", None) or "").strip()
    if content or not reasoning:
        return msg
    from types import SimpleNamespace

    return SimpleNamespace(
        content=reasoning,
        reasoning_content=reasoning,
        tool_calls=None,
    )


def _norm_args(arguments: str) -> str:
    raw = (arguments or "").strip()
    if not raw:
        return ""
    try:
        return json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True)
    except Exception:
        return raw


_ABORT_TOOL_MSG = "工具调用已中止（用户 abort）"


def run_tool_with_hooks(
    hooks: HookRegistry,
    ctx: AgentContext,
    name: str,
    arguments: str,
    call_counts: dict[str, int],
    recent_sigs: deque[tuple[str, str]],
) -> str:
    if turn_control.is_aborted():
        return _ABORT_TOOL_MSG

    sig = (name, _norm_args(arguments))
    with _call_counts_lock:
        count = call_counts.get(name, 0) + 1
        call_counts[name] = count
        recent_sigs.append(sig)
        # 保留窗口略大于阈值，便于判断「最近 N 次全相同」
        while len(recent_sigs) > max(DOOM_LOOP_AT, 8):
            recent_sigs.popleft()
        tail = list(recent_sigs)[-DOOM_LOOP_AT:]
        doom = (
            DOOM_LOOP_AT >= 2
            and len(tail) >= DOOM_LOOP_AT
            and all(s == sig for s in tail)
        )

    if doom:
        return _DOOM_LOOP_MSG.format(n=DOOM_LOOP_AT, name=name)

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
    if turn_control.is_aborted():
        return _ABORT_TOOL_MSG
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
    recent_sigs: deque[tuple[str, str]],
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
        pool = ThreadPoolExecutor(max_workers=workers)
        aborted_parallel = False
        try:
            futures = {
                pool.submit(
                    run_tool_with_hooks,
                    hooks,
                    ctx,
                    tc.function.name,
                    tc.function.arguments,
                    call_counts,
                    recent_sigs,
                ): tc
                for tc in readonly
            }
            for future in as_completed(futures):
                tc = futures[future]
                try:
                    results[tc.id] = future.result()
                except Exception as exc:
                    results[tc.id] = f"工具执行异常: {exc}"
                if turn_control.is_aborted():
                    aborted_parallel = True
                    for pending, ptc in futures.items():
                        if ptc.id not in results:
                            pending.cancel()
                            results[ptc.id] = _ABORT_TOOL_MSG
                    break
        finally:
            # abort 时勿阻塞等待仍在跑的只读工具
            pool.shutdown(wait=not aborted_parallel, cancel_futures=True)

    for tc in writes:
        if turn_control.is_aborted():
            results[tc.id] = _ABORT_TOOL_MSG
            continue
        results[tc.id] = run_tool_with_hooks(
            hooks, ctx, tc.function.name, tc.function.arguments, call_counts, recent_sigs
        )

    if turn_control.is_aborted():
        raise TurnAborted()

    return results


def _refuse_tools(msg: Any, messages: list[dict[str, Any]]) -> None:
    """末步仍要调工具：写入拒绝结果，促使下一轮纯文本。"""
    ui.warn("步骤上限：已拒绝工具调用，请模型改用文本总结")
    for tc in msg.tool_calls or []:
        ui.show_tool_result(tc.function.name, _TOOLS_DISABLED_MSG)
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": _TOOLS_DISABLED_MSG,
        })


def run_agent_turn(
    client: OpenAI,
    messages: list[dict[str, Any]],
    ctx: AgentContext,
    hooks: HookRegistry,
) -> None:
    call_counts: dict[str, int] = {}
    recent_sigs: deque[tuple[str, str]] = deque()
    post_limit_refusals = 0
    turn_control.start()
    ui.begin_turn()
    if not turn_control.tui_mode:
        ui.info("运行中: e/1-9 展开  list 列表  Esc 暂停")

    try:
        step = 0
        while True:
            step += 1
            # 末步及之后：关工具 + 总结提示（OpenCode soft max-steps）
            is_last = step >= MAX_TOOL_ROUNDS
            turn_control.checkpoint(f"第 {step} 步开始前")

            if step == 1:
                ctx.refresh()
            ctx.sync_system_message(messages)
            tools = [] if is_last else ctx.build_openai_tools()
            tool_choice = "none" if is_last else "auto"

            llm_before = hooks.emit("llm.before", {
                "messages": messages,
                "tools": tools,
                "round_idx": step,
            })
            if llm_before.cancel:
                ui.hook_blocked("LLM 调用被 hook 拦截")
                return

            req_messages = llm_before.get("messages", messages)
            req_tools = llm_before.get("tools", tools)
            if is_last:
                req_tools = []
            req_messages = slim_messages_for_api(
                req_messages,
                is_readonly=ctx.is_readonly_tool,
                tools=req_tools,
            )
            req_messages = ctx.with_clock_for_api(req_messages)
            if is_last:
                # 仅 API 副本注入，不污染会话历史
                req_messages = list(req_messages) + [
                    {"role": "user", "content": _MAX_STEPS_PROMPT},
                ]

            usage = estimate_context_usage(req_messages, req_tools)
            ui.show_context_progress(usage)

            msg = stream_chat_completion(
                client,
                messages=req_messages,
                tools=req_tools,
                tool_choice=tool_choice,
                round_idx=step,
            )
            msg = _promote_empty_reply(msg)

            turn_control.checkpoint(f"第 {step} 步推理后")
            hooks.emit("llm.after", {"message": msg, "round_idx": step})
            messages.append(assistant_message(msg))

            if msg.tool_calls:
                if is_last:
                    _refuse_tools(msg, messages)
                    post_limit_refusals += 1
                    if post_limit_refusals >= _POST_LIMIT_REFUSALS:
                        ui.warn("步骤上限后仍反复请求工具，结束本轮")
                        # 不硬塞假失败文案；保留已有 assistant/tool，等用户接着问
                        return
                    continue

                ui.show_tool_round(step, msg)
                turn_control.checkpoint(f"第 {step} 步工具执行前")
                results = _execute_tool_calls(
                    msg.tool_calls, hooks, ctx, call_counts, recent_sigs
                )
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
    finally:
        turn_control.stop()
