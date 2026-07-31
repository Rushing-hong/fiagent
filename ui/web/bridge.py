"""Frontend bridge that mirrors TUI hooks and fans out SSE events."""

from __future__ import annotations

import re
import threading
from typing import Any, Callable

from ui import ui


class WebBridge:
    """Bound via ``ui.bind_tui`` so existing AgentUI fan-out works unchanged."""

    def __init__(self, emit: Callable[[dict[str, Any]], None]) -> None:
        self._emit = emit
        self.thinking_mode = "hide"
        self._stream_len = 0
        self._think_len = 0
        self._tick: threading.Thread | None = None
        self._stop = threading.Event()

    def call_from_thread(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        fn(*args, **kwargs)

    def start_ticks(self) -> None:
        if self._tick and self._tick.is_alive():
            return
        self._stop.clear()
        self._tick = threading.Thread(target=self._tick_loop, name="web-ui-tick", daemon=True)
        self._tick.start()

    def stop_ticks(self) -> None:
        self._stop.set()

    def _tick_loop(self) -> None:
        while not self._stop.wait(0.12):
            pending = ui._tui_stream_pending
            if pending and len(pending) != self._stream_len:
                self._stream_len = len(pending)
                self._emit({"type": "reply_delta", "text": pending})
            think = ui._tui_think_pending
            if think and len(think) != self._think_len:
                self._think_len = len(think)
                self._emit({"type": "think_delta", "text": think})

    def reset_turn_ui(self) -> None:
        self._stream_len = 0
        self._think_len = 0
        self._emit({"type": "turn_reset"})

    def mount_startup(self, **kwargs: Any) -> None:
        self._emit({"type": "startup", **kwargs})

    def mount_line(self, text: str, *, classes: str = "line-info") -> None:
        self._emit({"type": "line", "text": _strip_markup(text), "classes": classes})

    def mount_rule(self, title: str = "") -> None:
        self._emit({"type": "rule", "title": title})

    def mount_user(self, content: str, *, collapsed: bool = False) -> None:
        self._emit({"type": "user", "text": content, "collapsed": collapsed})

    def mount_reply(self, content: str) -> None:
        self._emit({"type": "reply", "text": content})

    def mount_sessions(self, sessions: list) -> None:
        """Fallback if show_sessions is called — prefer picker shape."""
        items = [
            {
                "id": "__new__",
                "label": "新建对话",
                "hint": "清空上下文",
                "current": False,
            }
        ]
        for s in sessions:
            ts = str(getattr(s, "updated_at", ""))[:19].replace("T", " ")
            items.append(
                {
                    "id": getattr(s, "id", ""),
                    "label": getattr(s, "title", "") or getattr(s, "id", ""),
                    "hint": f"{getattr(s, 'id', '')} · {getattr(s, 'message_count', 0)} 条 · {ts}",
                    "current": False,
                }
            )
        self._emit(
            {
                "type": "picker",
                "kind": "session",
                "title": "Session",
                "hint": "选择要进入的对话 · Esc 关闭",
                "items": items,
            }
        )

    def stream_thinking_begin(self) -> None:
        self._think_len = 0
        self._emit({"type": "think_begin", "collapsed": False})

    def stream_thinking_update(self, text: str) -> None:
        self._think_len = len(text or "")
        self._emit({"type": "think_delta", "text": text or ""})

    def stream_thinking_end(self, text: str) -> None:
        self._think_len = len(text or "")
        # Web：默认展开，避免「模型睡着了」的错觉；用户可手动折叠
        collapsed = False
        if getattr(self, "thinking_mode", "hide") == "hide":
            # hide 仅表示偏好折叠终态，流式过程仍已展开过
            collapsed = False
        self._emit(
            {
                "type": "think_end",
                "text": text or "",
                "chars": len(text or ""),
                "collapsed": collapsed,
            }
        )

    def stream_reply_begin(self) -> None:
        self._stream_len = 0
        self._emit({"type": "reply_begin"})

    def stream_reply_update(self, content: str) -> None:
        self._stream_len = len(content or "")
        self._emit({"type": "reply_delta", "text": content or ""})

    def stream_reply_end(self, content: str) -> None:
        self._stream_len = len(content or "")
        self._emit({"type": "reply_end", "text": content or ""})

    def stream_reply_cancel(self) -> None:
        self._stream_len = 0
        self._emit({"type": "reply_cancel"})

    def tui_show_thinking(self, text: str, *, round_idx: int = 0) -> None:
        self._emit(
            {
                "type": "think_end",
                "text": text or "",
                "round": round_idx,
                "chars": len(text or ""),
                "collapsed": False,
            }
        )

    def tui_show_tool_call(self, label: str, args_text: str = "") -> None:
        self._emit(
            {
                "type": "tool_call",
                "name": label,
                "args": args_text or "",
                "collapsed": False,
            }
        )

    def tui_show_tool_result(self, name: str, result: str) -> None:
        preview = result if len(result) <= 12000 else result[:12000] + "\n…"
        self._emit(
            {
                "type": "tool_result",
                "name": name,
                "text": preview,
                "collapsed": False,
            }
        )

    def show_context_progress(self, usage: dict) -> None:
        self._emit({"type": "context", "usage": usage})

    def llm_round_start(self, round_idx: int) -> None:
        self._emit({"type": "round", "index": round_idx})
        self._emit(
            {
                "type": "activity",
                "text": f"第 {round_idx} 轮 · 连接模型…",
                "busy": True,
            }
        )

    def llm_activity_update(self, text: str) -> None:
        self._emit({"type": "status", "text": text})
        if text:
            self._emit({"type": "activity", "text": text, "busy": True})

    def llm_activity_clear(self) -> None:
        self._emit({"type": "status", "text": ""})
        self._emit({"type": "activity", "text": "", "busy": False})
    def set_busy(self, text: str) -> None:
        self._emit({"type": "busy", "text": text, "busy": True})

    def set_idle(self) -> None:
        self._emit({"type": "busy", "text": "就绪", "busy": False})

    def mount_agent_team(self, payload: dict[str, Any]) -> None:
        self._emit({"type": "agent_team", **payload})

    def mount_help(self, commands: dict[str, str]) -> None:
        items = [{"cmd": k, "desc": v} for k, v in commands.items()]
        self._emit({"type": "help", "items": items})

    def web_list_folds(self) -> None:
        self._emit({"type": "list_folds"})

    def tui_expand_slot(self, slot: int = 1) -> None:
        self._emit({"type": "expand_slot", "slot": int(slot)})


def _strip_markup(text: str) -> str:
    return re.sub(r"\[/?[^\]]*\]", "", text or "")
