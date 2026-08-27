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


def test_slim_strips_session_only_collaboration_metadata():
    messages = [{
        "role": "assistant",
        "content": "协作结论",
        "_fiagent": {
            "collaboration": {"run_id": "run123", "mode": "research"},
        },
    }]

    out = slim_messages_for_api(messages)

    assert out == [{"role": "assistant", "content": "协作结论"}]
    assert "_fiagent" in messages[0]


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


def test_slim_deduplicates_only_identical_readonly_results():
    body = "large market payload " + ("Z" * 2000)
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "r1",
                "type": "function",
                "function": {"name": "read", "arguments": '{"path":"a"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "r1", "content": body},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "r2",
                "type": "function",
                "function": {"name": "read", "arguments": '{"path":"a"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "r2", "content": body},
    ]

    out = slim_messages_for_api(messages, is_readonly=lambda name: name == "read")
    results = [m["content"] for m in out if m.get("role") == "tool"]

    assert results[0] == body
    assert results[1].startswith("[重复只读结果")
    assert messages[3]["content"] == body


def test_slim_does_not_deduplicate_write_results():
    body = "same write result"
    messages = []
    for call_id in ("w1", "w2"):
        messages.extend([
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": "write", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": call_id, "content": body},
        ])

    out = slim_messages_for_api(messages, is_readonly=lambda name: name != "write")
    assert [m["content"] for m in out if m.get("role") == "tool"] == [body, body]


def test_content_risk_recovery_compacts_only_oversized_tool_results():
    from core.message_sanitize import compact_tool_messages_for_content_risk

    long_result = "H" * 7_000 + "T" * 3_000
    messages = [
        {"role": "user", "content": "keep user text"},
        {"role": "tool", "tool_call_id": "short", "content": "short result"},
        {"role": "tool", "tool_call_id": "long", "content": long_result},
    ]

    compacted = compact_tool_messages_for_content_risk(messages, max_chars=2_000)

    assert compacted is not messages
    assert compacted[0] == messages[0]
    assert compacted[1] == messages[1]
    assert len(compacted[2]["content"]) < len(long_result)
    assert compacted[2]["content"].startswith("H" * 500)
    assert compacted[2]["content"].endswith("T" * 500)
    assert "内容风控恢复模式省略" in compacted[2]["content"]
    assert messages[2]["content"] == long_result
