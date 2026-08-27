"""流式 LLM 调用与消息组装。"""

from __future__ import annotations

import hashlib
import os
import time
from types import SimpleNamespace
from typing import Any

import httpx
from openai import APIConnectionError, APITimeoutError, OpenAI

from core.config import env_int
from core.llm.catalog import get_model_spec
from core.llm.cache_metrics import record_cache_usage
from core.llm.client import MissingApiKeyError, build_thinking_kwargs, get_client_for_model
from core.llm.limiter import llm_slot
from core.message_sanitize import compact_tool_messages_for_content_risk
from core.turn_control import turn_control
from ui import ui
from ui.prefs import get_model, get_reasoning_effort

_MAX_STREAM_ATTEMPTS = env_int(
    "FIAGENT_LLM_STREAM_RETRIES", 2, minimum=1, maximum=10
)


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


def _build_message(
    content: str,
    reasoning: str,
    tool_acc: dict[int, dict],
    usage: Any = None,
) -> SimpleNamespace:
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
        usage=usage,
    )


def _message_from_api(msg: Any, usage: Any = None) -> SimpleNamespace:
    tool_calls = list(msg.tool_calls) if getattr(msg, "tool_calls", None) else None
    reasoning = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
    return SimpleNamespace(
        content=getattr(msg, "content", None),
        reasoning_content=reasoning or None,
        tool_calls=tool_calls,
        usage=usage,
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


def is_content_risk_error(exc: BaseException) -> bool:
    """DeepSeek may reject an otherwise valid research prompt during moderation."""
    return "content exists risk" in str(exc).lower()


def _llm_failure_detail(
    exc: BaseException,
    *,
    retried_without_thinking: bool = False,
    retried_with_compacted_tools: bool = False,
) -> str:
    if is_content_risk_error(exc):
        retry_modes: list[str] = []
        if retried_without_thinking:
            retry_modes.append("关闭 thinking")
        if retried_with_compacted_tools:
            retry_modes.append("压缩超长工具结果")
        retry_note = (
            "已尝试" + "并".join(retry_modes) + "，仍被拒绝。"
            if retry_modes else ""
        )
        return (
            "模型服务的内容风控拒绝了本次请求（Content Exists Risk）。"
            "这不是 SSE 或本地网络故障，常见于供应商误判上游研究材料或生成内容。"
            f"{retry_note}可缩短上游材料、稍后重试，或切换到其他已配置模型。"
            f"\n\n技术细节: {_exc_detail(exc)}"
        )
    if is_transient_stream_error(exc):
        return (
            "LLM 连接在传输过程中中断。已尝试流式重试与非流式降级；"
            "请检查代理/VPN，稍后重试，或降低思考强度。"
            f"\n\n技术细节: {_exc_detail(exc)}"
        )
    return (
        "LLM 请求被模型服务拒绝或参数不兼容；重试通常不会自动解决。"
        "请核对模型配置，或切换模型后重试。"
        f"\n\n技术细节: {_exc_detail(exc)}"
    )


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
) -> tuple[str, str, dict[int, dict], Any]:
    content = ""
    reasoning = ""
    tool_acc: dict[int, dict] = {}
    reply_open = False
    thinking_open = False
    usage = None

    try:
        got_first = False
        for chunk in stream:
            turn_control.checkpoint(f"第 {round_idx} 轮流式推理")
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage = chunk_usage
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

    return content, reasoning, tool_acc, usage


def _try_non_stream_completion(
    active: OpenAI,
    create_kwargs: dict[str, Any],
    *,
    quiet_ui: bool,
) -> SimpleNamespace | None:
    kwargs = dict(create_kwargs)
    kwargs["stream"] = False
    kwargs.pop("stream_options", None)
    try:
        if not quiet_ui:
            ui.llm_activity_update("流式中断，改用非流式请求…")
        with llm_slot():
            resp = active.chat.completions.create(**kwargs)
        if not resp.choices:
            return None
        return _message_from_api(resp.choices[0].message, getattr(resp, "usage", None))
    except Exception:
        return None


def _non_stream_first(
    active: OpenAI,
    create_kwargs: dict[str, Any],
) -> SimpleNamespace | None:
    """Sub-agents: skip SSE entirely (more stable with thinking+tools)."""
    kwargs = dict(create_kwargs)
    kwargs["stream"] = False
    kwargs.pop("stream_options", None)
    with llm_slot():
        resp = active.chat.completions.create(**kwargs)
    if not resp.choices:
        return None
    return _message_from_api(resp.choices[0].message, getattr(resp, "usage", None))


def _emit_quiet_llm_error(message: str) -> None:
    try:
        from research.run_context import get_run_context
        from ui.web.collaboration_progress import emit_collaboration_progress

        rc = get_run_context()
        if rc is None:
            return
        emit_collaboration_progress({
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
    provider = str(getattr(spec, "provider", ""))
    endpoint = str(getattr(active, "base_url", "")).lower()
    official_deepseek = provider == "deepseek" and "api.deepseek.com" in endpoint
    official_openai = provider == "openai" and "api.openai.com" in endpoint
    if official_deepseek or official_openai:
        # Both official APIs support a final usage-only SSE chunk.
        create_kwargs["stream_options"] = {"include_usage": True}
    if official_openai:
        # Stable across all agent profiles; OpenAI combines this with the
        # exact prefix hash to improve cache-aware request routing.
        shared = ""
        if messages and messages[0].get("role") == "system":
            shared = str(messages[0].get("content") or "")
        digest = hashlib.sha256(shared.encode("utf-8")).hexdigest()[:24]
        create_kwargs["prompt_cache_key"] = f"fiagent-shared-{digest}"
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
        try:
            direct = _non_stream_first(active, create_kwargs)
        except Exception as exc:
            if is_content_risk_error(exc):
                retry_kwargs = dict(create_kwargs)
                retry_kwargs.pop("reasoning_effort", None)
                retry_kwargs.pop("extra_body", None)
                retry_kwargs.update(build_thinking_kwargs(spec.thinking, "off"))
                retry_exc: Exception = exc
                direct = None

                # If thinking was already disabled, an identical retry adds no
                # recovery value. Otherwise try the lower-risk mode first.
                if effort != "off":
                    try:
                        direct = _non_stream_first(active, retry_kwargs)
                    except Exception as candidate_exc:
                        retry_exc = candidate_exc

                compacted = False
                if direct is None and is_content_risk_error(retry_exc):
                    compact_messages = compact_tool_messages_for_content_risk(
                        list(retry_kwargs.get("messages") or [])
                    )
                    if compact_messages != retry_kwargs.get("messages"):
                        compacted = True
                        compact_kwargs = dict(retry_kwargs)
                        compact_kwargs["messages"] = compact_messages
                        try:
                            direct = _non_stream_first(active, compact_kwargs)
                        except Exception as compact_exc:
                            retry_exc = compact_exc

                if direct is None:
                    detail = _llm_failure_detail(
                        retry_exc,
                        retried_without_thinking=effort != "off",
                        retried_with_compacted_tools=compacted,
                    )
                    _emit_quiet_llm_error(detail)
                    raise LlmStreamError(detail) from retry_exc
            elif not is_transient_stream_error(exc):
                detail = _llm_failure_detail(exc)
                _emit_quiet_llm_error(detail)
                raise LlmStreamError(detail) from exc
            else:
                direct = None
        if direct is not None:
            record_cache_usage(provider, spec.api_model, direct.usage)
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
            content, reasoning, tool_acc, usage = _consume_stream(
                stream, round_idx=round_idx, quiet_ui=quiet_ui,
            )
            result = _build_message(content, reasoning, tool_acc, usage)
            record_cache_usage(provider, spec.api_model, usage)
            return result
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

    # A non-stream retry only changes the transport.  It can recover from an
    # interrupted SSE connection, but deterministic 4xx/schema errors will be
    # rejected again with identical parameters and should fail immediately.
    if is_transient_stream_error(last_exc):
        fallback = _try_non_stream_completion(active, create_kwargs, quiet_ui=quiet_ui)
        if fallback is not None:
            record_cache_usage(provider, spec.api_model, fallback.usage)
            return fallback

    assert last_exc is not None
    detail = _llm_failure_detail(last_exc)
    if quiet_ui:
        _emit_quiet_llm_error(detail)
        raise LlmStreamError(detail) from last_exc
    ui.error(detail)
    raise LlmStreamError(detail) from last_exc
