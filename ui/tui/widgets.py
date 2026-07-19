"""Textual 自定义控件。"""

from __future__ import annotations

from dataclasses import dataclass

from textual import events
from textual.message import Message
from textual.containers import VerticalScroll
from textual.widgets import TextArea


class ChatScroll(VerticalScroll):
    """聊天滚动区。

    滚轮/键盘上滑时通知 App 取消粘底——事件常在此被消费，App 级 handler 收不到。
    """

    ALLOW_MAXIMIZE = False

    def _notify_scroll_away(self) -> None:
        unpin = getattr(self.app, "_unpin_bottom", None)
        if callable(unpin):
            unpin()

    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self._notify_scroll_away()
        return super()._on_mouse_scroll_up(event)

    def action_scroll_up(self) -> None:
        self._notify_scroll_away()
        return super().action_scroll_up()

    def action_page_up(self) -> None:
        self._notify_scroll_away()
        return super().action_page_up()


class PromptTextArea(TextArea):
    """多行输入：支持完整粘贴；Enter 发送，Shift+Enter 换行。

    当 App 打开 slash 指令菜单时，↑↓ 选中、Tab/Enter 确认、Esc 关闭。
    """

    ALLOW_MAXIMIZE = False

    @dataclass
    class Submitted(Message):
        """回车提交（Shift+Enter 为换行）。"""

        prompt: TextArea
        value: str

        @property
        def control(self) -> TextArea:
            return self.prompt

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("soft_wrap", True)
        kwargs.setdefault("show_line_numbers", False)
        kwargs.setdefault("tab_behavior", "indent")
        super().__init__(*args, **kwargs)

    def _slash_open(self) -> bool:
        return bool(getattr(self.app, "_slash_open", False))

    async def _on_key(self, event: events.Key) -> None:
        # 让 Ctrl+P 冒泡到 App 打开命令面板
        if event.key == "ctrl+p":
            return

        if self._slash_open():
            app = self.app
            if event.key == "escape":
                event.stop()
                event.prevent_default()
                app._close_slash_menu()  # type: ignore[attr-defined]
                return
            if event.key == "down":
                event.stop()
                event.prevent_default()
                app._slash_move(1)  # type: ignore[attr-defined]
                return
            if event.key == "up":
                event.stop()
                event.prevent_default()
                app._slash_move(-1)  # type: ignore[attr-defined]
                return
            if event.key in ("tab", "enter"):
                picked = app._slash_selected()  # type: ignore[attr-defined]
                if picked:
                    event.stop()
                    event.prevent_default()
                    app._close_slash_menu()  # type: ignore[attr-defined]
                    self.clear()
                    self.post_message(self.Submitted(self, picked))
                    return

        if event.key == "enter":
            value = self.text.strip()
            if value:
                event.stop()
                event.prevent_default()
                self.clear()
                if getattr(self.app, "_slash_open", False):
                    self.app._close_slash_menu()  # type: ignore[attr-defined]
                self.post_message(self.Submitted(self, value))
            return
        await super()._on_key(event)
