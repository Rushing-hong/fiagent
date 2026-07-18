"""上下文占用估算（无 tokenizer 时的粗测，用于进度条）。"""

from __future__ import annotations

import json
import os
from typing import Any

# DeepSeek V4 API 官方上下文为 1M；可用环境变量覆盖
DEFAULT_CONTEXT_TOKENS = int(os.environ.get("FIAGENT_CONTEXT_TOKENS", "1000000"))


def _chars_of(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    try:
        return len(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return len(str(value))


def estimate_tokens_from_chars(chars: int) -> int:
    """中英混合粗估：约 2 字 ≈ 1 token。"""
    return max(0, (chars + 1) // 2)


def measure_messages_chars(messages: list[dict[str, Any]]) -> int:
    total = 0
    for msg in messages:
        for key, value in msg.items():
            total += _chars_of(value)
    return total


def measure_tools_chars(tools: list[dict[str, Any]] | None) -> int:
    if not tools:
        return 0
    return _chars_of(tools)


def estimate_context_usage(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    limit_tokens: int | None = None,
) -> dict[str, Any]:
    """返回 used/limit/ratio 与短进度条文案。"""
    limit = limit_tokens or DEFAULT_CONTEXT_TOKENS
    msg_chars = measure_messages_chars(messages)
    tool_chars = measure_tools_chars(tools)
    used = estimate_tokens_from_chars(msg_chars + tool_chars)
    ratio = min(1.0, used / limit) if limit > 0 else 0.0
    return {
        "used": used,
        "limit": limit,
        "ratio": ratio,
        "msg_chars": msg_chars,
        "tool_chars": tool_chars,
        "bar": format_context_bar(ratio),
        "label": format_context_label(used, limit, ratio),
    }


def format_context_bar(ratio: float, *, width: int = 8) -> str:
    filled = int(round(max(0.0, min(1.0, ratio)) * width))
    filled = min(width, max(0, filled))
    # ASCII：Windows 纯终端也能显示
    return "#" * filled + "-" * (width - filled)


def format_context_label(used: int, limit: int, ratio: float) -> str:
    def _k(n: int) -> str:
        if n >= 1000:
            return f"{n / 1000:.0f}k"
        return str(n)

    pct = int(round(ratio * 100))
    return f"ctx {format_context_bar(ratio)} {_k(used)}/{_k(limit)} {pct}%"
