"""折叠工具函数（借鉴 OpenCode collapse-tool-output / reasoningSummary）。"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CollapseResult:
    text: str
    overflow: bool


def collapse_output(output: str, max_lines: int, max_chars: int) -> CollapseResult:
    """与 OpenCode collapseToolOutput 一致：按行数/字数截断。"""
    lines = output.split("\n")
    if len(lines) <= max_lines and len(output) <= max_chars:
        return CollapseResult(text=output, overflow=False)

    preview = "\n".join(lines[:max_lines])
    if len(preview) > max_chars:
        return CollapseResult(
            text=preview[: max(0, max_chars - 1)] + "…",
            overflow=True,
        )
    return CollapseResult(text="\n".join([*lines[:max_lines], "…"]), overflow=True)


def max_chars_for_lines(max_lines: int, terminal_width: int) -> int:
    """OpenCode: maxLines * max(20, width - 6)。"""
    return max_lines * max(20, terminal_width - 6)


def reasoning_summary(text: str) -> tuple[str | None, str]:
    """解析 **标题**\\n\\n正文 格式的思考摘要。"""
    content = text.strip()
    match = re.match(r"^\*\*([^*\n]+)\*\*(?:\r?\n\r?\n|$)", content)
    if not match:
        first = content.splitlines()[0][:60] if content else "思考"
        return None, content
    title = match.group(1).strip()
    body = content[match.end() :].strip()
    return title, body


def first_line_summary(text: str, limit: int = 56) -> str:
    title, _ = reasoning_summary(text)
    if title:
        return title
    line = text.strip().splitlines()[0] if text.strip() else "思考"
    if len(line) > limit:
        return line[: limit - 1] + "…"
    return line
