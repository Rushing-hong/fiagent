"""Textual 全屏 TUI：鼠标点击折叠/展开。"""

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.widgets import Collapsible, Footer, Header, Rule, Static

from core.context import AgentContext
from core.loop import run_agent_turn
from core.turn_control import TurnAborted, turn_control
from hooks.registry import HookRegistry
from session import SessionInfo, SessionStore
from ui import ui
from ui import TOOL_MAX_LINES
from ui.collapse import first_line_summary, reasoning_summary
from ui.prefs import get_thinking_mode, ui_mode_label
from ui.tui.widgets import ChatScroll, PromptTextArea

if TYPE_CHECKING:
    from openai import OpenAI

_TCSS = Path(__file__).resolve().parent / "tui.tcss"
_STREAM_INTERVAL = 0.12
_STREAM_TAIL_CHARS = 3200
_LIVE_STRIP_TAIL_CHARS = 1200
_SCROLL_TAIL = 2


def _plain_static(content: str, *, classes: str = "") -> Static:
    """Plain text Static — avoids MarkupError on `[` `]` in tool/LLM output."""
    return Static(content, classes=classes, markup=False)


def _live_tail_text(text: str, *, cursor: bool) -> str:
    if len(text) > _LIVE_STRIP_TAIL_CHARS:
        text = (
            f"…（上文省略 {len(text) - _LIVE_STRIP_TAIL_CHARS} 字）\n"
            f"{text[-_LIVE_STRIP_TAIL_CHARS:]}"
        )
    if cursor:
        text += " ▌"
    return text


class FiagentApp(App):
    """fiagent Textual 界面。"""

    CSS_PATH = _TCSS
    TITLE = "fiagent"
    BINDINGS = [
        Binding("escape", "request_pause", "Pause", show=True),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    def __init__(
        self,
        *,
        client: OpenAI,
        ctx: AgentContext,
        hooks: HookRegistry,
        store: SessionStore,
        messages: list,
        current: SessionInfo | None,
        loaded_hooks: list[str],
        session_commands: dict[str, str],
        handle_command,
    ) -> None:
        super().__init__()
        self.client = client
        self.ctx = ctx
        self.hooks = hooks
        self.store = store
        self.messages = messages
        self.current = current
        self.loaded_hooks = loaded_hooks
        self.session_commands = session_commands
        self.handle_command = handle_command
        self._busy = False
        self._paused = False
        self._turn_lock = threading.Lock()
        self.thinking_mode = get_thinking_mode()
        self._activity: Static | None = None
        self._think_root: Collapsible | None = None
        self._think_body: Static | None = None
        self._think_pending = ""
        self._think_last_flush = 0.0
        self._think_flush_timer = None
        self._think_tick_timer = None
        self._think_pulse = 0
        self._stream_root: Container | None = None
        self._stream_body: Static | None = None
        self._stream_pending = ""
        self._stream_last_flush = 0.0
        self._stream_last_len = 0
        self._stream_flush_timer = None
        self._stream_tick_timer = None
        self._stream_pulse = 0
        self._stream_follow = True
        self._activity_text = ""
        self._activity_last = 0.0
        self._last_thought_text = ""
        self._last_thought_key = ""
        self._llm_round_idx = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield ChatScroll(id="chat")
        yield Static("", id="live-strip", markup=False)
        yield Static("就绪", id="status-bar")
        yield PromptTextArea(
            id="prompt",
            placeholder="Message  /help  ·  Enter 发送  Shift+Enter 换行",
            classes="prompt-input",
        )
        yield Footer()

    def on_mount(self) -> None:
        ui.bind_tui(self)
        turn_control.set_tui_mode(True)
        self._post_startup()
        self.query_one("#prompt", PromptTextArea).focus()

    def on_unmount(self) -> None:
        ui.unbind_tui()
        turn_control.set_tui_mode(False)

    def _chat(self) -> ChatScroll:
        return self.query_one("#chat", ChatScroll)

    def _status(self) -> Static:
        return self.query_one("#status-bar", Static)

    def _live_strip(self) -> Static:
        return self.query_one("#live-strip", Static)

    def _hide_live_strip(self) -> None:
        strip = self._live_strip()
        strip.remove_class("-active")
        strip.update("")

    def _fold_preview(self, text: str, *, max_lines: int = TOOL_MAX_LINES) -> str:
        """TUI 折叠块预览：避免把数万字塞进 Static 拖垮布局。"""
        from ui.collapse import collapse_output, max_chars_for_lines

        try:
            width = max(40, self.size.width - 8)
        except Exception:
            width = 80
        max_chars = max_chars_for_lines(max_lines, width)
        collapsed = collapse_output(text, max_lines, max_chars)
        body = collapsed.text
        if collapsed.overflow:
            body += f"\n\n…（共 {len(text)} 字，界面仅预览前 {max_lines} 行）"
        return body

    def _is_near_bottom(self) -> bool:
        chat = self._chat()
        try:
            return chat.scroll_y >= max(0, chat.max_scroll_y - _SCROLL_TAIL)
        except Exception:
            return True

    def _scroll_bottom(self) -> None:
        self._chat().scroll_end(animate=False)

    def _scroll_bottom_if_pinned(self) -> None:
        """OpenCode stickyScroll：仅在用户未上滑时跟底。"""
        if self._stream_body is not None and not self._stream_follow:
            return
        if self._stream_body is not None and not self._is_near_bottom():
            self._stream_follow = False
            return
        self._scroll_bottom()

    @on(events.MouseScrollUp)
    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if self._stream_body is not None:
            self._stream_follow = False

    @on(events.MouseScrollDown)
    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if self._stream_body is not None and self._is_near_bottom():
            self._stream_follow = True

    def clear_chat(self) -> None:
        self._chat().remove_children()

    def _post_startup(self) -> None:
        skills = [s.name for s in self.ctx.skills.all()]
        self.mount_startup(
            session_id=self.current.id if self.current else None,
            session_title=self.current.title if self.current else "新对话",
            skills=skills,
            hook_count=len(self.loaded_hooks),
            current_time=self.ctx.format_now(),
            thinking_mode=self.thinking_mode,
            ui_mode="tui",
        )

    # --- mount helpers ---

    def mount_startup(
        self,
        *,
        session_id: str | None,
        session_title: str,
        skills: list[str],
        hook_count: int,
        current_time: str,
        thinking_mode: str,
        ui_mode: str = "tui",
    ) -> None:
        sid = f"[cyan]{session_id}[/]" if session_id else "[dim]新对话[/]"
        think = "展开" if thinking_mode == "show" else "折叠"
        skill_n = len(skills)
        body = (
            f"[bold #58a6ff]fi[/][bold #d2a8ff]agent[/]  "
            f"[dim]quant research assistant[/]\n\n"
            f"[dim]时间[/]  {current_time}\n"
            f"[dim]会话[/]  {sid}  {session_title}\n"
            f"[dim]技能[/]  {skill_n} 个  ·  [dim]Hooks[/]  {hook_count}  ·  "
            f"[dim]思考[/]  {think}  ·  [dim]界面[/]  {ui_mode_label(ui_mode)}"
        )
        card = Static(body, classes="card-startup")
        self._chat().mount(card)

        if skill_n:
            preview = ", ".join(skills[:8])
            if skill_n > 8:
                preview += f" … +{skill_n - 8}"
            self.mount_foldable(
                f"Skills ({skill_n})",
                preview,
                kind="tool",
                collapsed=True,
            )

        self.mount_line(
            "点击折叠标题展开/收起  ·  Esc 暂停  ·  /plain 切换纯终端",
            classes="card-hint",
        )
        self._scroll_bottom()

    def mount_sessions(self, sessions) -> None:
        if not sessions:
            self.mount_line("暂无 session", classes="line-warn")
            return
        rows = []
        for s in sessions:
            ts = s.updated_at[:19].replace("T", " ")
            rows.append(
                f"[cyan]{s.id}[/]  [bold]{s.title}[/]  "
                f"[dim]{s.message_count} 条 · {ts}[/]"
            )
        self.mount_foldable(
            f"Sessions ({len(sessions)})",
            "\n".join(rows),
            kind="tool",
            collapsed=False,
            body_markup=True,
        )

    def mount_line(self, text: str, *, classes: str = "line-info") -> None:
        self._chat().mount(Static(text, classes=classes))
        self._scroll_bottom()

    def mount_rule(self, title: str) -> None:
        if title:
            self._chat().mount(Static(title, classes="round-rule-label"))
        self._chat().mount(Rule.horizontal(classes="round-rule"))
        self._scroll_bottom()

    def mount_foldable(
        self,
        title: str,
        body: str,
        *,
        kind: str = "tool",
        collapsed: bool = True,
        body_markup: bool = False,
    ) -> None:
        body_classes = "fold-body"
        if body.lstrip().startswith(("{", "[")):
            body_classes += " -code"
        body_widget = (
            Static(body, classes=body_classes, markup=True)
            if body_markup
            else _plain_static(body, classes=body_classes)
        )
        widget = Collapsible(
            body_widget,
            title=title,
            collapsed=collapsed,
            classes=f"fold-{kind}",
        )
        self._chat().mount(widget)
        self._scroll_bottom()

    def mount_user(self, content: str, *, collapsed: bool = False) -> None:
        if collapsed and len(content) >= 200:
            first = content.strip().splitlines()[0][:48]
            from rich.markup import escape

            self.mount_foldable(f"You: {escape(first)}", content, kind="user", collapsed=True)
            return
        from rich.markup import escape
        self._chat().mount(
            Static(f"[bold #3fb950]You[/]  {escape(content)}", classes="line-user")
        )
        self._scroll_bottom()

    def reset_turn_ui(self) -> None:
        self._last_thought_text = ""
        self._last_thought_key = ""
        ui._tui_stream_pending = ""
        ui._tui_think_pending = ""

    def llm_round_start(self, round_idx: int) -> None:
        self._llm_round_idx = round_idx
        self._clear_activity()
        self.set_status(f"[dim]第 {round_idx} 轮推理…[/]")

    def llm_activity_update(self, text: str) -> None:
        if self._activity is None:
            return
        now = time.monotonic()
        if text == self._activity_text and now - self._activity_last < 0.2:
            return
        self._activity_text = text
        self._activity_last = now
        self._activity.update(f"[dim italic]{text}[/]")

    def llm_activity_clear(self) -> None:
        self._clear_activity()

    def _clear_activity(self) -> None:
        if self._activity is not None:
            self._activity.remove()
            self._activity = None

    def _cancel_stream_tick(self) -> None:
        if self._stream_tick_timer is not None:
            self._stream_tick_timer.stop()
            self._stream_tick_timer = None

    def _start_stream_tick(self) -> None:
        self._cancel_stream_tick()
        self._stream_pulse = 0
        self._stream_tick_timer = self.set_interval(
            _STREAM_INTERVAL, self._on_stream_tick
        )

    def _on_stream_tick(self) -> None:
        if self._stream_body is None:
            return
        self._stream_pending = ui._tui_stream_pending
        self._flush_stream_live()

    def _cancel_think_tick(self) -> None:
        if self._think_tick_timer is not None:
            self._think_tick_timer.stop()
            self._think_tick_timer = None

    def _start_think_tick(self) -> None:
        self._cancel_think_tick()
        self._think_pulse = 0
        self._think_tick_timer = self.set_interval(
            _STREAM_INTERVAL, self._on_think_tick
        )

    def _on_think_tick(self) -> None:
        if self._think_body is None:
            return
        self._think_pending = ui._tui_think_pending
        self._flush_think_live()

    def _cancel_stream_timer(self) -> None:
        self._cancel_stream_tick()
        if self._stream_flush_timer is not None:
            self._stream_flush_timer.stop()
            self._stream_flush_timer = None

    def _cancel_think_timer(self) -> None:
        self._cancel_think_tick()
        if self._think_flush_timer is not None:
            self._think_flush_timer.stop()
            self._think_flush_timer = None

    def _stream_tail_text(self, text: str, *, cursor: bool) -> str:
        if len(text) > _STREAM_TAIL_CHARS:
            text = f"…（上文省略 {len(text) - _STREAM_TAIL_CHARS} 字）\n{text[-_STREAM_TAIL_CHARS:]}"
        if cursor:
            text += " ▌"
        return text

    def stream_thinking_begin(self) -> None:
        """思考流式：可折叠标题 + 正文，默认收起（无大黄框）。"""
        self._clear_activity()
        self._cancel_think_timer()
        self._think_pending = ""
        self._think_last_flush = 0.0
        self._think_last_body_len = -1
        r = self._llm_round_idx
        collapsed = not (ui.verbose or self.thinking_mode == "show")
        self._think_body = _plain_static("", classes="fold-body")
        self._think_root = Collapsible(
            self._think_body,
            title=f"Thought R{r}  思考中…",
            collapsed=collapsed,
            classes="fold-think",
        )
        self._chat().mount(self._think_root)
        self._scroll_bottom()
        self._start_think_tick()
        self.set_status("[#d29922]思考中…[/]")

    def _flush_think_live(self) -> None:
        if self._think_body is None:
            return
        n = len(self._think_pending)
        expanded = self._think_root is not None and not self._think_root.collapsed
        if expanded and n != getattr(self, "_think_last_body_len", -1):
            self._think_body.update(self._stream_tail_text(self._think_pending, cursor=True))
            self._think_last_body_len = n
        self._think_last_flush = time.monotonic()
        self._think_pulse = (self._think_pulse + 1) % 3
        dots = "." * (self._think_pulse + 1)
        if self._think_root is not None:
            self._think_root.title = f"Thought R{self._llm_round_idx}  思考中 · {n} 字"
        self.set_status(f"[#d29922]思考中 · {n} 字{dots}[/]")

    def stream_thinking_update(self, text: str) -> None:
        self._think_pending = text
        if self._think_body is None:
            return
        if self._think_last_flush == 0.0:
            self._flush_think_live()

    def stream_thinking_end(self, text: str) -> None:
        self._cancel_think_timer()
        root = self._think_root
        body = self._think_body
        self._think_root = None
        self._think_body = None
        self._think_pending = text
        round_idx = self._llm_round_idx

        if not text.strip():
            if root and root.is_attached:
                root.remove()
            return

        key = f"R{round_idx}:{len(text)}:{text[:96]}"
        if key == self._last_thought_key:
            if root and root.is_attached:
                root.remove()
            return

        from rich.markup import escape

        title_part, _ = reasoning_summary(text)
        label = title_part or first_line_summary(text)
        lines = text.count("\n") + 1 if text else 0
        header = f"Thought R{round_idx}  {escape(label)}  ({lines} lines)"

        if root and body and root.is_attached and body.is_attached:
            root.title = header
            body.update(text)
            self._last_thought_key = key
            self._last_thought_text = text
            self._scroll_bottom()
            return

        if root and root.is_attached:
            root.remove()
        self._last_thought_key = key
        self._last_thought_text = text
        self.tui_show_thinking(text, round_idx=round_idx)

    def stream_reply_begin(self) -> None:
        """Assistant 流式：底部 live-strip 预览，不触发布局整个聊天区。"""
        self._cancel_stream_timer()
        self._stream_last_flush = 0.0
        self._stream_last_len = 0
        self._stream_follow = True
        self._stream_pending = ui._tui_stream_pending
        self._stream_root = None
        strip = self._live_strip()
        strip.add_class("-active")
        self._stream_body = strip
        self._start_stream_tick()
        self.set_status("[#58a6ff]输出中…[/]")
        if self._stream_pending:
            self._flush_stream_live()

    def _flush_stream_live(self) -> None:
        if self._stream_body is None:
            return
        self._stream_pending = ui._tui_stream_pending
        n = len(self._stream_pending)
        self._stream_pulse = (self._stream_pulse + 1) % 3
        dots = "." * (self._stream_pulse + 1)
        self.set_status(f"[#58a6ff]输出中 · {n} 字{dots}[/]")
        if n == self._stream_last_len:
            return
        self._stream_last_len = n
        self._stream_body.update(_live_tail_text(self._stream_pending, cursor=True))
        self._stream_last_flush = time.monotonic()

    def stream_reply_update(self, content: str) -> None:
        self._stream_pending = content
        if self._stream_body is None:
            return
        if self._stream_last_flush == 0.0:
            self._flush_stream_live()

    def stream_reply_end(self, content: str) -> None:
        self._cancel_stream_timer()
        follow = self._stream_follow
        self._stream_root = None
        self._stream_body = None
        self._stream_pending = content
        self._hide_live_strip()
        if content:
            self.mount_reply(content)
            if follow:
                self._scroll_bottom()

    def stream_reply_cancel(self) -> None:
        self._cancel_stream_timer()
        if self._stream_root is not None:
            self._stream_root.remove()
        self._stream_root = None
        self._stream_body = None
        self._stream_pending = ""
        self._hide_live_strip()

    def mount_reply(self, content: str) -> None:
        self._chat().mount(
            Container(
                Static("[bold #79c0ff]Assistant[/]", classes="reply-label"),
                _plain_static(content, classes="stream-md stream-plain"),
                classes="reply-block",
            )
        )
        self._scroll_bottom()

    def tui_expand_slot(self, slot: int = 1) -> None:
        items = list(self.query(Collapsible))
        if not items:
            self.mount_line("没有可展开的内容", classes="line-warn")
            return
        if slot < 1 or slot > len(items):
            self.mount_line(f"无效序号 [{slot}]", classes="line-warn")
            return
        items[-slot].collapsed = False
        self._scroll_bottom()

    def set_status(self, text: str) -> None:
        self._status().update(text)

    def set_busy(self, text: str) -> None:
        self._paused = False
        self._busy = True
        self.set_status(f"[#58a6ff]{text}[/]")
        inp = self.query_one("#prompt", PromptTextArea)
        inp.disabled = True
        inp.add_class("-disabled")

    def set_paused(self) -> None:
        """暂停时恢复输入框，便于输入 resume / abort。"""
        self._paused = True
        self._busy = False
        self.set_status("[#d29922]已暂停 · Esc 继续 · 或输入 resume / abort[/]")
        inp = self.query_one("#prompt", PromptTextArea)
        inp.disabled = False
        inp.remove_class("-disabled")
        inp.focus()

    def set_idle(self) -> None:
        self._paused = False
        self._busy = False
        if self._stream_body is None and self._think_body is None:
            self.set_status("[dim]就绪 · 点击标题展开/收起[/]")
        inp = self.query_one("#prompt", PromptTextArea)
        inp.disabled = False
        inp.remove_class("-disabled")
        inp.focus()

    def tui_show_thinking(self, text: str, *, round_idx: int | None = None) -> None:
        from rich.markup import escape

        collapsed = not (ui.verbose or self.thinking_mode == "show")
        title_part, _ = reasoning_summary(text)
        label = title_part or first_line_summary(text)
        lines = text.count("\n") + 1 if text else 0
        r = round_idx if round_idx is not None else self._llm_round_idx
        header = f"Thought R{r}  {escape(label)}  ({lines} lines)"
        self.mount_foldable(header, text, kind="think", collapsed=collapsed)

    def tui_show_tool_call(self, name: str, args_text: str) -> None:
        if not args_text.strip() or args_text.strip() == "{}":
            self.mount_line(f"  > {name}", classes="line-tool")
            return
        preview = self._fold_preview(args_text, max_lines=6)
        self.mount_foldable(f"> {name}", preview, kind="tool", collapsed=True)

    def tui_show_tool_result(self, name: str, result: str) -> None:
        if name in ui.INLINE_TOOLS and not ui.verbose and len(result) < 400:
            from rich.markup import escape

            preview = escape(result.strip().splitlines()[0][:72])
            self.mount_line(f"  ok {name}  [dim]{preview}[/]", classes="line-tool")
            return
        preview = self._fold_preview(result)
        self.mount_foldable(f"ok {name}  ({len(result)} chars)", preview, kind="tool", collapsed=True)

    def cancel_active_streams(self) -> None:
        self.stream_reply_cancel()
        self._cancel_think_timer()
        if self._think_root is not None:
            self._think_root.remove()
        self._think_root = None
        self._think_body = None

    def action_request_pause(self) -> None:
        if self._paused:
            turn_control.request_resume()
            self.set_busy("继续中…")
            return
        if not self._busy:
            return
        turn_control.request_pause()
        self.set_paused()
        self.mount_line("已暂停 — 输入 resume 继续 / abort 中止", classes="line-warn")

    def _handle_pause_command(self, text: str) -> bool:
        low = text.lower()
        if low in ("resume", "r", "continue", "c", "/resume"):
            turn_control.request_resume()
            self.set_busy("继续中…")
            return True
        if low in ("abort", "a", "/abort", "stop"):
            turn_control.request_abort()
            self.set_busy("中止中…")
            return True
        return False

    @on(PromptTextArea.Submitted, "#prompt")
    def on_prompt(self, event: PromptTextArea.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if self._paused:
            if self._handle_pause_command(text):
                return
            self.mount_line("已暂停，请输入 resume 或 abort", classes="line-warn")
            return
        if self._busy:
            return
        if text.lower() in ("quit", "exit", "q"):
            self.exit()
            return
        threading.Thread(target=self._run_turn, args=(text,), daemon=True).start()

    def _run_turn(self, user_input: str) -> None:
        with self._turn_lock:
            self.call_from_thread(self.set_busy, "处理中…")
            try:
                self._process_input(user_input)
            finally:
                self.call_from_thread(self.set_idle)

    def _process_input(self, user_input: str) -> None:
        if user_input.startswith("/"):
            from core.commands import HANDLED_RESTART

            current, new_messages, handled = self.handle_command(
                user_input, self.store, self.ctx, self.current
            )
            self.current = current
            if handled == HANDLED_RESTART:
                self.call_from_thread(self.exit)
                return
            if new_messages is not None:
                self.messages = new_messages
                self.call_from_thread(self.clear_chat)
                self.call_from_thread(self._post_startup)
            elif handled and user_input.strip().lower() == "/reload":
                self.ctx.sync_system_message(self.messages)
            elif not handled:
                self.call_from_thread(
                    ui.warn, f"未知命令: {user_input}，输入 /help 查看帮助"
                )
            return

        turn_ctx = self.hooks.emit("turn.start", {
            "input": user_input,
            "session_id": self.current.id if self.current else None,
        })
        if turn_ctx.cancel:
            self.call_from_thread(ui.hook_blocked)
            return
        user_input = turn_ctx.get("input", user_input)

        turn_start = len(self.messages)
        self.call_from_thread(ui.show_user_message, user_input)
        self.messages.append({"role": "user", "content": user_input})

        try:
            run_agent_turn(self.client, self.messages, self.ctx, self.hooks)
            if self.current is None:
                self.current = self.store.create()
                self.store.auto_title(self.current.id, user_input)
                updated = self.store.get(self.current.id)
                if updated:
                    self.current = updated
            self.store.save_messages(self.current.id, self.messages)
            self.hooks.emit("turn.end", {
                "input": user_input,
                "messages": self.messages,
                "session_id": self.current.id,
            })
        except TurnAborted:
            del self.messages[turn_start:]
            self.call_from_thread(self.cancel_active_streams)
            self.call_from_thread(ui.warn, "本轮已中止，对话未保存本轮内容")
        except Exception as exc:
            self.call_from_thread(ui.error, str(exc))
            if self.messages and self.messages[-1].get("role") == "user":
                self.messages.pop()


def run_tui(
    *,
    client: OpenAI,
    ctx: AgentContext,
    hooks: HookRegistry,
    store: SessionStore,
    messages: list,
    current: SessionInfo | None,
    loaded_hooks: list[str],
    session_commands: dict[str, str],
    handle_command,
    args: argparse.Namespace,
) -> None:
    app = FiagentApp(
        client=client,
        ctx=ctx,
        hooks=hooks,
        store=store,
        messages=messages,
        current=current,
        loaded_hooks=loaded_hooks,
        session_commands=session_commands,
        handle_command=handle_command,
    )
    try:
        app.run()
    finally:
        hooks.emit("session.end", {
            "session_id": app.current.id if app.current else None,
            "messages": app.messages,
        })
        store.close()
