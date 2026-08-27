"""Tests for live clock injection and cache-friendly time context."""

from __future__ import annotations

import time
from pathlib import Path

from core.context import AgentContext


def test_with_clock_for_api_appends_trailing_user(tmp_path: Path):
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
    assert msgs[-1]["content"] == "现在几点"
    assert out[-1]["role"] == "user"
    assert "系统实时时钟" in out[-1]["content"]
    assert out[-2]["content"] == "现在几点"
    assert out[0]["content"] == "base"


def test_build_time_context_day_level_stable():
    root = Path(__file__).resolve().parents[1]
    ctx = AgentContext(root)
    text = ctx.build_time_context()
    assert "get_current_time" in text
    assert "今天：" in text
    assert "%H:%M:%S" not in text
    t1 = ctx.build_time_context()
    time.sleep(0.05)
    t2 = ctx.build_time_context()
    assert t1 == t2


def test_sync_system_message_skips_when_unchanged():
    root = Path(__file__).resolve().parents[1]
    ctx = AgentContext(root)
    msgs = ctx.fresh_messages()
    before = msgs[0]["content"]
    same = ctx.sync_system_message(msgs)
    assert same is msgs
    assert msgs[0]["content"] == before
