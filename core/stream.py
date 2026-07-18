"""流式 LLM 调用与消息组装。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from openai import OpenAI

from core.turn_control import turn_control
from ui import ui
from ui.prefs import get_model, get_reasoning_effort


def _merge_tool_delta(acc: dict[int, dict], tc_delta: Any) -> None:
    idx = tc_delta.index
    if idx not in acc:
        acc[idx] = {
            "id": "",
            "type": "function",
            "function": {"name": "", "arguments": ""},
        }
    entry = acc[idx]
    if tc_delta.id:
        entry["id"] = tc_delta.id
    fn = tc_delta.function
    if not fn:
        return
    if fn.name:
        entry["function"]["name"] += fn.name
    if fn.arguments:
        entry["function"]["arguments"] += fn.arguments


def _build_message(content: str, reasoning: str, tool_acc: dict[int, dict]) -> SimpleNamespace:
    tool_calls = None
    if tool_acc:
        tool_calls = []
        for idx in sorted(tool_acc):
            d = tool_acc[idx]
            tool_calls.append(
                SimpleNamespace(
                    id=d["id"],
                    function=SimpleNamespace(
                        name=d["function"]["name"],
                        arguments=d["function"]["arguments"],
                    ),
                )
            )
    return SimpleNamespace(
        content=content or None,
        reasoning_content=reasoning or None,
        tool_calls=tool_calls,
    )


def _finalize_reply_stream(content: str, reply_open: bool) -> bool:
    """工具调用前先结束回答流式框，避免半句话+光标卡住。"""
    if not reply_open:
        return False
    if content.strip():
        ui.stream_reply_end(content)
    else:
        ui.stream_reply_cancel()
    return False


def _end_thinking_stream(reasoning: str, thinking_open: bool) -> bool:
    if thinking_open:
        ui.stream_thinking_end(reasoning)
        return False
    return thinking_open


def stream_chat_completion(
    client: OpenAI,
    *,
    messages: list,
    tools: list | None,
    round_idx: int,
    tool_choice: str = "auto",
) -> SimpleNamespace:
    ui.llm_round_start(round_idx)

    content = ""
    reasoning = ""
    tool_acc: dict[int, dict] = {}
    reply_open = False
    thinking_open = False

    model = get_model()
    effort = get_reasoning_effort()
    create_kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    # 末步关工具：不传 tools，或传空列表 + tool_choice=none
    if tools:
        create_kwargs["tools"] = tools
        create_kwargs["tool_choice"] = tool_choice
    else:
        create_kwargs["tool_choice"] = "none"
    if effort == "off":
        create_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    else:
        create_kwargs["reasoning_effort"] = effort
        create_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

    # Thought 框要等 API 吐出首个 reasoning token 才会出现；
    # thinking 开启时 prefill + 内部思考常占数秒～数十秒，先给可见进度。
    tool_n = len(tools) if tools else 0
    ui.llm_activity_update(
        f"连接 {model} · effort={effort} · {tool_n} tools…"
        + (" · 步骤上限收尾" if not tools else "")
    )
    stream = client.chat.completions.create(**create_kwargs)
    ui.llm_activity_update(
        "等待首包…"
        + ("（thinking 开启时首字前可能较久）" if effort != "off" else "")
    )

    try:
        got_first = False
        for chunk in stream:
            turn_control.checkpoint(f"第 {round_idx} 轮流式推理")
            if not got_first:
                got_first = True
                ui.llm_activity_update("流式输出中…")
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue

            rc = getattr(delta, "reasoning_content", None)
            if rc:
                reasoning += rc
                if not reply_open and not tool_acc:
                    if not thinking_open:
                        ui.stream_thinking_begin()
                        thinking_open = True
                    ui.stream_thinking_update(reasoning)

            if delta.tool_calls:
                thinking_open = _end_thinking_stream(reasoning, thinking_open)
                reply_open = _finalize_reply_stream(content, reply_open)
                for tc in delta.tool_calls:
                    _merge_tool_delta(tool_acc, tc)
                ui.llm_activity_update("规划工具调用…")

            if delta.content:
                thinking_open = _end_thinking_stream(reasoning, thinking_open)
                content += delta.content
                if not reply_open:
                    ui.stream_reply_begin()
                    reply_open = True
                ui.stream_reply_update(content)
    finally:
        ui.llm_activity_clear()
        thinking_open = _end_thinking_stream(reasoning, thinking_open)
        if reply_open:
            ui.stream_reply_end(content)
        else:
            ui.stream_reply_cancel()

    return _build_message(content, reasoning, tool_acc)
