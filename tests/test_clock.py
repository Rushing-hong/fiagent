"""Tests for live clock injection."""

from __future__ import annotations

from pathlib import Path

from core.context import AgentContext


def test_with_clock_for_api_inserts_before_last_user(tmp_path: Path):
    # minimal ctx — use project root if skills exist
    root = Path(__file__).resolve().parents[1]
    ctx = AgentContext(root)
    msgs = [
        {"role": "system", "content": "base"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "现在几点"},
    ]
    out = ctx.with_clock_for_api(msgs)
    assert out is not msgs
    assert msgs[-1]["content"] == "现在几点"  # original untouched
    # clock system sits immediately before last user
    assert out[-1]["role"] == "user"
    assert out[-2]["role"] == "system"
    assert "系统实时时钟" in out[-2]["content"]
    assert out[0]["content"] == "base"


def test_build_time_context_mentions_tool():
    root = Path(__file__).resolve().parents[1]
    ctx = AgentContext(root)
    text = ctx.build_time_context()
    assert "get_current_time" in text
    assert "现在：" in text
