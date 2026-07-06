"""Textual 自定义控件。"""

from __future__ import annotations

from dataclasses import dataclass

from textual import events
from textual.message import Message
from textual.containers import VerticalScroll
from textual.widgets import TextArea


class ChatScroll(VerticalScroll):
    """聊天滚动区。"""


class PromptTextArea(TextArea):
    """多行输入：支持完整粘贴；Enter 发送，Shift+Enter 换行。"""

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

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            value = self.text.strip()
            if value:
                event.stop()
                event.prevent_default()
                self.clear()
                self.post_message(self.Submitted(self, value))
            return
        await super()._on_key(event)
