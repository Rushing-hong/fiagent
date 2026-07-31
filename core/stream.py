"""流式 LLM 调用与消息组装。"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace
from typing import Any

import httpx
from openai import APIConnectionError, APITimeoutError, OpenAI

from core.llm.catalog import get_model_spec
from core.llm.client import MissingApiKeyError, build_thinking_kwargs, get_client_for_model
from core.llm.limiter import llm_slot
from core.turn_control import turn_control
from ui import ui
from ui.prefs import get_model, get_reasoning_effort

_MAX_STREAM_ATTEMPTS = max(1, int(os.getenv("FIAGENT_LLM_STREAM_RETRIES", "2")))


class LlmStreamError(RuntimeError):
    """Stream failed after retries; message may already be shown via ui.error."""


def _exc_detail(exc: BaseException) -> str:
    text = str(exc).strip()
    if text:
        return text
    return f"{type(exc).__module__}.{type(exc).__name__}"


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


def _message_from_api(msg: Any) -> SimpleNamespace:
    tool_calls = list(msg.tool_calls) if getattr(msg, "tool_calls", None) else None
    reasoning = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
    return SimpleNamespace(
        content=getattr(msg, "content", None),
        reasoning_content=reasoning or None,
        tool_calls=tool_calls,
    )


def _finalize_reply_stream(content: str, reply_open: bool) -> bool:
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


def _delta_reasoning(delta: Any) -> str | None:
    for attr in ("reasoning_content", "reasoning"):
        val = getattr(delta, attr, None)
        if val:
            return val
    return None


def is_transient_stream_error(exc: BaseException) -> bool:
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    if isinstance(
        exc,
        (
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.ConnectTimeout,
            httpx.WriteTimeout,
            ConnectionError,
            TimeoutError,
        ),
    ):
        return True
    msg = str(exc).lower()
    needles = (
        "incomplete chunked read",
        "peer closed connection",
        "connection reset",
        "broken pipe",
        "timed out",
        "unexpected eof",
    )
    return any(n in msg for n in needles)


def _cleanup_partial_stream(
    *,
    reply_open: bool,
    thinking_open: bool,
    reasoning: str,
    content: str,
) -> None:
    if thinking_open:
        ui.stream_thinking_end(reasoning)
    if reply_open:
        ui.stream_reply_cancel()
    ui.llm_activity_clear()


def _consume_stream(
    stream: Any,
    *,
    round_idx: int,
    quiet_ui: bool,
) -> tuple[str, str, dict[int, dict]]:
    content = ""
    reasoning = ""
    tool_acc: dict[int, dict] = {}
    reply_open = False
    thinking_open = False

    try:
        got_first = False
        for chunk in stream:
            turn_control.checkpoint(f"第 {round_idx} 轮流式推理")
            if not got_first:
                got_first = True
                ui.llm_activity_update("准备输出中…")
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue

            rc = _delta_reasoning(delta)
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

    return content, reasoning, tool_acc


def _try_non_stream_completion(
    active: OpenAI,
    create_kwargs: dict[str, Any],
    *,
    quiet_ui: bool,
) -> SimpleNamespace | None:
    kwargs = dict(create_kwargs)
    kwargs["stream"] = False
    try:
        if not quiet_ui:
            ui.llm_activity_update("流式中断，改用非流式请求…")
        with llm_slot():
            resp = active.chat.completions.create(**kwargs)
        if not resp.choices:
            return None
        return _message_from_api(resp.choices[0].message)
    except Exception:
        return None


def _non_stream_first(
    active: OpenAI,
    create_kwargs: dict[str, Any],
) -> SimpleNamespace | None:
    """Sub-agents: skip SSE entirely (more stable with thinking+tools)."""
    kwargs = dict(create_kwargs)
    kwargs["stream"] = False
    try:
        with llm_slot():
            resp = active.chat.completions.create(**kwargs)
        if not resp.choices:
            return None
        return _message_from_api(resp.choices[0].message)
    except Exception:
        return None


def _emit_quiet_llm_error(message: str) -> None:
    try:
        from research.run_context import get_run_context
        from ui.web.agent_progress import emit_agent_progress

        rc = get_run_context()
        if rc is None:
            return
        emit_agent_progress({
            "phase": "agent_log",
            "run_id": rc.run_id,
            "agent": rc.agent_name,
            "level": "error",
            "message": message[:500],
        })
    except Exception:
        pass


def stream_chat_completion(
    client: OpenAI | None = None,
    *,
    messages: list,
    tools: list | None,
    round_idx: int,
    tool_choice: str = "auto",
    model_override: str | None = None,
    quiet_ui: bool = False,
) -> SimpleNamespace:
    """Stream one chat completion. Uses prefs model unless model_override set."""
    ui.llm_round_start(round_idx)

    model_id = model_override or get_model()
    effort = get_reasoning_effort()
    _ = client
    try:
        spec = get_model_spec(model_id)
        active = get_client_for_model(model_id)
    except MissingApiKeyError as exc:
        if not quiet_ui:
            ui.error(str(exc))
        raise
    except KeyError:
        if not quiet_ui:
            ui.error(f"未知模型: {model_id}")
        raise

    create_kwargs: dict[str, Any] = {
        "model": spec.api_model,
        "messages": messages,
        "stream": True,
    }
    if tools:
        create_kwargs["tools"] = tools
        create_kwargs["tool_choice"] = tool_choice
    else:
        create_kwargs["tool_choice"] = "none"
    create_kwargs.update(build_thinking_kwargs(spec.thinking, effort))

    tool_n = len(tools) if tools else 0
    effort_note = effort if spec.supports_thinking else "n/a"
    wait_hint = ""
    if spec.supports_thinking and effort != "off":
        wait_hint = "（thinking 开启时首字前可能较久）"

    # Sub-agents (research team): non-stream avoids SSE chunked-read drops on long prefill.
    if quiet_ui:
        direct = _non_stream_first(active, create_kwargs)
        if direct is not None:
            ui.llm_activity_clear()
            reasoning = getattr(direct, "reasoning_content", None) or ""
            if reasoning:
                ui.stream_thinking_end(reasoning)
            return direct

    last_exc: BaseException | None = None
    for attempt in range(1, _MAX_STREAM_ATTEMPTS + 1):
        content = ""
        reasoning = ""
        tool_acc: dict[int, dict] = {}
        try:
            if attempt > 1 and not quiet_ui:
                ui.warn(
                    f"LLM 流式连接中断，正在重试 ({attempt}/{_MAX_STREAM_ATTEMPTS})…"
                )
                time.sleep(min(1.5 * (attempt - 1), 4.0))

            if not quiet_ui:
                ui.llm_activity_update(
                    f"连接 {spec.label} · effort={effort_note} · {tool_n} tools…"
                    + (" · 步骤上限收尾" if not tools else "")
                    + (f" · 重试 {attempt}/{_MAX_STREAM_ATTEMPTS}" if attempt > 1 else "")
                )
            with llm_slot():
                stream = active.chat.completions.create(**create_kwargs)
            if not quiet_ui:
                ui.llm_activity_update("等待首包…" + wait_hint)
            content, reasoning, tool_acc = _consume_stream(
                stream, round_idx=round_idx, quiet_ui=quiet_ui,
            )
            return _build_message(content, reasoning, tool_acc)
        except Exception as exc:
            last_exc = exc
            if not is_transient_stream_error(exc) or attempt >= _MAX_STREAM_ATTEMPTS:
                break
            _cleanup_partial_stream(
                reply_open=False,
                thinking_open=False,
                reasoning=reasoning,
                content=content,
            )

    fallback = _try_non_stream_completion(active, create_kwargs, quiet_ui=quiet_ui)
    if fallback is not None:
        return fallback

    assert last_exc is not None
    hint = (
        "LLM 请求失败（已尝试流式降级与非流式）。"
        "DeepSeek 官方文档支持 stream=true/false；"
        "incomplete chunked read 多为 SSE 长连接被网关/代理或并发压断，"
        "不一定是业务逻辑错误。请重启服务后重试，或换 deepseek-v4-flash、降低思考强度。"
    )
    detail = f"{hint}\n\n技术细节: {_exc_detail(last_exc)}"
    if quiet_ui:
        _emit_quiet_llm_error(detail)
        raise LlmStreamError(detail) from last_exc
    ui.error(detail)
    raise LlmStreamError(detail) from last_exc
