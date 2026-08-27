from types import SimpleNamespace

import httpx
import pytest

from core.stream import is_transient_stream_error


def test_transient_chunked_read():
    assert is_transient_stream_error(
        RuntimeError("peer closed connection without sending complete message body (incomplete chunked read)")
    )


def test_exc_detail_empty_message():
    from core.stream import _exc_detail
    assert "RemoteProtocolError" in _exc_detail(httpx.RemoteProtocolError(""))


def test_non_stream_fallback_used_on_transient_error(monkeypatch):
    from core import stream as sm

    calls = {"stream": 0, "plain": 0}

    class _FakeStream:
        def __iter__(self):
            raise httpx.RemoteProtocolError("incomplete chunked read")

    class _FakeCompletions:
        def create(self, **kwargs):
            if kwargs.get("stream"):
                calls["stream"] += 1
                return _FakeStream()
            calls["plain"] += 1
            assert "stream_options" not in kwargs
            msg = SimpleNamespace(
                content="ok-fallback",
                reasoning_content=None,
                tool_calls=None,
            )
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        base_url = "https://api.deepseek.com"
        chat = _FakeChat()

    monkeypatch.setattr(sm, "get_model", lambda: "deepseek-v4-pro")
    monkeypatch.setattr(sm, "get_reasoning_effort", lambda: "off")
    monkeypatch.setattr(
        sm,
        "get_model_spec",
        lambda _m: SimpleNamespace(
            provider="deepseek",
            api_model="m",
            label="M",
            thinking="none",
            supports_thinking=False,
        ),
    )
    monkeypatch.setattr(sm, "get_client_for_model", lambda _m: _FakeClient())
    monkeypatch.setattr(sm, "_MAX_STREAM_ATTEMPTS", 1)

    out = sm.stream_chat_completion(
        None,
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        round_idx=1,
        quiet_ui=True,
    )
    assert out.content == "ok-fallback"
    assert calls["stream"] == 0
    assert calls["plain"] == 1


def test_quiet_content_risk_retries_without_thinking(monkeypatch):
    from core import stream as sm

    calls: list[dict] = []

    class _FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if kwargs.get("reasoning_effort") == "high":
                raise RuntimeError("Error code: 400 - Content Exists Risk")
            msg = SimpleNamespace(
                content="ok-without-thinking",
                reasoning_content=None,
                tool_calls=None,
            )
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        base_url = "https://api.deepseek.com"
        chat = _FakeChat()

    monkeypatch.setattr(sm, "get_model", lambda: "deepseek-v4-flash")
    monkeypatch.setattr(sm, "get_reasoning_effort", lambda: "high")
    monkeypatch.setattr(
        sm,
        "get_model_spec",
        lambda _m: SimpleNamespace(
            provider="deepseek",
            api_model="m",
            label="M",
            thinking="deepseek",
            supports_thinking=True,
        ),
    )
    monkeypatch.setattr(sm, "get_client_for_model", lambda _m: _FakeClient())

    out = sm.stream_chat_completion(
        None,
        messages=[{"role": "user", "content": "分析公司"}],
        tools=None,
        round_idx=1,
        quiet_ui=True,
    )

    assert out.content == "ok-without-thinking"
    assert len(calls) == 2
    assert calls[0]["reasoning_effort"] == "high"
    assert calls[1]["extra_body"] == {"thinking": {"type": "disabled"}}


def test_quiet_content_risk_recovers_with_compacted_tool_results(monkeypatch):
    from core import stream as sm

    calls: list[dict] = []

    class _FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            tool_content = next(
                (
                    message.get("content", "")
                    for message in kwargs["messages"]
                    if message.get("role") == "tool"
                ),
                "",
            )
            if len(tool_content) > 6_500:
                raise RuntimeError("Error code: 400 - Content Exists Risk")
            msg = SimpleNamespace(
                content="ok-after-compaction",
                reasoning_content=None,
                tool_calls=None,
            )
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        base_url = "https://api.deepseek.com"
        chat = _FakeChat()

    monkeypatch.setattr(sm, "get_model", lambda: "deepseek-v4-flash")
    monkeypatch.setattr(sm, "get_reasoning_effort", lambda: "high")
    monkeypatch.setattr(
        sm,
        "get_model_spec",
        lambda _m: SimpleNamespace(
            provider="deepseek",
            api_model="m",
            label="M",
            thinking="deepseek",
            supports_thinking=True,
        ),
    )
    monkeypatch.setattr(sm, "get_client_for_model", lambda _m: _FakeClient())

    out = sm.stream_chat_completion(
        None,
        messages=[
            {"role": "user", "content": "分析市场"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_limit_board", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "X" * 10_000},
        ],
        tools=None,
        round_idx=2,
        quiet_ui=True,
    )

    assert out.content == "ok-after-compaction"
    assert len(calls) == 3
    assert len(calls[0]["messages"][-1]["content"]) == 10_000
    assert len(calls[1]["messages"][-1]["content"]) == 10_000
    assert len(calls[2]["messages"][-1]["content"]) < 6_500
    assert "内容风控恢复模式省略" in calls[2]["messages"][-1]["content"]


def test_stream_fallback_after_transient_error_when_not_quiet(monkeypatch):
    from core import stream as sm

    calls = {"stream": 0, "plain": 0}

    class _FakeStream:
        def __iter__(self):
            raise httpx.RemoteProtocolError("incomplete chunked read")

    class _FakeCompletions:
        def create(self, **kwargs):
            if kwargs.get("stream"):
                calls["stream"] += 1
                return _FakeStream()
            calls["plain"] += 1
            msg = SimpleNamespace(
                content="ok-fallback",
                reasoning_content=None,
                tool_calls=None,
            )
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    monkeypatch.setattr(sm, "get_model", lambda: "deepseek-v4-pro")
    monkeypatch.setattr(sm, "get_reasoning_effort", lambda: "off")
    monkeypatch.setattr(
        sm,
        "get_model_spec",
        lambda _m: SimpleNamespace(
            api_model="m",
            label="M",
            thinking="none",
            supports_thinking=False,
        ),
    )
    monkeypatch.setattr(sm, "get_client_for_model", lambda _m: _FakeClient())
    monkeypatch.setattr(sm, "_MAX_STREAM_ATTEMPTS", 1)

    out = sm.stream_chat_completion(
        None,
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        round_idx=1,
        quiet_ui=False,
    )
    assert out.content == "ok-fallback"
    assert calls["stream"] >= 1
    assert calls["plain"] == 1


def test_deterministic_400_does_not_repeat_as_non_stream(monkeypatch):
    from core import stream as sm

    calls = {"stream": 0, "plain": 0}

    class _FakeCompletions:
        def create(self, **kwargs):
            if kwargs.get("stream"):
                calls["stream"] += 1
            else:
                calls["plain"] += 1
            raise RuntimeError("Error code: 400 - invalid_request_error")

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    monkeypatch.setattr(sm, "get_model", lambda: "deepseek-v4-pro")
    monkeypatch.setattr(sm, "get_reasoning_effort", lambda: "off")
    monkeypatch.setattr(
        sm,
        "get_model_spec",
        lambda _m: SimpleNamespace(
            api_model="m",
            label="M",
            thinking="none",
            supports_thinking=False,
        ),
    )
    monkeypatch.setattr(sm, "get_client_for_model", lambda _m: _FakeClient())
    monkeypatch.setattr(sm.ui, "error", lambda _message: None)

    with pytest.raises(sm.LlmStreamError, match="invalid_request_error"):
        sm.stream_chat_completion(
            None,
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            round_idx=1,
            quiet_ui=False,
        )

    assert calls == {"stream": 1, "plain": 0}
