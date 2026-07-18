"""Session command handler: /new, /resume, /delete, /title, /reload, etc."""

from __future__ import annotations

import os
import sys

from core.context import AgentContext
from session import SessionInfo, SessionStore
from ui import ui
from ui.prefs import (
    ALWAYS_ON_TOOLS,
    AVAILABLE_EFFORTS,
    EFFORT_LABELS,
    MODEL_LABELS,
    effort_label,
    get_model,
    get_reasoning_effort,
    is_mcp_tool_enabled,
    is_skill_enabled,
    is_tool_enabled,
    model_label,
    set_last_session_id,
    set_model,
    set_reasoning_effort,
    set_ui_mode,
    toggle_mcp_tool,
    toggle_skill,
    toggle_tool,
    ui_mode_label,
)

HANDLED_RESTART = "__restart__"
HANDLED_REEXEC = "__reexec__"

# `/` 菜单与 /help 只展示主命令；别名仍可手输，见 COMMAND_ALIASES
SESSION_COMMANDS = {
    "/help": "显示全部斜杠指令",
    "/new": "创建新 session",
    "/sessions": "打开 Session 次级选择界面",
    "/resume": "恢复 session，用法: /resume <id>",
    "/delete": "删除 session，用法: /delete <id>",
    "/title": "重命名当前 session，用法: /title <名称>",
    "/reload": "重新扫描 skills / tools / mcp 并刷新 system prompt",
    "/reload_comp": "全面重启（退出进程并重新进入，保留当前 session）",
    "/expand": "展开折叠（TUI 点标题；纯终端用 /expand 或 /e）",
    "/list": "列出可展开项（1=最新）",
    "/model": "打开模型选择；或 /model [pro|flash]",
    "/effort": "打开思考强度选择；或 /effort [high|max|off]",
    "/tools": "管理工具开关；或 /tools [name] 切换",
    "/skills": "管理 Skills 开关；或 /skills [name] 切换",
    "/mcp": "管理 MCP server / 工具开关",
    "/thinking": "切换思考过程展开/折叠一行",
    "/verbose": "切换长内容默认全部展开",
    "/tui": "切换为 TUI 全屏界面（保存偏好并重启）",
    "/plain": "切换为纯终端 Rich 界面（保存偏好并重启）",
    "/ui": "查看当前界面模式",
    "/quit": "退出 fiagent",
}

# 别名 → 主命令（可执行；补全时若键入别名则带出主命令）
COMMAND_ALIASES = {
    "/session": "/sessions",
    "/reexec": "/reload_comp",
    "/restart": "/reload_comp",
    "/re": "/reload_comp",
    "/rc": "/reload_comp",
    "/e": "/expand",
    "/tool": "/tools",
    "/skill": "/skills",
    "/mcps": "/mcp",
    "/exit": "/quit",
    "/q": "/quit",
}

# 裸 `/` 菜单顺序：常用靠前，避免字母序把 /reload_comp 挤出首屏
_MENU_ORDER = (
    "/help",
    "/new",
    "/sessions",
    "/resume",
    "/reload",
    "/reload_comp",
    "/model",
    "/effort",
    "/tools",
    "/skills",
    "/mcp",
    "/thinking",
    "/verbose",
    "/expand",
    "/list",
    "/title",
    "/delete",
    "/tui",
    "/plain",
    "/ui",
    "/quit",
)
_MENU_RANK = {cmd: i for i, cmd in enumerate(_MENU_ORDER)}


def match_slash_command(cmd: str, query: str) -> bool:
    """`/` 菜单筛选：前缀优先；短查询不误伤（/r 不匹配 /effort）。

    - `/re` → `/reload` `/resume` `/reload_comp`（主命令前缀）
    - `/comp` 或 `/rc` → `/reload_comp`（分段前缀或别名映射）
    """
    q = (query or "").strip().lower()
    c = cmd.lower()
    if not q or q == "/":
        return True
    if c.startswith(q) or c == q:
        return True
    needle = q[1:] if q.startswith("/") else q
    if not needle:
        return True
    name = c[1:]
    segments = name.split("_")
    # 分段前缀：/comp → reload_comp；单字母不做全文子串（避免 r∈effort）
    if len(needle) >= 2 and any(seg.startswith(needle) for seg in segments):
        return True
    if len(needle) >= 3 and needle in name:
        return True
    return False


def _slash_sort_key(cmd: str, query: str) -> tuple:
    q = (query or "").strip().lower() or "/"
    c = cmd.lower()
    needle = q[1:] if q.startswith("/") else q
    if q == "/" or not needle:
        return (_MENU_RANK.get(c, 999), c)
    if c == q:
        return (0, 0, c)
    if c.startswith(q):
        # /reload 与 /reload_comp 成组，且 /reload 在前；避免 /resume 插在中间
        base, _, rest = c.partition("_")
        return (1, base, 0 if not rest else 1, len(c), c)
    name = c[1:]
    if any(seg.startswith(needle) for seg in name.split("_")):
        return (2, len(c), c)
    return (3, len(c), c)


def list_slash_matches(query: str) -> list[tuple[str, str]]:
    """返回 (cmd, desc)：只列主命令；键入别名时带出对应主命令。"""
    q = (query or "").strip().lower()
    matched = [
        (cmd, desc)
        for cmd, desc in SESSION_COMMANDS.items()
        if match_slash_command(cmd, q)
    ]
    # 精确别名或别名前缀 → 保证主命令出现（如 /rc → /reload_comp）
    for alias, canon in COMMAND_ALIASES.items():
        if canon not in SESSION_COMMANDS:
            continue
        if q == alias or (q != "/" and alias.startswith(q)):
            if all(c != canon for c, _ in matched):
                matched.append((canon, SESSION_COMMANDS[canon]))
    matched.sort(key=lambda item: _slash_sort_key(item[0], q))
    return matched


def _build_reexec_args(*, resume_id: str | None = None) -> list[str]:
    script = os.path.abspath(sys.argv[0])
    args = [sys.executable, script]
    skip_next = False
    for arg in sys.argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg in ("--resume", "-r"):
            skip_next = True
            continue
        if arg.startswith("--resume="):
            continue
        if arg == "--list":
            continue
        args.append(arg)
    if resume_id:
        args.extend(["--resume", resume_id])
    return args


def _reset_terminal() -> None:
    """Textual 退出后复位鼠标/备用屏，避免重启后点不动。"""
    seq = (
        "\033[?1000l\033[?1002l\033[?1003l\033[?1006l"
        "\033[?25h\033[?1049l\033[0m"
    )
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.write(seq)
            stream.flush()
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
            # ENABLE_PROCESSED_INPUT|LINE|ECHO|EXTENDED|INSERT|QUICK_EDIT
            ctypes.windll.kernel32.SetConsoleMode(handle, 0x00A7)
        except Exception:
            pass


def reexec_self(*, resume_id: str | None = None) -> None:
    """全面重启：先复位终端，再拉起新进程（Windows 不用 execv，避免鼠标失效）。"""
    import subprocess
    import time

    args = _build_reexec_args(resume_id=resume_id)
    _reset_terminal()
    time.sleep(0.15)
    if sys.platform == "win32":
        # Windows 上 os.execv 常导致控制台鼠标追踪残留，新 Textual 点不了。
        # 用 call 阻塞等待，保持同一控制台句柄由当前进程树持有。
        raise SystemExit(subprocess.call(args))
    os.execv(args[0], args)


def _new_draft(
    ctx: AgentContext,
) -> tuple[None, list[dict], bool]:
    ctx.refresh()
    return None, ctx.fresh_messages(), True


def handle_session_command(
    cmd: str,
    store: SessionStore,
    ctx: AgentContext,
    current: SessionInfo | None,
) -> tuple[SessionInfo | None, list[dict] | None, bool | str]:
    parts = cmd.split(maxsplit=1)
    name = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if name == "/help":
        ui.show_help(SESSION_COMMANDS)
        return current, None, True

    if name == "/sessions" or name == "/session":
        ui.show_sessions(store.list_sessions())
        return current, None, True

    if name == "/reload":
        ctx.refresh()
        ui.success("已重新扫描 skills / tools / mcp")
        return current, None, True

    if name in ("/reload_comp", "/reexec", "/restart", "/re", "/rc"):
        ui.success("正在全面重启…")
        return current, None, HANDLED_REEXEC

    if name == "/new":
        from ui.prefs import set_last_session_id

        set_last_session_id(None)
        ui.success("已开始新对话（有消息后将自动保存）")
        return _new_draft(ctx)

    if name == "/resume":
        if not arg:
            ui.warn("用法: /resume <id>")
            return current, None, True
        info = store.find(arg)
        if info is None:
            ui.warn(f"未找到 session: {arg}")
            return current, None, True
        messages = store.load_messages(info.id)
        if not messages:
            messages = ctx.fresh_messages()
        else:
            ctx.refresh()
            ctx.sync_system_message(messages)
        from ui.prefs import set_last_session_id

        set_last_session_id(info.id)
        ui.success(f"已恢复 session [{info.id}] {info.title}")
        return info, messages, True

    if name == "/delete":
        if not arg:
            ui.warn("用法: /delete <id>")
            return current, None, True
        target = store.find(arg)
        if target is None:
            ui.warn(f"未找到 session: {arg}")
            return current, None, True
        store.delete(target.id)
        ui.success(f"已删除 session [{target.id}]")
        if current and target.id == current.id:
            return _new_draft(ctx)
        return current, None, True

    if name == "/title":
        if not arg:
            ui.warn("用法: /title <名称>")
            return current, None, True
        if current is None:
            ui.warn("当前对话尚未保存，请先发送一条消息")
            return current, None, True
        store.rename(current.id, arg)
        updated = store.get(current.id)
        if updated:
            current = updated
        ui.success(f"已重命名为: {arg}")
        return current, None, True

    if name in ("/expand", "/e"):
        slot = 1
        if arg:
            try:
                slot = int(arg.lstrip("#[]"))
            except ValueError:
                ui.warn("用法: /e [1-9]（1=最新）")
                return current, None, True
        ui.expand_slot(slot)
        return current, None, True

    if name == "/list":
        ui.list_collapsed()
        return current, None, True

    if name == "/model":
        aliases = {
            "pro": "deepseek-v4-pro",
            "flash": "deepseek-v4-flash",
            "deepseek-v4-pro": "deepseek-v4-pro",
            "deepseek-v4-flash": "deepseek-v4-flash",
        }
        if not arg:
            ui.info(
                f"当前模型: {model_label()}（{get_model()}）"
                f"  可选: pro / flash"
            )
            return current, None, True
        target = aliases.get(arg.lower())
        if target is None:
            ui.warn("用法: /model [pro|flash]")
            return current, None, True
        set_model(target)
        ui.success(f"模型已切换为 {MODEL_LABELS[target]}（{target}）")
        return current, None, True

    if name == "/effort":
        if not arg:
            ui.info(
                f"当前思考强度: {effort_label()}（{get_reasoning_effort()}）"
                f"  可选: high / max / off"
            )
            return current, None, True
        target = arg.lower()
        if target not in AVAILABLE_EFFORTS:
            ui.warn("用法: /effort [high|max|off]")
            return current, None, True
        set_reasoning_effort(target)
        ui.success(f"思考强度已切换为 {EFFORT_LABELS[target]}")
        return current, None, True

    if name in ("/tools", "/tool"):
        ctx.refresh()
        if not arg:
            from ui.capability_groups import group_tools

            toggleable = [
                (tname, summary)
                for tname, summary in ctx.tools.all()
                if tname not in ALWAYS_ON_TOOLS
            ]
            lines = []
            for cat_id, hint, members in group_tools([n for n, _ in toggleable]):
                by = dict(toggleable)
                on_n = sum(1 for n in members if is_tool_enabled(n))
                lines.append(f"\n[{cat_id}] {on_n}/{len(members)} · {hint}")
                for tname in members:
                    mark = "[green]●[/]" if is_tool_enabled(tname) else "[red]●[/]"
                    lines.append(f"  {mark} {tname}  {by.get(tname, '')}")
            lines.append("\n★ 常开: " + ", ".join(sorted(ALWAYS_ON_TOOLS)))
            ui.info(
                "工具开关（绿开红关）\n"
                + "\n".join(lines)
                + "\n用法: /tools <name> 切换；TUI 用 Ctrl+P → 管理工具"
            )
            return current, None, True
        if arg in ALWAYS_ON_TOOLS:
            ui.warn(f"`{arg}` 为常开工具，不可关闭")
            return current, None, True
        if ctx.tools.get(arg) is None:
            ui.warn(f"未找到工具: {arg}")
            return current, None, True
        enabled = toggle_tool(arg)
        ui.success(f"工具 `{arg}` 已{'启用' if enabled else '禁用'}")
        return current, None, True

    if name in ("/skills", "/skill"):
        ctx.refresh()
        if not arg:
            from ui.capability_groups import group_skills

            skills = list(ctx.skills.all())
            by = {s.name: s for s in skills}
            lines = []
            for cat_id, hint, members in group_skills([s.name for s in skills]):
                on_n = sum(1 for n in members if is_skill_enabled(n))
                lines.append(f"\n[{cat_id}] {on_n}/{len(members)} · {hint}")
                for sname in members:
                    skill = by[sname]
                    mark = "[green]●[/]" if is_skill_enabled(sname) else "[red]●[/]"
                    tag = "内置" if skill.bundled else "用户"
                    lines.append(f"  {mark} {sname}  [{tag}] {skill.description}")
            ui.info(
                "Skills 开关（绿开红关）\n"
                + "\n".join(lines)
                + "\n用法: /skills <name> 切换；TUI 用 Ctrl+P → 管理 Skills"
            )
            return current, None, True
        if ctx.skills.get(arg) is None:
            ui.warn(f"未找到 skill: {arg}")
            return current, None, True
        enabled = toggle_skill(arg)
        ui.success(f"Skill `{arg}` 已{'启用' if enabled else '禁用'}")
        return current, None, True

    if name in ("/mcp", "/mcps"):
        ctx.refresh()
        if not arg:
            servers = ctx.mcp.servers()
            if not servers:
                ui.warn("未配置 MCP server（编辑 mcps/mcp.json）")
                return current, None, True
            lines = []
            for server in servers:
                sm = "[green]●[/]" if server.enabled else "[red]●[/]"
                lines.append(f"\n{sm} server `{server.id}`")
                for tool in server.tools:
                    effective = server.enabled and is_mcp_tool_enabled(tool.name)
                    tm = "[green]●[/]" if effective else "[red]●[/]"
                    lines.append(f"  {tm} {tool.name}  {tool.description}")
            ui.info(
                "MCP 开关（绿开红关）\n"
                + "\n".join(lines)
                + "\n用法: /mcp server <id> 切换整机；/mcp tool <name> 切换工具"
                + "\nTUI 用 Ctrl+P → 管理 MCP"
            )
            return current, None, True
        parts = arg.split(None, 1)
        kind = parts[0].lower()
        target = parts[1].strip() if len(parts) > 1 else ""
        if kind in ("server", "srv", "s") and target:
            try:
                enabled = ctx.mcp.toggle_server(target)
            except KeyError:
                ui.warn(f"未找到 MCP server: {target}")
                return current, None, True
            ui.success(f"MCP server `{target}` 已{'启用' if enabled else '关闭'}")
            return current, None, True
        if kind in ("tool", "t") and target:
            # 确认工具存在于任一 server
            found = any(
                t.name == target for s in ctx.mcp.servers() for t in s.tools
            )
            if not found:
                ui.warn(f"未找到 MCP 工具: {target}")
                return current, None, True
            enabled = toggle_mcp_tool(target)
            ctx.refresh()
            ui.success(f"MCP 工具 `{target}` 已{'启用' if enabled else '禁用'}")
            return current, None, True
        ui.warn("用法: /mcp | /mcp server <id> | /mcp tool <name>")
        return current, None, True

    if name == "/thinking":
        mode = ui.toggle_thinking()
        ui.success("思考过程已展开" if mode == "show" else "思考过程已折叠为一行")
        return current, None, True

    if name == "/verbose":
        on = ui.toggle_verbose()
        ui.success("长内容默认展开" if on else "长内容默认折叠")
        return current, None, True

    if name == "/ui":
        ui.info(f"当前界面: {ui_mode_label()}（/tui 或 /plain 切换并重启）")
        return current, None, True

    if name == "/tui":
        set_ui_mode("tui")
        ui.success("已切换为 TUI 模式，正在全面重启…")
        return current, None, HANDLED_REEXEC

    if name == "/plain":
        set_ui_mode("plain")
        ui.success("已切换为纯终端模式，正在全面重启…")
        return current, None, HANDLED_REEXEC

    return current, None, False
