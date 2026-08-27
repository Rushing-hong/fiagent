"""Sanitize / slim messages before sending to the LLM API.

Session 中的 messages 保持完整；本模块只产出「请求副本」，不改历史。

原则：工具返回视为必要事实，**不截断独特的 tool 正文**。
仅做：消息合法性修复、去掉历史 reasoning_content，以及折叠完全相同的
只读工具结果（保留首次完整正文）。
"""
from __future__ import annotations

from typing import Any

from core.config import env_int


_CONTENT_RISK_TOOL_CHARS = env_int(
    "FIAGENT_CONTENT_RISK_TOOL_CHARS",
    6_000,
    minimum=1_000,
    maximum=50_000,
)


def sanitize_messages_for_api(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """DeepSeek 要求 assistant 必须有 content 或 tool_calls。

    Thinking 模式偶发只写 reasoning_content；历史 session 也可能已脏数据。
    发送前修复，避免 400 invalid_request_error。
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        # Session-only UI metadata is intentionally retained on disk but must
        # never be forwarded as an unsupported chat-completions message field.
        clean = {k: v for k, v in msg.items() if not k.startswith("_fiagent")}
        if msg.get("role") != "assistant":
            out.append(clean)
            continue
        m = clean
        content = m.get("content")
        tool_calls = m.get("tool_calls")
        reasoning = m.get("reasoning_content")
        has_tools = bool(tool_calls)
        has_content = isinstance(content, str) and bool(content.strip())
        if has_tools:
            if not has_content:
                m["content"] = None
            out.append(m)
            continue
        if has_content:
            out.append(m)
            continue
        if isinstance(reasoning, str) and reasoning.strip():
            m["content"] = reasoning.strip()
            out.append(m)
            continue
        continue

    valid_ids: set[str] = set()
    for msg in out:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            if isinstance(tc, dict):
                tid = tc.get("id")
            else:
                tid = getattr(tc, "id", None)
            if tid:
                valid_ids.add(str(tid))

    cleaned: list[dict[str, Any]] = []
    for msg in out:
        if msg.get("role") == "tool":
            tid = msg.get("tool_call_id")
            if tid is None or str(tid) not in valid_ids:
                continue
        cleaned.append(msg)
    return cleaned


def slim_messages_for_api(
    messages: list[dict[str, Any]],
    *,
    is_readonly: Any = None,
    tools: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """请求侧瘦身：去掉历史 reasoning 并折叠完全重复的只读结果。

    独特结果和写工具结果始终保留全量。只读工具的结果正文与此前结果
    完全相同时，后续副本替换成一条引用标记；session 历史不受影响。
    `tools` 保留以兼容旧调用方。
    """
    del tools  # 兼容旧签名
    msgs = sanitize_messages_for_api(messages)
    for msg in msgs:
        msg.pop("reasoning_content", None)

    if not callable(is_readonly):
        return msgs

    call_names: dict[str, str] = {}
    seen_results: set[tuple[str, str]] = set()
    for msg in msgs:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict):
                    tid = tc.get("id")
                    function = tc.get("function") or {}
                    name = function.get("name") if isinstance(function, dict) else None
                else:
                    tid = getattr(tc, "id", None)
                    function = getattr(tc, "function", None)
                    name = getattr(function, "name", None)
                if tid and name:
                    call_names[str(tid)] = str(name)
            continue
        if msg.get("role") != "tool":
            continue
        name = call_names.get(str(msg.get("tool_call_id") or ""))
        if not name or not is_readonly(name):
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        signature = (name, content)
        if signature in seen_results:
            msg["content"] = f"[重复只读结果：与此前 `{name}` 的结果完全相同，正文已省略]"
        else:
            seen_results.add(signature)
    return msgs


def compact_tool_messages_for_content_risk(
    messages: list[dict[str, Any]],
    *,
    max_chars: int | None = None,
) -> list[dict[str, Any]]:
    """Shorten oversized tool results only after provider content-risk rejection.

    Normal requests retain every unique tool result in full.  The recovery copy
    keeps both the head and tail, preserving the response envelope, early
    observations, and latest rows while removing a large repetitive middle.
    """
    limit = max_chars or _CONTENT_RISK_TOOL_CHARS
    out: list[dict[str, Any]] = []
    for message in messages:
        copied = dict(message)
        content = copied.get("content")
        if copied.get("role") != "tool" or not isinstance(content, str):
            out.append(copied)
            continue
        if len(content) <= limit:
            out.append(copied)
            continue

        tail_chars = min(1_500, max(250, limit // 4))
        head_chars = max(250, limit - tail_chars)
        omitted = max(0, len(content) - head_chars - tail_chars)
        copied["content"] = (
            content[:head_chars]
            + f"\n...[内容风控恢复模式省略 {omitted} 字符，保留工具结果首尾]...\n"
            + content[-tail_chars:]
        )
        out.append(copied)
    return out
