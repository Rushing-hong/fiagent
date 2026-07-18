"""Tests for API-side message slim (no session mutation)."""

from __future__ import annotations

from core.message_sanitize import slim_messages_for_api


def test_slim_strips_reasoning_keeps_full_tool_body():
    big = "X" * 5000
    messages = [
        {"role": "system", "content": "sys"},
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "secret-thoughts",
            "tool_calls": [{
                "id": "c1",
                "type": "function",
                "function": {"name": "screen_market", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "c1", "content": big},
        {"role": "assistant", "content": "done", "reasoning_content": "more"},
    ]
    out = slim_messages_for_api(messages, is_readonly=lambda n: True)
    assert all("reasoning_content" not in m for m in out)
    tool = next(m for m in out if m.get("role") == "tool")
    assert tool["content"] == big
    assert len(messages[2]["content"]) == 5000


def test_slim_keeps_write_tool_full():
    body = "ok " + ("Y" * 2000)
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "w1",
                "type": "function",
                "function": {"name": "write", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "w1", "content": body},
    ]
    out = slim_messages_for_api(messages, is_readonly=lambda n: n != "write")
    tool = next(m for m in out if m.get("role") == "tool")
    assert tool["content"] == body
