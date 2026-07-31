from types import SimpleNamespace

import httpx

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
        quiet_ui=True,
    )
    assert out.content == "ok-fallback"
    assert calls["stream"] == 0
    assert calls["plain"] == 1


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
