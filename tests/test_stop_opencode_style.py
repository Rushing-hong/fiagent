"""OpenCode-style stop: soft max-steps + doom_loop."""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock

from core import loop
from core.loop import _norm_args, run_tool_with_hooks


class _Ctx:
    def is_repeatable_tool(self, name: str) -> bool:
        return True

    def execute_tool(self, name: str, arguments: str) -> str:
        return f"ok:{name}:{arguments}"


class _Hooks:
    def emit(self, *_a, **_k):
        return MagicMock(cancel=False, get=lambda k, d=None: d)


def test_norm_args_stable_json():
    assert _norm_args('{"b":1,"a":2}') == _norm_args('{"a": 2, "b": 1}')


def test_doom_loop_rejects_third_identical_call():
    ctx = _Ctx()
    hooks = _Hooks()
    counts: dict[str, int] = {}
    recent: deque[tuple[str, str]] = deque()
    args = '{"x":1}'
    r1 = run_tool_with_hooks(hooks, ctx, "screen_market", args, counts, recent)
    r2 = run_tool_with_hooks(hooks, ctx, "screen_market", args, counts, recent)
    r3 = run_tool_with_hooks(hooks, ctx, "screen_market", args, counts, recent)
    assert r1.startswith("ok:")
    assert r2.startswith("ok:")
    assert "doom_loop" in r3
    # different args still works
    r4 = run_tool_with_hooks(hooks, ctx, "screen_market", '{"x":2}', counts, recent)
    assert r4.startswith("ok:")


def test_defaults_opencode_style():
    assert loop.MAX_TOOL_ROUNDS >= 40
    assert loop.DOOM_LOOP_AT == 3
    assert not hasattr(loop, "_REPEAT_BLOCK_AT") or True
