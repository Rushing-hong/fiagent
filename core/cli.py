"""CLI argument parsing, environment loading, and bootstrap logic."""

import argparse
import json
import os
import sys

from openai import OpenAI

from core.context import AgentContext
from core.llm.catalog import DEFAULT_MODEL_ID, get_model_spec
from core.llm.client import (
    MissingApiKeyError,
    first_configured_provider,
    get_client_for_model,
    resolve_api_key,
)
from hooks.registry import HookRegistry
from paths import ENV_PATH, PROJECT_ROOT
from session import RETENTION_DAYS, SessionInfo, SessionStore
from ui import ui
from ui.prefs import get_model, get_ui_mode, get_last_session_id, set_last_session_id, set_model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Atrading Agent with sessions")
    parser.add_argument("--resume", metavar="ID", help="恢复指定 session")
    parser.add_argument("--list", action="store_true", help="列出所有 session 后退出")
    ui_group = parser.add_mutually_exclusive_group()
    ui_group.add_argument("--plain", action="store_true", help="本次使用纯终端 Rich 界面（不写入偏好）")
    ui_group.add_argument("--tui", action="store_true", help="本次使用 Textual TUI 全屏界面（不写入偏好）")
    ui_group.add_argument("--web", action="store_true", help="本次使用本机网页界面（不写入偏好）")
    return parser.parse_args()


def resolve_ui_mode(args: argparse.Namespace) -> str:
    """返回 ``tui`` / ``plain`` / ``web``。"""
    if getattr(args, "web", False):
        return "web"
    if args.plain:
        return "plain"
    if args.tui:
        return "tui"
    env = os.getenv("FIAGENT_PLAIN_UI", "").strip().lower()
    if env in ("1", "true", "yes"):
        return "plain"
    if env in ("0", "false", "no"):
        return "tui"
    web_env = os.getenv("FIAGENT_WEB_UI", "").strip().lower()
    if web_env in ("1", "true", "yes"):
        return "web"
    return get_ui_mode()


def _load_local_env() -> None:
    if not ENV_PATH.exists():
        return
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _save_env_key(key: str, value: str) -> None:
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    found = False
    out: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw:
            out.append(raw)
            continue
        k, sep, _ = raw.partition("=")
        if sep and k.strip() == key:
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(raw)
    if not found:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        import stat
        ENV_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except (OSError, AttributeError):
        pass
    os.environ[key] = value


def _ensure_llm_ready() -> OpenAI:
    """Resolve preferred model + API key; prompt once if the active provider lacks a key."""
    model_id = get_model()
    try:
        spec = get_model_spec(model_id)
    except KeyError:
        set_model(DEFAULT_MODEL_ID)
        model_id = DEFAULT_MODEL_ID
        spec = get_model_spec(model_id)

    from core.llm.providers import get_provider

    provider = get_provider(spec.provider)
    if resolve_api_key(provider):
        return get_client_for_model(model_id)

    # Fall back to any configured provider and switch default model if needed
    other = first_configured_provider()
    if other is not None and other.id != provider.id:
        from core.llm.catalog import list_models

        for m in list_models():
            if m.provider == other.id:
                set_model(m.id)
                ui.info(f"当前模型缺少 {provider.env_key}，已改用 {m.label}")
                return get_client_for_model(m.id)

    # Prompt for the active provider key (DeepSeek-compatible UX)
    ui.info(f"需要 {provider.label} 的 API Key（环境变量 {provider.env_key}）")
    api_key = ui.api_key_prompt().strip()
    if not api_key:
        ui.error(f"{provider.env_key} 不能为空")
        sys.exit(1)
    _save_env_key(provider.env_key, api_key)
    ui.info(f"API Key 已保存到 {ENV_PATH}")
    try:
        return get_client_for_model(get_model())
    except MissingApiKeyError as exc:
        ui.error(str(exc))
        sys.exit(1)


def bootstrap(args: argparse.Namespace):
    """共享启动逻辑，返回运行所需对象。"""
    _load_local_env()
    client = _ensure_llm_ready()

    hooks = HookRegistry()
    try:
        loaded_hooks = hooks.load_from_config()
    except (FileNotFoundError, ImportError, json.JSONDecodeError) as e:
        ui.error(f"Hook 加载失败: {e}")
        sys.exit(1)

    store = SessionStore()
    purged = store.maybe_auto_purge()
    if purged:
        ui.info(f"已清理 {len(purged)} 个超过 {RETENTION_DAYS} 天未更新的 session")

    ctx = AgentContext(PROJECT_ROOT)
    current: SessionInfo | None = None
    messages: list[dict] = []

    current, messages = _resolve_startup(store, ctx, args)
    if messages:
        ctx.sync_system_message(messages)

    hooks.emit("session.start", {
        "session_id": current.id if current else None,
        "session_title": current.title if current else "新对话",
        "skills": [s.name for s in ctx.skills.all()],
        "tools": [t[0] for t in ctx.tools.all()],
        "messages": messages,
    })

    return client, hooks, store, ctx, current, messages, loaded_hooks


def _resolve_startup(store, ctx, args) -> tuple[SessionInfo | None, list[dict]]:
    if args.list:
        ui.show_sessions(store.list_sessions())
        sys.exit(0)
    if args.resume:
        info = store.find(args.resume)
        if info is None:
            ui.error(f"未找到 session: {args.resume}")
            sys.exit(1)
        return _load_session(store, ctx, info)

    # OpenCode --continue 风格：默认回到上次 / 最近 session
    last_id = get_last_session_id()
    info = store.get(last_id) if last_id else None
    if info is None:
        info = store.latest()
    if info is not None:
        set_last_session_id(info.id)
        return _load_session(store, ctx, info)

    return None, ctx.fresh_messages()


def _load_session(store, ctx, info: SessionInfo) -> tuple[SessionInfo, list[dict]]:
    messages = store.load_messages(info.id)
    if not messages:
        messages = ctx.fresh_messages()
    else:
        ctx.sync_system_message(messages)
    set_last_session_id(info.id)
    return info, messages
