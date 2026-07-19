"""Atrading — DeepSeek ReAct Agent for A-share quantitative research.

Usage:
    atrading                 # 默认 TUI（需 pip install -e .）
    atrading --plain         # 纯终端
    atrading --tui           # 强制 TUI
    atrading --resume <id>   # 恢复 session
    atrading --list          # 列出 sessions
    python -m atrading       # 等价入口
"""

import sys

from paths import PROJECT_ROOT

# Flat layout: ensure repo root is on sys.path before importing siblings.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.cli import bootstrap, parse_args, resolve_ui_mode
from core.commands import (
    HANDLED_REEXEC,
    HANDLED_RESTART,
    SESSION_COMMANDS,
    handle_session_command,
    reexec_self,
)
from core.loop import run_agent_turn
from core.turn_control import TurnAborted
from ui import ui


def main_plain(args) -> None:
    client, hooks, store, ctx, current, messages, loaded_hooks = bootstrap(args)
    reexec_resume: str | None | bool = False

    try:
        ui.banner()
        ui.show_startup(
            session_id=current.id if current else None,
            session_title=current.title if current else "新对话",
            skills=[s.name for s in ctx.skills.all()],
            hooks=loaded_hooks,
            current_time=ctx.format_now(),
            ui_mode="plain",
        )
        ui.hydrate_messages(messages)

        while True:
            try:
                user_input = ui.user_input()
            except (EOFError, KeyboardInterrupt):
                ui.goodbye()
                break

            if user_input.strip().lower() in ("quit", "exit", "q"):
                ui.goodbye()
                break

            if not user_input.strip():
                continue

            if user_input.startswith("/"):
                current, new_messages, handled = handle_session_command(
                    user_input, store, ctx, current
                )
                if not handled:
                    ui.warn(f"未知命令: {user_input}，输入 /help 查看帮助")
                elif handled in (HANDLED_REEXEC, HANDLED_RESTART):
                    reexec_resume = current.id if current else None
                    break
                else:
                    if new_messages is not None:
                        messages = new_messages
                        ui.show_startup(
                            session_id=current.id if current else None,
                            session_title=current.title if current else "新对话",
                            skills=[s.name for s in ctx.skills.all()],
                            hooks=loaded_hooks,
                            current_time=ctx.format_now(),
                            ui_mode="plain",
                        )
                        ui.hydrate_messages(messages)
                    elif user_input.strip().lower() == "/reload":
                        ctx.sync_system_message(messages)
                continue

            turn_ctx = hooks.emit("turn.start", {
                "input": user_input,
                "session_id": current.id if current else None,
            })
            if turn_ctx.cancel:
                ui.hook_blocked()
                continue
            user_input = turn_ctx.get("input", user_input)

            turn_start = len(messages)
            ui.show_user_message(user_input)
            messages.append({"role": "user", "content": user_input})

            try:
                run_agent_turn(client, messages, ctx, hooks)
                if current is None:
                    current = store.create()
                    store.auto_title(current.id, user_input)
                    updated = store.get(current.id)
                    if updated:
                        current = updated
                store.save_messages(current.id, messages)
                from ui.prefs import set_last_session_id

                set_last_session_id(current.id)
                hooks.emit("turn.end", {
                    "input": user_input,
                    "messages": messages,
                    "session_id": current.id,
                })
            except TurnAborted:
                del messages[turn_start:]
                ui.warn("本轮已中止，对话未保存本轮内容")
            except Exception as e:
                ui.error(str(e))
                del messages[turn_start:]
    finally:
        hooks.emit("session.end", {
            "session_id": current.id if current else None,
            "messages": messages,
        })
        store.close()

    if reexec_resume is not False:
        reexec_self(resume_id=reexec_resume if isinstance(reexec_resume, str) else None)


def main():
    args = parse_args()
    if args.list:
        from core.cli import bootstrap as _bootstrap
        s = _bootstrap(args)[2]
        ui.show_sessions(s.list_sessions())
        s.close()
        return

    use_plain = resolve_ui_mode(args)

    if use_plain:
        main_plain(args)
        return

    try:
        import textual  # noqa: F401
        from ui.tui.app import run_tui  # noqa: F401
    except ImportError as exc:
        ui.warn(f"未安装 TUI 依赖 ({exc})。请执行:\n  \"{sys.executable}\" -m pip install textual")
        main_plain(args)
        return

    client, hooks, store, ctx, current, messages, loaded_hooks = bootstrap(args)

    run_tui(
        client=client, ctx=ctx, hooks=hooks, store=store,
        messages=messages, current=current, loaded_hooks=loaded_hooks,
        session_commands=SESSION_COMMANDS,
        handle_command=handle_session_command,
        args=args,
    )


if __name__ == "__main__":
    main()
