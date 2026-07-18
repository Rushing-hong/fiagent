"""次级切换界面：点进类别 → 在 Modal 里选一项（OpenCode DialogSelect 思路）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.content import Content
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, OptionList
from textual.widgets.option_list import Option


@dataclass(frozen=True)
class PickerItem:
    """次级界面里的一行选项。

    on: True=绿点启用 / False=红点禁用 / None=无开关态（用 current 显示 ✓）
    """

    id: str
    label: str
    hint: str = ""
    current: bool = False
    on: bool | None = None


def _picker_prompt(it: PickerItem) -> Content:
    if it.on is True:
        mark = Content.from_markup("[#3fb950]●[/] ")
    elif it.on is False:
        mark = Content.from_markup("[#f85149]●[/] ")
    elif it.current:
        mark = Content("✓ ")
    else:
        mark = Content("  ")
    body = it.label
    if it.hint:
        body += f"  ·  {it.hint}"
    return mark + Content(body)


class PickerScreen(ModalScreen[str | None]):
    """通用选择次屏：Esc 返回，点击/回车选中。"""

    ALLOW_MAXIMIZE = False
    BINDINGS = [
        Binding("escape", "cancel", "返回", show=True),
    ]

    CSS = """
    PickerScreen {
        align: center middle;
    }

    #picker-dialog {
        width: 72;
        max-width: 96;
        height: auto;
        max-height: 80%;
        background: #161b22;
        border: round #30363d;
        padding: 1 1 0 1;
    }

    #picker-title {
        color: #58a6ff;
        text-style: bold;
        margin: 0 0 1 0;
        padding: 0 1;
    }

    #picker-hint {
        color: #8b949e;
        margin: 0 0 1 0;
        padding: 0 1;
    }

    #picker-list {
        height: auto;
        max-height: 22;
        border: none;
        background: #0d1117;
        padding: 0 1;
    }

    #picker-list > .option-list--option {
        padding: 0 1;
        color: #c9d1d9;
    }

    #picker-list > .option-list--option-highlighted {
        background: #21262d;
        color: #f0f6fc;
    }

    #picker-footer {
        dock: bottom;
        height: 1;
        background: #161b22;
        color: #484f58;
    }
    """

    def __init__(
        self,
        *,
        title: str,
        hint: str,
        items: list[PickerItem],
    ) -> None:
        super().__init__()
        self._title = title
        self._hint = hint
        self._items = items

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog"):
            yield Label(self._title, id="picker-title")
            yield Label(self._hint, id="picker-hint")
            options = [
                Option(_picker_prompt(it), id=it.id)
                for it in self._items
            ]
            yield OptionList(*options, id="picker-list")
            yield Footer(id="picker-footer")

    def on_mount(self) -> None:
        listing = self.query_one("#picker-list", OptionList)
        listing.focus()
        for i, it in enumerate(self._items):
            if it.current:
                listing.highlighted = i
                break

    @on(OptionList.OptionSelected, "#picker-list")
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        opt_id = event.option.id
        if opt_id is None:
            self.dismiss(None)
            return
        self.dismiss(str(opt_id))

    def action_cancel(self) -> None:
        self.dismiss(None)


def open_picker(
    app,
    *,
    title: str,
    hint: str,
    items: list[PickerItem],
    on_pick: Callable[[str], None],
    on_cancel: Callable[[], None] | None = None,
) -> None:
    """推入次级选择界面；选中回调 on_pick(id)，Esc 回调 on_cancel（上一层）。"""

    def _done(result: str | None) -> None:
        if result is not None:
            on_pick(result)
        elif on_cancel is not None:
            on_cancel()

    app.push_screen(PickerScreen(title=title, hint=hint, items=items), _done)
