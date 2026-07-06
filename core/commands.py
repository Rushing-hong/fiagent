"""Session command handler: /new, /resume, /delete, /title, /reload, etc."""

from core.context import AgentContext
from session import SessionInfo, SessionStore
from ui import ui
from ui.prefs import set_ui_mode, ui_mode_label

HANDLED_RESTART = "__restart__"

SESSION_COMMANDS = {
    "/new": "创建新 session",
    "/sessions": "列出所有 session",
    "/resume": "恢复 session，用法: /resume <id>",
    "/delete": "删除 session，用法: /delete <id>",
    "/title": "重命名当前 session，用法: /title <名称>",
    "/reload": "重新扫描 skills / tools / mcp 并刷新 system prompt",
    "/expand": "展开折叠（TUI 模式请点击标题；纯终端用 /e）",
    "/list": "列出可展开项（1=最新）",
    "/thinking": "切换思考过程展开/折叠一行",
    "/verbose": "切换长内容默认全部展开",
    "/tui": "切换为 TUI 全屏界面（保存偏好并重启）",
    "/plain": "切换为纯终端 Rich 界面（保存偏好并重启）",
    "/ui": "查看当前界面模式",
    "/help": "显示 session 命令帮助",
}


def _new_draft(ctx: AgentContext) -> tuple[None, list[dict]]:
    ctx.refresh()
    return None, ctx.fresh_messages()


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

    if name == "/sessions":
        ui.show_sessions(store.list_sessions())
        return current, None, True

    if name == "/reload":
        ctx.refresh()
        ui.success("已重新扫描 skills / tools / mcp")
        return current, None, True

    if name == "/new":
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
        ui.success("已切换为 TUI 模式，正在退出请重新运行 python agent.py")
        return current, None, HANDLED_RESTART

    if name == "/plain":
        set_ui_mode("plain")
        ui.success("已切换为纯终端模式，正在退出请重新运行 python agent.py")
        return current, None, HANDLED_RESTART

    return current, None, False
