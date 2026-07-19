import json
import os
import re
import threading
from collections.abc import Callable
from contextlib import contextmanager

from rich import box
from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from paths import DATA_DIR

from ui.collapse import (
    collapse_output,
    first_line_summary,
    max_chars_for_lines,
    reasoning_summary,
)
from ui.prefs import get_thinking_mode, toggle_thinking_mode

UI_CACHE_DIR = DATA_DIR / "ui_cache"

# OpenCode 风格默认：思考 3 行、工具块 10 行，宽度自适应
THINK_MAX_LINES = int(os.getenv("FIAGENT_THINK_MAX_LINES", "3"))
TOOL_MAX_LINES = int(os.getenv("FIAGENT_TOOL_MAX_LINES", "10"))

THEME = Theme({
    "banner": "bold cyan",
    "accent": "bold magenta",
    "muted": "dim",
    "user": "bold green",
    "think": "italic yellow",
    "tool": "bold blue",
    "result": "cyan",
    "reply": "white",
    "ok": "bold green",
    "warn": "bold yellow",
    "err": "bold red",
    "hook": "dim magenta",
    "id": "bold cyan",
    "title": "bold white",
    "fold": "dim italic",
})


class AgentUI:
    def __init__(self) -> None:
        self.console = Console(theme=THEME, highlight=False, legacy_windows=False)
        self.verbose = os.getenv("FIAGENT_UI_EXPAND", "").strip().lower() in ("1", "true", "yes")
        self.thinking_mode = get_thinking_mode()
        self._cache_seq = 0
        self._items: dict[int, dict] = {}
        self._recent: list[int] = []
        self._tui = None
        self._reply_streamed = False
        self._plain_stream_live = None
        self._thinking_shown = False
        self._dup_tool_counts: dict[str, int] = {}
        self._dup_result_counts: dict[str, int] = {}
        self._tui_stream_pending = ""
        self._tui_stream_queued = False
        self._tui_think_pending = ""
        self._tui_think_queued = False
        self._tui_activity_pending = ""
        self._tui_activity_queued = False
        UI_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def begin_turn(self) -> None:
        """新一轮用户输入：重置工具重复展示计数。"""
        self._dup_tool_counts = {}
        self._dup_result_counts = {}
        self._tui_call("reset_turn_ui")

    def bind_tui(self, app) -> None:
        self._tui = app
        self.thinking_mode = get_thinking_mode()

    def unbind_tui(self) -> None:
        self._tui = None

    @property
    def use_tui(self) -> bool:
        return self._tui is not None

    def _tui_call(self, method: str, *args, **kwargs) -> bool:
        if not self._tui:
            return False
        fn = getattr(self._tui, method, None)
        if fn is None:
            return False
        if threading.current_thread() is threading.main_thread():
            fn(*args, **kwargs)
        else:
            self._tui.call_from_thread(fn, *args, **kwargs)
        return True

    def _tui_queue_stream(self, content: str) -> bool:
        """合并流式 token 更新，避免 call_from_thread 队列爆炸。"""
        if not self._tui:
            return False
        self._tui_stream_pending = content
        if self._tui_stream_queued:
            return True
        self._tui_stream_queued = True
        if threading.current_thread() is threading.main_thread():
            self._tui_deliver_stream()
        else:
            self._tui.call_from_thread(self._tui_deliver_stream)
        return True

    def _tui_deliver_stream(self) -> None:
        self._tui_stream_queued = False
        if not self._tui:
            return
        pending = self._tui_stream_pending
        self._tui.stream_reply_update(pending)
        if self._tui_stream_pending != pending:
            self._tui_queue_stream(self._tui_stream_pending)

    def _tui_queue_think(self, text: str) -> bool:
        if not self._tui:
            return False
        self._tui_think_pending = text
        if self._tui_think_queued:
            return True
        self._tui_think_queued = True
        if threading.current_thread() is threading.main_thread():
            self._tui_deliver_think()
        else:
            self._tui.call_from_thread(self._tui_deliver_think)
        return True

    def _tui_deliver_think(self) -> None:
        self._tui_think_queued = False
        if not self._tui:
            return
        pending = self._tui_think_pending
        self._tui.stream_thinking_update(pending)
        if self._tui_think_pending != pending:
            self._tui_queue_think(self._tui_think_pending)

    def _tui_queue_activity(self, text: str) -> bool:
        if not self._tui:
            return False
        self._tui_activity_pending = text
        if self._tui_activity_queued:
            return True
        self._tui_activity_queued = True
        if threading.current_thread() is threading.main_thread():
            self._tui_deliver_activity()
        else:
            self._tui.call_from_thread(self._tui_deliver_activity)
        return True

    def _tui_deliver_activity(self) -> None:
        self._tui_activity_queued = False
        if not self._tui:
            return
        self._tui.llm_activity_update(self._tui_activity_pending)

    def toggle_verbose(self) -> bool:
        self.verbose = not self.verbose
        return self.verbose

    def toggle_thinking(self) -> str:
        self.thinking_mode = toggle_thinking_mode()
        if self._tui:
            self._tui.thinking_mode = self.thinking_mode
        return self.thinking_mode

    def _slot_for(self, item_id: int) -> int | None:
        """1 = 最新，2 = 次新 …"""
        if item_id not in self._recent:
            return None
        return len(self._recent) - self._recent.index(item_id)

    def list_collapsed(self) -> None:
        if not self._recent:
            self.warn("暂无折叠内容")
            return
        self.console.print()
        table = Table(title="折叠列表（运行中按数字键展开）", box=box.SIMPLE)
        table.add_column("键", style="accent", justify="right")
        table.add_column("标题", style="title")
        table.add_column("大小", style="muted")
        for item_id in reversed(self._recent[-9:]):
            item = self._items[item_id]
            slot = self._slot_for(item_id)
            meta = item.get("meta", "")
            table.add_row(f"[{slot}]", item["title"], meta)
        self.console.print(table)
        self.console.print("[fold]按 [accent]e[/] 展开最新 · [accent]1-9[/] 展开对应项[/]")
        self.console.print()

    def expand_slot(self, slot: int = 1) -> bool:
        if self.use_tui:
            return self._tui_call("tui_expand_slot", slot)
        if not self._recent:
            self.warn("没有可展开的内容")
            return False
        if slot < 1 or slot > len(self._recent):
            self.warn(f"无效 [{slot}]，输入 list 查看（1=最新）")
            return False
        item_id = self._recent[-slot]
        return self._print_expanded(item_id)

    def expand_item(self, item_id: int | None = None) -> bool:
        if item_id is None:
            return self.expand_slot(1)
        if item_id not in self._items:
            self.warn(f"未找到 #{item_id}，输入 list 查看")
            return False
        return self._print_expanded(item_id)

    def expand_last(self) -> bool:
        return self.expand_slot(1)

    def _print_expanded(self, item_id: int) -> bool:
        item = self._items[item_id]
        slot = self._slot_for(item_id)
        slot_hint = f" [{slot}]" if slot else ""
        self.console.print()
        self.console.print(
            Panel(
                item["renderable"],
                title=f"{item['title']}{slot_hint}",
                border_style=item.get("border", "cyan"),
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )
        self.console.print()
        return True

    def _register(
        self,
        *,
        title: str,
        content: str,
        kind: str,
        render_full: Callable[[str], RenderableType],
        border: str = "cyan",
        meta: str = "",
    ) -> tuple[int, int]:
        self._cache_seq += 1
        item_id = self._cache_seq
        safe_kind = re.sub(r"[^\w\-]+", "_", kind).strip("_") or "block"
        cache_path = UI_CACHE_DIR / f"{item_id:04d}_{safe_kind}.md"
        cache_path.write_text(content, encoding="utf-8")
        self._items[item_id] = {
            "title": title,
            "content": content,
            "renderable": render_full(content),
            "border": border,
            "meta": meta,
            "cache_path": cache_path,
        }
        self._recent.append(item_id)
        if len(self._recent) > 50:
            old = self._recent.pop(0)
            self._items.pop(old, None)
        slot = self._slot_for(item_id) or 1
        return item_id, slot

    def _compact_line(self, *, prefix: str, label: str, meta: str, style: str = "title") -> None:
        line = Text()
        line.append("  ", "")
        line.append(prefix, style="muted")
        line.append(" ", "")
        line.append(label, style=style)
        if meta:
            line.append(f"  ({meta})", style="muted")
        self.console.print(line)

    def _render_block(
        self,
        *,
        title: str,
        content: str,
        kind: str,
        render_full: Callable[[str], RenderableType],
        border_style: str,
        max_lines: int,
    ) -> None:
        max_chars = max_chars_for_lines(max_lines, self.console.width)
        collapsed = collapse_output(content, max_lines, max_chars)
        if not collapsed.overflow:
            self.console.print(
                Panel(
                    render_full(content),
                    title=title,
                    border_style=border_style,
                    box=box.ROUNDED,
                    padding=(0, 1),
                )
            )
            return

        lines = content.count("\n") + 1 if content else 0
        meta = f"{len(content)} 字 · {lines} 行"
        _, slot = self._register(
            title=title,
            content=content,
            kind=kind,
            render_full=render_full,
            border=border_style,
            meta=meta,
        )
        preview = render_full(collapsed.text)
        body = Group(preview, Text("  按 e 展开最新 · list 查看编号", style="fold"))
        self.console.print(
            Panel(
                body,
                title=title,
                border_style=border_style,
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    def rule(self, title: str = "") -> None:
        if self._tui_call("mount_rule", title):
            return
        self.console.print(Rule(title, style="accent"))

    def info(self, message: str) -> None:
        if self._tui_call("mount_line", f"[dim]{rich_escape(message)}[/]", classes="line-info"):
            return
        self.console.print(f"[muted]ℹ[/] {message}")

    def success(self, message: str) -> None:
        if self._tui_call("mount_line", f"[green]{rich_escape(message)}[/]", classes="line-info"):
            return
        self.console.print(f"[ok]✓[/] {message}")

    def warn(self, message: str) -> None:
        if self._tui_call("mount_line", f"[yellow]{rich_escape(message)}[/]", classes="line-warn"):
            return
        self.console.print(f"[warn]![/] {message}")

    def error(self, message: str) -> None:
        if self._tui_call("mount_line", f"[red]{rich_escape(message)}[/]", classes="line-err"):
            return
        self.console.print(Panel(message, title="错误", border_style="err", box=box.ROUNDED))

    def hook_log(self, tag: str, message: str) -> None:
        if tag in ("tool.before", "tool.after"):
            name = message
            if tag == "tool.before":
                self._dup_tool_counts[name] = self._dup_tool_counts.get(name, 0) + 1
            if self._dup_tool_counts.get(name, 0) > 1:
                return
            if self._tui_call("mount_line", f"[dim]hook {message}[/]", classes="line-hook"):
                return
            self.console.print(f"[hook]hook:{tag}[/] [tool]{message}[/]")
            return
        if self._tui_call("mount_line", f"[dim]hook:{tag} {message}[/]", classes="line-hook"):
            return
        self.console.print(f"[hook]hook:{tag}[/] [muted]{message}[/]")

    def banner(self) -> None:
        from brand import APP_NAME, TAGLINE, TAGLINE_ZH

        art = Text()
        art.append(APP_NAME, style="banner")
        body = Group(
            art,
            Text(TAGLINE, style="muted"),
            Text(TAGLINE_ZH, style="muted"),
            Text("DeepSeek Agent  ·  思考 · 工具 · Skills · Hooks · Session", style="muted"),
            Text("折叠: e=展开最新  1-9=展开对应项  list=列表  /thinking=思考开关", style="fold"),
            Text("界面: /tui  /plain 切换（保存偏好并重启）", style="fold"),
            Text("运行中: Esc 暂停  resume 继续  abort 中止", style="fold"),
        )
        self.console.print(Panel(body, border_style="cyan", box=box.DOUBLE, padding=(1, 2)))
        self.console.print()

    def show_startup(
        self,
        session_id: str | None,
        session_title: str,
        skills: list[str],
        hooks: list[str],
        current_time: str = "",
        ui_mode: str = "tui",
    ) -> None:
        if self.use_tui:
            self._tui_call(
                "mount_startup",
                session_id=session_id,
                session_title=session_title,
                skills=skills,
                hook_count=len(hooks),
                current_time=current_time,
                thinking_mode=self.thinking_mode,
                ui_mode=ui_mode,
            )
            return
        table = Table(show_header=False, box=box.SIMPLE, padding=(0, 1), expand=False)
        table.add_column("key", style="muted", no_wrap=True)
        table.add_column("val")
        if current_time:
            table.add_row("当前时间", current_time)
        if session_id:
            session_line = f"[id]{session_id}[/]  [title]{session_title}[/]"
        else:
            session_line = f"[title]{session_title}[/] [muted]（有对话后自动保存）[/]"
        table.add_row("Session", session_line)
        from ui.prefs import effort_label, model_label, ui_mode_label

        table.add_row("模型", f"{model_label()}  ·  强度 {effort_label()}")
        table.add_row("Skills", ", ".join(skills) if skills else "无")
        table.add_row("Hooks", str(len(hooks)) if hooks else "无")
        think = "展开" if self.thinking_mode == "show" else "折叠一行"
        table.add_row("思考显示", think)
        table.add_row("界面", ui_mode_label(ui_mode if ui_mode in ("tui", "plain") else None))
        table.add_row("展开", "[muted]e[/] 最新  [muted]1-9[/] 对应项  [muted]list[/] 列表")
        table.add_row("运行中", "[muted]Esc[/] 暂停  [muted]/model /effort[/] 切换")
        self.console.print(Panel(table, title="就绪", border_style="green", box=box.ROUNDED))
        self.console.print()

    def api_key_prompt(self) -> str:
        return Prompt.ask("[accent]DeepSeek API Key[/]（仅首次，将保存到本地 .env）")

    def session_choice_prompt(self, latest_id: str, latest_title: str) -> str:
        self.console.print()
        hint = (
            f"回车继续 [id]{latest_id}[/] [title]{latest_title}[/]，"
            "或输入 id / [accent]new[/]"
        )
        return Prompt.ask(hint, default="")

    def user_input(self) -> str:
        self.console.print()
        return Prompt.ask("[bold #7ee787]You[/] [dim]›[/] [bold white]")

    def goodbye(self) -> None:
        self.console.print()
        self.console.print(Panel("再见，期待下次对话", border_style="cyan", box=box.ROUNDED))
        self.console.print()

    def show_sessions(self, sessions) -> None:
        if self._tui_call("mount_sessions", sessions):
            return
        if not sessions:
            self.warn("暂无 session")
            return
        table = Table(title="Sessions", box=box.ROUNDED, border_style="cyan", expand=True)
        table.add_column("ID", style="id", no_wrap=True)
        table.add_column("标题", style="title")
        table.add_column("消息", justify="right", style="muted")
        table.add_column("更新时间", style="muted")
        for s in sessions:
            table.add_row(s.id, s.title, str(s.message_count), s.updated_at[:19].replace("T", " "))
        self.console.print()
        self.console.print(table)
        self.console.print()

    def show_help(self, commands: dict[str, str]) -> None:
        if self.use_tui:
            rows = [f"[bold]{cmd}[/]  {desc}" for cmd, desc in commands.items()]
            self._tui_call("mount_line", "\n".join(rows), classes="msg-info")
            return
        table = Table(title="命令", box=box.ROUNDED, border_style="magenta")
        table.add_column("命令", style="accent", no_wrap=True)
        table.add_column("说明")
        for cmd, desc in commands.items():
            table.add_row(cmd, desc)
        self.console.print()
        self.console.print(table)
        self.console.print()

    @property
    def reply_was_streamed(self) -> bool:
        return self._reply_streamed

    def clear_reply_streamed(self) -> None:
        self._reply_streamed = False

    @property
    def thinking_was_shown(self) -> bool:
        return self._thinking_shown

    def clear_thinking_shown(self) -> None:
        self._thinking_shown = False

    def show_context_progress(self, usage: dict) -> None:
        """更新上下文占用进度（状态栏 / 纯终端一行）。"""
        label = usage.get("label") or ""
        if self._tui_call("show_context_progress", usage):
            return
        if label:
            self.console.print(f"[muted]{label}[/]")

    def llm_round_start(self, round_idx: int) -> None:
        self._reply_streamed = False
        self._thinking_shown = False
        if self._tui_call("llm_round_start", round_idx):
            return
        self.console.print(f"[muted]第 {round_idx} 轮…[/]")

    def llm_activity_update(self, text: str) -> None:
        if self._tui_queue_activity(text):
            return
        if self._plain_stream_live is None:
            from rich.live import Live
            from rich.text import Text as RText
            self._plain_stream_live = Live(
                RText(text, style="muted italic"),
                console=self.console,
                refresh_per_second=12,
            )
            self._plain_stream_live.start()
        else:
            from rich.text import Text as RText
            self._plain_stream_live.update(RText(text, style="muted italic"))

    def llm_activity_clear(self) -> None:
        if self._tui_call("llm_activity_clear"):
            return
        if self._plain_stream_live is not None:
            self._plain_stream_live.stop()
            self._plain_stream_live = None

    def stream_thinking_begin(self) -> None:
        self._thinking_shown = True
        self._tui_think_pending = ""
        if self._tui_call("stream_thinking_begin"):
            return

    def stream_thinking_update(self, text: str) -> None:
        self._tui_think_pending = text
        if self._tui:
            return

    def stream_thinking_end(self, text: str) -> None:
        self._thinking_shown = bool(text)
        self._tui_think_pending = text
        self._tui_think_queued = False
        if self._tui_call("stream_thinking_end", text):
            return

    def stream_reply_begin(self) -> None:
        self.llm_activity_clear()
        self._tui_stream_pending = ""
        self._tui_stream_queued = False
        if self._tui_call("stream_reply_begin"):
            return
        from rich.live import Live
        from rich.markdown import Markdown as RMarkdown
        self._plain_stream_live = Live(
            Panel(RMarkdown(""), title="Assistant", border_style="white", box=box.ROUNDED, padding=(0, 1)),
            console=self.console,
            refresh_per_second=12,
        )
        self._plain_stream_live.start()

    def stream_reply_update(self, content: str) -> None:
        # TUI：只写 pending，由 tick 刷新，避免每 token call_from_thread 阻塞
        self._tui_stream_pending = content
        if self._tui:
            return
        if self._plain_stream_live is not None:
            from rich.markdown import Markdown as RMarkdown
            self._plain_stream_live.update(
                Panel(
                    RMarkdown(content + " ▌"),
                    title="Assistant",
                    border_style="white",
                    box=box.ROUNDED,
                    padding=(0, 1),
                )
            )

    def stream_reply_end(self, content: str) -> None:
        self._reply_streamed = bool(content)
        self._tui_stream_queued = False
        if self._tui_call("stream_reply_end", content):
            self.llm_activity_clear()
            return
        if self._plain_stream_live is not None:
            from rich.markdown import Markdown as RMarkdown
            self._plain_stream_live.update(
                Panel(
                    RMarkdown(content),
                    title="Assistant",
                    border_style="white",
                    box=box.ROUNDED,
                    padding=(0, 1),
                )
            )
            self._plain_stream_live.stop()
            self._plain_stream_live = None
        else:
            self.show_reply(content)

    def stream_reply_cancel(self) -> None:
        self._tui_stream_queued = False
        if self._tui_call("stream_reply_cancel"):
            return
        if self._plain_stream_live is not None:
            self._plain_stream_live.stop()
            self._plain_stream_live = None

    def show_user_message(self, content: str) -> None:
        if self.use_tui:
            collapsed = not self.verbose and len(content) >= 200
            self._tui_call("mount_user", content, collapsed=collapsed)
            return
        if self.verbose or len(content) < 120:
            self.console.print(Panel(Text(content), title="You", border_style="green", box=box.ROUNDED))
            return
        lines = content.count("\n") + 1
        summary = content.strip().splitlines()[0]
        if len(summary) > 50:
            summary = summary[:49] + "…"
        _, _slot = self._register(
            title="You",
            content=content,
            kind="user",
            render_full=lambda t: Text(t),
            border="green",
            meta=f"{len(content)} 字",
        )
        self._compact_line(prefix="You:", label=summary, meta=f"{lines} 行", style="user")

    def show_thinking(self, text: str) -> None:
        self._thinking_shown = True
        if self._tui_call("tui_show_thinking", text):
            return
        if self.verbose or self.thinking_mode == "show":
            self._render_block(
                title="思考",
                content=text,
                kind="thinking",
                render_full=lambda t: Text(t, style="think"),
                border_style="yellow",
                max_lines=THINK_MAX_LINES,
            )
            return

        title, _ = reasoning_summary(text)
        label = title or first_line_summary(text)
        lines = text.count("\n") + 1 if text else 0
        _, _slot = self._register(
            title="思考",
            content=text,
            kind="thinking",
            render_full=lambda t: Text(t, style="think"),
            border="yellow",
            meta=f"{len(text)} 字 · {lines} 行",
        )
        self._compact_line(prefix="+ Thought:", label=label, meta=f"{lines} 行", style="think")

    INLINE_TOOLS = frozenset({
        "read", "grep", "search_symbol", "load_skill",
    })

    def show_tool_round(self, round_idx: int, msg) -> None:
        self.rule(f"第 {round_idx} 轮 · 工具调用")
        reasoning = getattr(msg, "reasoning_content", None)
        if reasoning:
            self.show_thinking(reasoning)
        if not msg.tool_calls:
            return

        from collections import Counter

        name_counts = Counter(tc.function.name for tc in msg.tool_calls)
        seen: set[str] = set()

        for tc in msg.tool_calls:
            name = tc.function.name
            if name in seen:
                continue
            seen.add(name)
            count = name_counts[name]
            label = f"{name} ×{count}" if count > 1 else name

            try:
                args = json.loads(tc.function.arguments or "{}")
                args_text = json.dumps(args, ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                args_text = tc.function.arguments or ""

            if self.use_tui:
                if count > 1 or not args_text.strip() or args_text.strip() == "{}":
                    self._tui_call("mount_line", f"  > {label}", classes="line-tool")
                else:
                    self._tui_call("tui_show_tool_call", label, args_text)
                continue

            if count > 1 or not args_text.strip() or args_text.strip() == "{}":
                self._compact_line(prefix="->", label=label, meta="", style="tool")
                continue

            def render_args(t: str, tool_name: str = name) -> RenderableType:
                return Group(Text(tool_name, style="tool"), Syntax(t, "json", theme="monokai", background_color="default"))

            _, _slot = self._register(
                title=f"Tool · {name} 参数",
                content=args_text,
                kind=f"args_{name}",
                render_full=render_args,
                border="blue",
                meta=f"{len(args_text)} 字",
            )
            self._compact_line(prefix="->", label=name, meta="参数", style="tool")

    def show_tool_result(self, name: str, result: str) -> None:
        if not self.verbose and name in self.INLINE_TOOLS:
            n = self._dup_result_counts.get(name, 0) + 1
            self._dup_result_counts[name] = n
            if n > 1:
                return

        if self._tui_call("tui_show_tool_result", name, result):
            return
        if name in self.INLINE_TOOLS and not self.verbose and len(result) < 400:
            first = next(iter(result.strip().splitlines()), "(empty)")
            preview = first[:72]
            if len(first) > 72:
                preview += "…"
            self.console.print(Text.assemble(("  ok ", "ok"), (name, "tool"), (f"  {preview}", "muted")))
            return

        lang = "json" if result.lstrip().startswith(("{", "[")) else "text"

        def render_result(t: str) -> RenderableType:
            if lang == "json":
                try:
                    formatted = json.dumps(json.loads(t), ensure_ascii=False, indent=2)
                    return Syntax(formatted, "json", theme="monokai", background_color="default")
                except json.JSONDecodeError:
                    pass
            return Text(t, style="result")

        lines = result.count("\n") + 1 if result else 0
        _, _slot = self._register(
            title=f"Tool · {name} 结果",
            content=result,
            kind=f"result_{name}",
            render_full=render_result,
            border="cyan",
            meta=f"{len(result)} 字 · {lines} 行",
        )
        self._compact_line(prefix="ok", label=name, meta=f"{len(result)} 字", style="tool")

    def show_reply(self, content: str) -> None:
        if self._tui_call("mount_reply", content):
            return
        self.console.print(
            Panel(
                Markdown(content),
                title="Assistant",
                border_style="white",
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    def show_assistant_turn(self, msg) -> None:
        reasoning = getattr(msg, "reasoning_content", None)
        if reasoning:
            self.show_thinking(reasoning)
        if msg.tool_calls:
            self.show_tool_round(0, msg)
            return
        if msg.content:
            self.show_reply(msg.content)

    def hydrate_messages(self, messages: list[dict]) -> None:
        """把已保存的对话历史回放到界面（OpenCode sync.session.sync 风格）。"""
        tool_names: dict[str, str] = {}
        for msg in messages:
            role = msg.get("role")
            if role == "system":
                continue
            if role == "user":
                content = msg.get("content") or ""
                if content:
                    self.show_user_message(content)
                continue
            if role == "assistant":
                reasoning = msg.get("reasoning_content")
                if reasoning:
                    self.show_thinking(reasoning)
                tool_calls = msg.get("tool_calls") or []
                if tool_calls:
                    self.rule("工具调用")
                    for tc in tool_calls:
                        if not isinstance(tc, dict):
                            continue
                        fn = tc.get("function") or {}
                        name = str(fn.get("name") or "tool")
                        tid = str(tc.get("id") or "")
                        if tid:
                            tool_names[tid] = name
                        raw_args = fn.get("arguments") or ""
                        try:
                            parsed = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                            args_text = json.dumps(parsed, ensure_ascii=False, indent=2)
                        except (TypeError, ValueError, json.JSONDecodeError):
                            args_text = str(raw_args)
                        if self.use_tui:
                            if not args_text.strip() or args_text.strip() == "{}":
                                self._tui_call("mount_line", f"  > {name}", classes="line-tool")
                            else:
                                self._tui_call("tui_show_tool_call", name, args_text)
                        else:
                            self._compact_line(prefix="->", label=name, meta="", style="tool")
                content = msg.get("content")
                if content:
                    self.show_reply(content)
                continue
            if role == "tool":
                tid = str(msg.get("tool_call_id") or "")
                name = tool_names.get(tid, "tool")
                self.show_tool_result(name, msg.get("content") or "")

    @contextmanager
    def llm_status(self, message: str):
        if self.use_tui:
            self._tui_call("set_busy", message)
            try:
                yield
            finally:
                self._tui_call("set_idle")
        else:
            with self.console.status(Spinner("dots", text=message, style="accent"), spinner_style="accent"):
                yield

    def hook_blocked(self, message: str = "本轮输入被 hook 拦截") -> None:
        self.warn(message)


ui = AgentUI()
