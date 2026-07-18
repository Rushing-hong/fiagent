"""Sanitize / slim messages before sending to the LLM API.

Session 中的 messages 保持完整；本模块只产出「请求副本」，不改历史。

原则：工具返回视为必要事实，**不对 tool 正文做截断/丢条目**。
仅做：消息合法性修复、去掉历史 reasoning_content（降 token，不丢工具数据）。
"""
from __future__ import annotations

from typing import Any


def sanitize_messages_for_api(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """DeepSeek 要求 assistant 必须有 content 或 tool_calls。

    Thinking 模式偶发只写 reasoning_content；历史 session 也可能已脏数据。
    发送前修复，避免 400 invalid_request_error。
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            out.append(dict(msg))
            continue
        m = dict(msg)
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
    """请求侧瘦身：只去掉历史 reasoning_content。

    不截断、不压缩 tool 正文（工具结果默认全量进模型）。
    is_readonly / tools 保留参数以兼容调用方，当前不使用。
    """
    del is_readonly, tools  # 兼容旧签名；刻意不用
    msgs = sanitize_messages_for_api(messages)
    slim: list[dict[str, Any]] = []
    for msg in msgs:
        m = dict(msg)
        m.pop("reasoning_content", None)
        slim.append(m)
    return slim
