"""Textual 全屏 TUI：鼠标点击折叠/展开。"""

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from textual import events, on
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Collapsible, Footer, Header, Rule, Static
from rich.markup import escape as rich_escape
from rich.text import Text
from rich.cells import cell_len, set_cell_size

from core.context import AgentContext
from core.loop import run_agent_turn
from core.turn_control import TurnAborted, turn_control
from hooks.registry import HookRegistry
from session import SessionInfo, SessionStore
from ui import ui
from ui import TOOL_MAX_LINES
from ui.collapse import first_line_summary, reasoning_summary
from ui.prefs import (
    ALWAYS_ON_TOOLS,
    AVAILABLE_EFFORTS,
    AVAILABLE_MODELS,
    EFFORT_LABELS,
    MODEL_LABELS,
    effort_label,
    get_model,
    get_reasoning_effort,
    get_thinking_mode,
    is_mcp_tool_enabled,
    is_skill_enabled,
    is_tool_enabled,
    model_label,
    set_last_session_id,
    set_model,
    set_reasoning_effort,
    toggle_mcp_tool,
    toggle_skill,
    toggle_tool,
    ui_mode_label,
)
from ui.capability_groups import category_counts, group_skills, group_tools
from ui.tui.screens import PickerItem, open_picker
from ui.tui.widgets import ChatScroll, PromptTextArea

if TYPE_CHECKING:
    from openai import OpenAI

_TCSS = Path(__file__).resolve().parent / "tui.tcss"
_STREAM_INTERVAL = 0.12
_STREAM_TAIL_CHARS = 3200
_SCROLL_TAIL = 5


def _plain_static(content: str, *, classes: str = "") -> Static:
    """Plain text Static — avoids MarkupError on `[` `]` in tool/LLM output."""
    return Static(content, classes=classes, markup=False)


class FiagentApp(App):
    """Atrading Textual UI."""

    CSS_PATH = _TCSS
    TITLE = "Atrading"
    ALLOW_MAXIMIZE = False
    BINDINGS = [
        Binding("escape", "request_pause", "Pause", show=True),
        # show=False：Footer 右侧已有命令面板专位，避免底栏出现两个 P
        Binding("ctrl+p", "command_palette", "命令", show=False),
        Binding("ctrl+c", "quit", "退出", show=False),
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
        self._pending_reexec = False
        self._reexec_resume_id: str | None = None
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
        # 粘底：用户上滑后为 False，新消息/回到底部再 True（避免输出时钉死在底）
        self._stick_bottom = True
        self._activity_text = ""
        self._activity_last = 0.0
        self._last_thought_text = ""
        self._last_thought_key = ""
        self._llm_round_idx = 0
        self._slash_open = False
        self._slash_items: list[tuple[str, str]] = []
        self._slash_index = 0
        self._slash_window = 0  # 可视窗口起始下标（超过 max 行时滚动）
        self._ctx_label = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield ChatScroll(id="chat")
        yield Static("", id="live-strip", markup=False)
        yield Static("就绪", id="status-bar")
        # OpenCode 结构：autocomplete 与 textarea 同属 composer，菜单在输入框正上方
        with Vertical(id="composer"):
            yield Static("", id="slash-menu", markup=False)
            yield PromptTextArea(
                id="prompt",
                placeholder="输入 / 弹出指令  ·  Enter 发送  ·  Shift+Enter 换行",
                classes="prompt-input",
            )
        yield Footer()

    def on_mount(self) -> None:
        ui.bind_tui(self)
        turn_control.set_tui_mode(True)
        self._close_slash_menu()
        self._reload_session_view(announce=False)
        self._pin_bottom()
        self.query_one("#prompt", PromptTextArea).focus()

    def _reload_session_view(self, *, announce: bool = True) -> None:
        """清屏 → 启动卡 → 回放历史（OpenCode resume hydrate）。"""
        self.clear_chat()
        self._post_startup()
        ui.hydrate_messages(self.messages)
        if announce and self.current is not None:
            self.mount_line(
                f"已载入 session [{self.current.id}] {self.current.title}",
                classes="line-info",
            )
        self._refresh_idle_status()
        self._pin_bottom()

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
            if chat.is_vertical_scroll_end:
                return True
            return chat.scroll_y >= max(0, chat.max_scroll_y - _SCROLL_TAIL)
        except Exception:
            return True

    def _anchor_released(self) -> bool:
        """Textual native anchor was released by user scroll."""
        try:
            return bool(getattr(self._chat(), "_anchor_released", False))
        except Exception:
            return False

    def _scroll_bottom(self) -> None:
        self._chat().scroll_end(animate=False, immediate=True, x_axis=False)

    def _pin_bottom(self) -> None:
        """重新粘底并滚到末尾（用户发送 / 主动回底时用）。"""
        self._stick_bottom = True
        self._stream_follow = True
        chat = self._chat()
        # Native anchor keeps view at bottom on layout growth without yanking after release
        chat.anchor(True)

    def _unpin_bottom(self) -> None:
        self._stick_bottom = False
        self._stream_follow = False
        try:
            self._chat().release_anchor()
        except Exception:
            pass

    def _scroll_bottom_if_pinned(self) -> None:
        """仅在用户未上滑（粘底）时跟底，允许查看上方历史。"""
        if not self._stick_bottom:
            return
        if self._stream_body is not None and not self._stream_follow:
            return
        chat = self._chat()
        if chat.is_vertical_scrollbar_grabbed or self._anchor_released():
            self._unpin_bottom()
            return
        # Native anchor already follows layout growth; scroll_end would re-bite a released anchor
        if chat.is_anchored:
            return
        if not self._is_near_bottom():
            self._unpin_bottom()
            return
        self._scroll_bottom()

    @on(events.MouseScrollUp)
    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if self._slash_open:
            self._slash_wheel(-1)
            event.stop()
            event.prevent_default()
            return
        self._unpin_bottom()

    @on(events.MouseScrollDown)
    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if self._slash_open:
            self._slash_wheel(1)
            event.stop()
            event.prevent_default()
            return
        if self._is_near_bottom():
            self._pin_bottom()

    def on_key(self, event: events.Key) -> None:
        # 焦点在输入框时也可滚聊天区：PageUp/PageDown
        if event.key == "pageup":
            self._unpin_bottom()
            self._chat().scroll_page_up(animate=False)
            event.stop()
            return
        if event.key == "pagedown":
            self._chat().scroll_page_down(animate=False)
            if self._is_near_bottom():
                self._pin_bottom()
            event.stop()
            return

    def clear_chat(self) -> None:
        self._chat().remove_children()

    def _post_startup(self) -> None:
        skills = [s.name for s in self.ctx.enabled_skills()]
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
        title_safe = rich_escape(session_title or "")
        body = (
            f"[bold #58a6ff]fi[/][bold #d2a8ff]agent[/]  "
            f"[dim]quant research assistant[/]\n\n"
            f"[dim]时间[/]  {rich_escape(current_time)}\n"
            f"[dim]会话[/]  {sid}  {title_safe}\n"
            f"[dim]模型[/]  {rich_escape(model_label())}  ·  [dim]强度[/]  {rich_escape(effort_label())}\n"
            f"[dim]技能[/]  {skill_n} 个  ·  [dim]Hooks[/]  {hook_count}  ·  "
            f"[dim]思考显示[/]  {think}  ·  [dim]界面[/]  {rich_escape(ui_mode_label(ui_mode))}"
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
            "输入 / 选指令  ·  Ctrl+P 切换/管理  ·  Esc 暂停  ·  PageUp/滚轮可上翻",
            classes="card-hint",
        )
        self._scroll_bottom_if_pinned()

    def mount_sessions(self, sessions) -> None:
        # /sessions 走次级选择界面，不再往聊天区堆列表
        self.open_session_picker()

    def mount_line(self, text: str, *, classes: str = "line-info") -> None:
        self._chat().mount(Static(text, classes=classes))
        self._scroll_bottom_if_pinned()

    def mount_rule(self, title: str) -> None:
        if title:
            self._chat().mount(Static(title, classes="round-rule-label"))
        self._chat().mount(Rule.horizontal(classes="round-rule"))
        self._scroll_bottom_if_pinned()

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
        self._scroll_bottom_if_pinned()

    def mount_user(self, content: str, *, collapsed: bool = False) -> None:
        # 用户新发言：强制粘底，方便看本轮输出
        if collapsed and len(content) >= 200:
            first = content.strip().splitlines()[0][:48]
            from rich.markup import escape

            self.mount_foldable(f"You: {escape(first)}", content, kind="user", collapsed=True)
            self._pin_bottom()
            return
        from rich.markup import escape
        self._chat().mount(
            Static(f"[bold #3fb950]You[/]  {escape(content)}", classes="line-user")
        )
        self._pin_bottom()

    def reset_turn_ui(self) -> None:
        self._last_thought_text = ""
        self._last_thought_key = ""
        ui._tui_stream_pending = ""
        ui._tui_think_pending = ""

    def llm_round_start(self, round_idx: int) -> None:
        self._llm_round_idx = round_idx
        self._clear_activity()
        ctx_bit = f" · {self._ctx_label}" if self._ctx_label else ""
        self.set_status(f"[dim]第 {round_idx} 轮推理…{ctx_bit}[/]")

    def show_context_progress(self, usage: dict) -> None:
        label = str(usage.get("label") or "")
        self._ctx_label = label
        ratio = float(usage.get("ratio") or 0)
        if ratio >= 0.85:
            color = "#f85149"
        elif ratio >= 0.65:
            color = "#d29922"
        else:
            color = "#8b949e"
        if self._busy or self._paused:
            self.set_status(
                f"[dim]第 {self._llm_round_idx} 轮[/]  [{color}]{label}[/]"
            )
        else:
            self._refresh_idle_status()

    def llm_activity_update(self, text: str) -> None:
        now = time.monotonic()
        if text == self._activity_text and now - self._activity_last < 0.2:
            return
        self._activity_text = text
        self._activity_last = now
        if self._activity is None:
            self._activity = Static(
                f"[dim italic]{text}[/]",
                classes="line-activity",
            )
            self._chat().mount(self._activity)
            self._scroll_bottom_if_pinned()
            return
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
        self._scroll_bottom_if_pinned()
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
            self._scroll_bottom_if_pinned()
            return

        if root and root.is_attached:
            root.remove()
        self._last_thought_key = key
        self._last_thought_text = text
        self.tui_show_thinking(text, round_idx=round_idx)

    def stream_reply_begin(self) -> None:
        """Assistant 流式：直接在聊天区挂载回复块并逐字刷新。"""
        self._cancel_stream_timer()
        self._hide_live_strip()
        self._stream_last_flush = 0.0
        self._stream_last_len = 0
        self._stream_follow = self._stick_bottom
        self._stream_pending = ui._tui_stream_pending
        self._stream_body = _plain_static("", classes="stream-md stream-plain")
        self._stream_root = Container(
            Static("[bold #79c0ff]Assistant[/]", classes="reply-label"),
            self._stream_body,
            classes="reply-block stream-live",
        )
        self._chat().mount(self._stream_root)
        self._scroll_bottom_if_pinned()
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
        # 流式中只刷新尾部，避免超长正文拖垮布局；结束时再写全文
        self._stream_body.update(self._stream_tail_text(self._stream_pending, cursor=True))
        self._stream_last_flush = time.monotonic()
        # 勿强制 scroll_end：会把已上滑的用户拽回底部，并重新咬住 Textual anchor。
        # 粘底时由 chat.anchor(True) + compositor 跟随增高。
        if self._stick_bottom and self._anchor_released():
            self._unpin_bottom()

    def stream_reply_update(self, content: str) -> None:
        self._stream_pending = content
        ui._tui_stream_pending = content
        if self._stream_body is None:
            return
        if self._stream_last_flush == 0.0:
            self._flush_stream_live()

    def stream_reply_end(self, content: str) -> None:
        self._cancel_stream_timer()
        follow = self._stream_follow and self._stick_bottom and not self._anchor_released()
        root = self._stream_root
        body = self._stream_body
        self._stream_root = None
        self._stream_body = None
        self._stream_pending = content
        self._hide_live_strip()

        if not content.strip():
            if root is not None and root.is_attached:
                root.remove()
            return

        if root is not None and body is not None and root.is_attached and body.is_attached:
            body.update(content)
            root.remove_class("stream-live")
            root.add_class("stream-done")
            if follow:
                self._scroll_bottom_if_pinned()
            return

        if root is not None and root.is_attached:
            root.remove()
        self.mount_reply(content)

    def stream_reply_cancel(self) -> None:
        self._cancel_stream_timer()
        if self._stream_root is not None and self._stream_root.is_attached:
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
        self._scroll_bottom_if_pinned()

    def tui_expand_slot(self, slot: int = 1) -> None:
        items = list(self.query(Collapsible))
        if not items:
            self.mount_line("没有可展开的内容", classes="line-warn")
            return
        if slot < 1 or slot > len(items):
            self.mount_line(f"无效序号 [{slot}]", classes="line-warn")
            return
        items[-slot].collapsed = False
        self._scroll_bottom_if_pinned()

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
            self._refresh_idle_status()
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

            first = next(iter(result.strip().splitlines()), "(empty)")
            preview = escape(first[:72])
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

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        """Ctrl+P：只保留实用入口（去掉 Theme/Keys/Maximize/Screenshot）。"""
        yield SystemCommand(
            "切换 Session",
            "进入次级界面选择 / 新建对话",
            self.open_session_picker,
        )
        yield SystemCommand(
            "切换模型",
            "进入次级界面选择 Pro / Flash",
            self.open_model_picker,
        )
        yield SystemCommand(
            "切换思考强度",
            "进入次级界面选择 High / Max / 关闭",
            self.open_effort_picker,
        )
        yield SystemCommand(
            "管理工具",
            "开关各工具（下次对话轮次生效）",
            self.open_tools_picker,
        )
        yield SystemCommand(
            "管理 Skills",
            "开关各 skill（下次对话轮次生效）",
            self.open_skills_picker,
        )
        yield SystemCommand(
            "管理 MCP",
            "开关 MCP server / 工具（下次对话轮次生效）",
            self.open_mcp_picker,
        )
        yield SystemCommand(
            "新建对话",
            "清空当前上下文，开始新 session",
            self._palette_new_session,
        )
        yield SystemCommand(
            "全面重启",
            "退出进程并重新进入（/reload_comp）",
            self._palette_reload_comp,
        )
        yield SystemCommand(
            "退出",
            "退出 Atrading",
            self.action_quit,
        )

    def open_session_picker(self) -> None:
        items = [
            PickerItem(
                id="__new__",
                label="新建对话",
                hint="清空上下文",
                current=self.current is None,
            )
        ]
        for session in self.store.list_sessions(limit=30):
            ts = session.updated_at[:19].replace("T", " ")
            items.append(
                PickerItem(
                    id=session.id,
                    label=session.title,
                    hint=f"{session.id} · {session.message_count} 条 · {ts}",
                    current=bool(self.current and self.current.id == session.id),
                )
            )
        open_picker(
            self,
            title="Session",
            hint="选择要进入的对话 · Esc 返回命令面板",
            items=items,
            on_pick=self._on_session_picked,
            on_cancel=self.action_command_palette,
        )

    def open_model_picker(self) -> None:
        current = get_model()
        items = [
            PickerItem(
                id=model_id,
                label=MODEL_LABELS[model_id],
                hint=model_id,
                current=model_id == current,
            )
            for model_id in AVAILABLE_MODELS
        ]
        open_picker(
            self,
            title="模型",
            hint="选择模型（下次请求生效）· Esc 返回命令面板",
            items=items,
            on_pick=self._palette_set_model,
            on_cancel=self.action_command_palette,
        )

    def open_effort_picker(self) -> None:
        current = get_reasoning_effort()
        items = [
            PickerItem(
                id=effort,
                label=EFFORT_LABELS[effort],
                hint="关闭后走非 thinking 模式" if effort == "off" else "thinking 开启",
                current=effort == current,
            )
            for effort in AVAILABLE_EFFORTS
        ]
        open_picker(
            self,
            title="思考强度",
            hint="选择强度 · Esc 返回命令面板",
            items=items,
            on_pick=self._palette_set_effort,
            on_cancel=self.action_command_palette,
        )

    def open_tools_picker(self) -> None:
        """先选分类，再进该分类开关工具。"""
        self.ctx.refresh()
        toggleable = [
            (n, s) for n, s in self.ctx.tools.all() if n not in ALWAYS_ON_TOOLS
        ]
        if not toggleable:
            ui.warn("没有可开关的工具")
            return
        by_name = dict(toggleable)
        groups = group_tools([n for n, _ in toggleable])
        items = []
        for cat_id, hint, members in groups:
            on_n, total = category_counts(members, is_enabled=is_tool_enabled)
            items.append(
                PickerItem(
                    id=f"cat:{cat_id}",
                    label=cat_id,
                    hint=f"{on_n}/{total} · {hint}",
                    on=(True if on_n == total else False if on_n == 0 else None),
                )
            )
        open_picker(
            self,
            title="管理工具 · 分类",
            hint="绿=全开 · 红=全关 · Esc 返回命令面板",
            items=items,
            on_pick=lambda cid: self._open_tools_category(cid, by_name),
            on_cancel=self.action_command_palette,
        )

    def _open_tools_category(self, cat_pick_id: str, by_name: dict[str, str]) -> None:
        if not cat_pick_id.startswith("cat:"):
            return
        cat_id = cat_pick_id.removeprefix("cat:")
        groups = {c: (h, m) for c, h, m in group_tools(list(by_name))}
        if cat_id not in groups:
            ui.warn(f"未知分类: {cat_id}")
            return
        _hint, members = groups[cat_id]
        items = [
            PickerItem(
                id=name,
                label=name,
                hint=by_name.get(name) or "",
                on=is_tool_enabled(name),
            )
            for name in members
        ]
        open_picker(
            self,
            title=f"工具 · {cat_id}",
            hint="点击切换 · 绿开红关 · Esc 返回分类",
            items=items,
            on_pick=lambda name: self._palette_toggle_tool(name, cat_id, by_name),
            on_cancel=self.open_tools_picker,
        )

    def open_skills_picker(self) -> None:
        """先选分类，再进该分类开关 Skills。"""
        self.ctx.refresh()
        skills = list(self.ctx.skills.all())
        if not skills:
            ui.warn("没有可开关的 skill")
            return
        by_name = {s.name: s for s in skills}
        groups = group_skills([s.name for s in skills])
        items = []
        for cat_id, hint, members in groups:
            on_n, total = category_counts(members, is_enabled=is_skill_enabled)
            items.append(
                PickerItem(
                    id=f"cat:{cat_id}",
                    label=cat_id,
                    hint=f"{on_n}/{total} · {hint}",
                    on=(True if on_n == total else False if on_n == 0 else None),
                )
            )
        open_picker(
            self,
            title="管理 Skills · 分类",
            hint="绿=全开 · 红=全关 · Esc 返回命令面板",
            items=items,
            on_pick=self._open_skills_category,
            on_cancel=self.action_command_palette,
        )

    def _open_skills_category(self, cat_pick_id: str) -> None:
        if not cat_pick_id.startswith("cat:"):
            return
        cat_id = cat_pick_id.removeprefix("cat:")
        skills = list(self.ctx.skills.all())
        by_name = {s.name: s for s in skills}
        groups = {c: (h, m) for c, h, m in group_skills(list(by_name))}
        if cat_id not in groups:
            ui.warn(f"未知分类: {cat_id}")
            return
        _hint, members = groups[cat_id]
        items = [
            PickerItem(
                id=name,
                label=name,
                hint=(
                    f"[{'内置' if by_name[name].bundled else '用户'}] "
                    + (by_name[name].description or "")[:40]
                ),
                on=is_skill_enabled(name),
            )
            for name in members
        ]
        open_picker(
            self,
            title=f"Skills · {cat_id}",
            hint="点击切换 · 绿开红关 · Esc 返回分类",
            items=items,
            on_pick=lambda name: self._palette_toggle_skill(name, cat_id),
            on_cancel=self.open_skills_picker,
        )

    def open_mcp_picker(self) -> None:
        """先选 MCP server，再开关该 server 下的工具。"""
        self.ctx.refresh()
        servers = self.ctx.mcp.servers()
        if not servers:
            ui.warn("未配置 MCP server（编辑 mcps/mcp.json）")
            return
        items = []
        for server in servers:
            tool_names = [t.name for t in server.tools]
            if server.enabled and tool_names:
                on_n = sum(1 for n in tool_names if is_mcp_tool_enabled(n))
                total = len(tool_names)
                hint = f"{on_n}/{total} 工具"
                tone = True if on_n == total else False if on_n == 0 else None
            elif server.enabled:
                hint = "无工具"
                tone = True
            else:
                hint = "server 已关"
                tone = False
            if server.note:
                hint = f"{hint} · {server.note[:36]}"
            items.append(
                PickerItem(
                    id=f"srv:{server.id}",
                    label=server.id,
                    hint=hint,
                    on=tone,
                )
            )
        open_picker(
            self,
            title="管理 MCP · Server",
            hint="绿开红关 · Esc 返回命令面板",
            items=items,
            on_pick=self._on_mcp_server_picked,
            on_cancel=self.action_command_palette,
        )

    def _on_mcp_server_picked(self, pick_id: str) -> None:
        if not pick_id.startswith("srv:"):
            return
        self._open_mcp_server(pick_id.removeprefix("srv:"))

    def _open_mcp_server(self, server_id: str) -> None:
        self.ctx.refresh()
        server = self.ctx.mcp.get_server(server_id)
        if server is None:
            ui.warn(f"未知 MCP server: {server_id}")
            return
        items = [
            PickerItem(
                id="__toggle_server__",
                label=f"{'关闭' if server.enabled else '启用'} server `{server_id}`",
                hint="写入 mcps/mcp.json",
                on=server.enabled,
            )
        ]
        for tool in server.tools:
            # server 关时工具实质不可用，仍显示 prefs 状态
            effective = server.enabled and is_mcp_tool_enabled(tool.name)
            items.append(
                PickerItem(
                    id=tool.name,
                    label=tool.name,
                    hint=(tool.description or "")[:48],
                    on=effective,
                )
            )
        if len(items) == 1:
            items.append(
                PickerItem(
                    id="__empty__",
                    label="（该 server 未声明 tools）",
                    hint="在 mcp.json 的 tools 数组补充",
                    on=None,
                )
            )
        open_picker(
            self,
            title=f"MCP · {server_id}",
            hint="点击切换 · 绿开红关 · Esc 返回 server 列表",
            items=items,
            on_pick=lambda name: self._palette_toggle_mcp(name, server_id),
            on_cancel=self.open_mcp_picker,
        )

    def _palette_toggle_tool(
        self,
        name: str,
        cat_id: str | None = None,
        by_name: dict[str, str] | None = None,
    ) -> None:
        enabled = toggle_tool(name)
        self.ctx.sync_system_message(self.messages)
        ui.success(f"工具 `{name}` 已{'启用' if enabled else '禁用'}")
        self._refresh_idle_status()
        if cat_id and by_name is not None:
            self._open_tools_category(f"cat:{cat_id}", by_name)
        else:
            self.open_tools_picker()

    def _palette_toggle_skill(self, name: str, cat_id: str | None = None) -> None:
        enabled = toggle_skill(name)
        self.ctx.sync_system_message(self.messages)
        ui.success(f"Skill `{name}` 已{'启用' if enabled else '禁用'}")
        self._refresh_idle_status()
        if cat_id:
            self._open_skills_category(f"cat:{cat_id}")
        else:
            self.open_skills_picker()

    def _palette_toggle_mcp(self, name: str, server_id: str) -> None:
        if name in ("__empty__", "__help__"):
            self._open_mcp_server(server_id)
            return
        if name == "__toggle_server__":
            try:
                enabled = self.ctx.mcp.toggle_server(server_id)
            except KeyError:
                ui.warn(f"未知 MCP server: {server_id}")
                return
            self.ctx.sync_system_message(self.messages)
            ui.success(f"MCP server `{server_id}` 已{'启用' if enabled else '关闭'}")
            self._refresh_idle_status()
            self._open_mcp_server(server_id)
            return
        enabled = toggle_mcp_tool(name)
        self.ctx.refresh()
        self.ctx.sync_system_message(self.messages)
        ui.success(f"MCP 工具 `{name}` 已{'启用' if enabled else '禁用'}")
        self._refresh_idle_status()
        self._open_mcp_server(server_id)

    def _on_session_picked(self, item_id: str) -> None:
        if item_id == "__new__":
            self._palette_new_session()
            return
        self._palette_resume_session(item_id)

    def _palette_set_model(self, model_id: str) -> None:
        set_model(model_id)
        ui.success(f"模型已切换为 {MODEL_LABELS.get(model_id, model_id)}（{model_id}）")
        self._refresh_idle_status()

    def _palette_set_effort(self, effort: str) -> None:
        set_reasoning_effort(effort)
        ui.success(f"思考强度已切换为 {EFFORT_LABELS.get(effort, effort)}")
        self._refresh_idle_status()

    def _palette_new_session(self) -> None:
        if self._busy or self._paused:
            self.mount_line("忙碌/暂停中，无法切换 session", classes="line-warn")
            return
        current, new_messages, _ = self.handle_command(
            "/new", self.store, self.ctx, self.current
        )
        self.current = current
        if new_messages is not None:
            self.messages = new_messages
            self._reload_session_view(announce=False)
            self.mount_line("已开始新对话", classes="line-info")

    def _palette_reload_comp(self) -> None:
        if self._busy:
            self.mount_line("忙碌中，请先等待本轮结束或 Esc 暂停后中止", classes="line-warn")
            return
        self._pending_reexec = True
        self._reexec_resume_id = self.current.id if self.current else None
        ui.success("正在全面重启…")
        # 先关掉可能残留的命令面板 / 次级界面，再干净退出
        try:
            while len(self.screen_stack) > 1:
                self.pop_screen()
        except Exception:
            pass
        self.exit()

    def _palette_resume_session(self, session_id: str) -> None:
        if self._busy or self._paused:
            self.mount_line("忙碌/暂停中，无法切换 session", classes="line-warn")
            return
        if self.current and self.current.id == session_id:
            ui.info(f"已在当前 session [{session_id}]")
            return
        current, new_messages, _ = self.handle_command(
            f"/resume {session_id}", self.store, self.ctx, self.current
        )
        self.current = current
        if new_messages is not None:
            self.messages = new_messages
            if self.current is not None:
                set_last_session_id(self.current.id)
            self._reload_session_view(announce=True)

    def _refresh_idle_status(self) -> None:
        if self._busy or self._paused:
            return
        # 空闲时也刷新一次上下文粗估
        try:
            from core.context_budget import estimate_context_usage

            usage = estimate_context_usage(
                self.messages, self.ctx.build_openai_tools()
            )
            self._ctx_label = usage["label"]
        except Exception:
            pass
        sid = self.current.id if self.current else "新对话"
        tools_on = len(self.ctx.enabled_tools())
        tools_all = len(self.ctx.tools.all())
        skills_on = len(self.ctx.enabled_skills())
        skills_all = len(self.ctx.skills.all())
        mcp_on = len(self.ctx.mcp.all())
        mcp_all = sum(len(s.tools) for s in self.ctx.mcp.servers())
        mcp_bit = f" · MCP {mcp_on}/{mcp_all}" if mcp_all else ""
        ctx_bit = f" · {self._ctx_label}" if self._ctx_label else ""
        self.set_status(
            f"[dim]就绪 · {model_label()} · {effort_label()} · {sid} · "
            f"工具 {tools_on}/{tools_all} · Skills {skills_on}/{skills_all}"
            f"{mcp_bit}{ctx_bit} · Ctrl+P[/]"
        )

    def action_request_pause(self) -> None:
        if self._slash_open:
            self._close_slash_menu()
            return
        if self._paused:
            turn_control.request_resume()
            self.set_busy("继续中…")
            return
        if not self._busy:
            return
        turn_control.request_pause()
        self.set_paused()
        self.mount_line("已暂停 — 输入 resume 继续 / abort 中止", classes="line-warn")

    # --- slash 指令菜单（单 Static 多行；每行按 cell 宽度截断，高度=行数）---

    _SLASH_MAX_ROWS = 10

    def _slash_menu(self) -> Static:
        return self.query_one("#slash-menu", Static)

    def _close_slash_menu(self) -> None:
        menu = self._slash_menu()
        menu.update("")
        menu.remove_class("-open")
        menu.styles.height = 0
        menu.styles.max_height = 0
        self._slash_open = False
        self._slash_items = []
        self._slash_index = 0
        self._slash_window = 0

    def _slash_fit(self, text: str, width: int) -> str:
        if width <= 0:
            return ""
        if cell_len(text) <= width:
            return text
        if width == 1:
            return "…"
        return set_cell_size(text, width - 1) + "…"

    def _render_slash_menu(self) -> None:
        items = self._slash_items
        if not items:
            self._close_slash_menu()
            return
        n = len(items)
        max_rows = self._SLASH_MAX_ROWS
        if self._slash_index < self._slash_window:
            self._slash_window = self._slash_index
        elif self._slash_index >= self._slash_window + max_rows:
            self._slash_window = self._slash_index - max_rows + 1
        self._slash_window = max(0, min(self._slash_window, max(0, n - max_rows)))

        end = min(n, self._slash_window + max_rows)
        window = items[self._slash_window:end]
        name_width = max((len(cmd) for cmd, _ in window), default=0) + 2
        row_width = max(40, self.size.width - 6)

        body = Text()
        for i, (cmd, tip) in enumerate(window):
            abs_i = self._slash_window + i
            selected = abs_i == self._slash_index
            name = cmd.ljust(name_width)
            desc = (tip or "").replace("\n", " ").strip()
            name_part = f" {name}"
            budget = row_width - cell_len(name_part) - 1
            if desc and budget > 0:
                desc = self._slash_fit(desc, budget)
            else:
                desc = ""
            line = name_part + (f" {desc}" if desc else "")
            # 再保险：整行不超过 row_width，杜绝软换行吃掉下一命令
            line = self._slash_fit(line, row_width)
            style = "bold #0d1117 on #58a6ff" if selected else "#c9d1d9"
            body.append(line, style=style)
            if i + 1 < len(window):
                body.append("\n")

        visible = len(window)
        menu = self._slash_menu()
        menu.update(body)
        menu.styles.height = visible
        menu.styles.max_height = visible
        menu.add_class("-open")
        self._slash_open = True
        menu.refresh(layout=True)

    def _slash_move(self, delta: int) -> None:
        if not self._slash_open or not self._slash_items:
            return
        n = len(self._slash_items)
        self._slash_index = (self._slash_index + delta) % n
        self._render_slash_menu()

    def _slash_wheel(self, delta: int) -> None:
        """鼠标滚轮：滑动可视窗口；不足一屏则移动高亮。"""
        if not self._slash_open or not self._slash_items:
            return
        n = len(self._slash_items)
        max_rows = self._SLASH_MAX_ROWS
        if n <= max_rows:
            self._slash_move(delta)
            return
        new_window = max(0, min(self._slash_window + delta, n - max_rows))
        if new_window == self._slash_window:
            return
        self._slash_window = new_window
        if self._slash_index < self._slash_window:
            self._slash_index = self._slash_window
        elif self._slash_index >= self._slash_window + max_rows:
            self._slash_index = self._slash_window + max_rows - 1
        self._render_slash_menu()

    def _slash_selected(self) -> str | None:
        if not self._slash_open or not self._slash_items:
            return None
        if not (0 <= self._slash_index < len(self._slash_items)):
            return None
        return self._slash_items[self._slash_index][0]

    def _slash_pick_and_run(self, cmd: str) -> None:
        self._close_slash_menu()
        prompt = self.query_one("#prompt", PromptTextArea)
        prompt.clear()
        if self._busy or self._paused:
            return
        threading.Thread(target=self._run_turn, args=(cmd,), daemon=True).start()

    @on(events.Click, "#slash-menu")
    def on_slash_menu_click(self, event: events.Click) -> None:
        if not self._slash_open or not self._slash_items:
            return
        # event.y：相对本控件的行坐标
        row = int(getattr(event, "y", 0) or 0)
        idx = self._slash_window + row
        if not (0 <= idx < len(self._slash_items)):
            return
        self._slash_index = idx
        picked = self._slash_selected()
        event.stop()
        if picked:
            self._slash_pick_and_run(picked)

    def _update_slash_menu(self, text: str) -> None:
        """行首 /、无参数空格时弹出；列表来自 list_slash_matches。"""
        from core.commands import list_slash_matches

        if self._busy or self._paused:
            self._close_slash_menu()
            return
        raw = text
        if "\n" in raw:
            self._close_slash_menu()
            return
        token = raw.strip()
        if not token.startswith("/") or " " in token:
            self._close_slash_menu()
            return
        matches = list_slash_matches(token)
        if not matches:
            self._close_slash_menu()
            return

        prev = self._slash_selected() if self._slash_open else None
        self._slash_items = [
            (cmd, (desc or "").replace("\n", " ").strip()) for cmd, desc in matches
        ]
        self._slash_index = 0
        self._slash_window = 0
        if prev:
            for i, (cmd, _) in enumerate(self._slash_items):
                if cmd == prev:
                    self._slash_index = i
                    break
        self._render_slash_menu()

    @on(PromptTextArea.Changed, "#prompt")
    def on_prompt_changed(self, event: PromptTextArea.Changed) -> None:
        self._update_slash_menu(event.text_area.text)

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
        if text.lower() in ("quit", "exit", "q", "/quit", "/exit", "/q"):
            self.exit()
            return
        threading.Thread(target=self._run_turn, args=(text,), daemon=True).start()

    def _run_turn(self, user_input: str) -> None:
        with self._turn_lock:
            self.call_from_thread(self.set_busy, "处理中…")
            try:
                self._process_input(user_input)
            finally:
                # 全面重启时不要再 set_idle，避免和 exit 抢状态
                if not self._pending_reexec:
                    self.call_from_thread(self.set_idle)

    def _process_input(self, user_input: str) -> None:
        if user_input.startswith("/"):
            from core.commands import HANDLED_REEXEC, HANDLED_RESTART

            low = user_input.strip().lower()
            # 带切换的命令：先进入次级界面，而不是直接改 / 刷列表
            if low in ("/sessions", "/session"):
                self.call_from_thread(self.open_session_picker)
                return
            if low == "/model":
                self.call_from_thread(self.open_model_picker)
                return
            if low == "/effort":
                self.call_from_thread(self.open_effort_picker)
                return
            if low in ("/tools", "/tool"):
                self.call_from_thread(self.open_tools_picker)
                return
            if low in ("/skills", "/skill"):
                self.call_from_thread(self.open_skills_picker)
                return
            if low in ("/mcp", "/mcps"):
                self.call_from_thread(self.open_mcp_picker)
                return

            current, new_messages, handled = self.handle_command(
                user_input, self.store, self.ctx, self.current
            )
            self.current = current
            if handled in (HANDLED_REEXEC, HANDLED_RESTART):
                self._pending_reexec = True
                self._reexec_resume_id = self.current.id if self.current else None
                self.call_from_thread(self.exit)
                return
            if new_messages is not None:
                self.messages = new_messages
                if self.current is not None:
                    set_last_session_id(self.current.id)
                self.call_from_thread(self._reload_session_view)
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
            set_last_session_id(self.current.id)
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
            del self.messages[turn_start:]


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
    from core.commands import reexec_self

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
    if app._pending_reexec:
        # app.run() 已退出 Textual；再复位终端后拉起，避免鼠标点不动
        reexec_self(resume_id=app._reexec_resume_id)
