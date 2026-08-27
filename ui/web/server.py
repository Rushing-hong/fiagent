"""Localhost-only Web UI for Atrading (stdlib HTTP + SSE)."""

from __future__ import annotations

import argparse
import json
import os
import queue
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from brand import APP_NAME, TAGLINE, TAGLINE_ZH
from core.commands import (
    HANDLED_REEXEC,
    HANDLED_QUIT,
    HANDLED_RESTART,
    SESSION_COMMANDS,
    handle_session_command,
    reexec_self,
)
from core.agents.dispatch import dispatch_turn
from core.agents.router import AgentMode
from core.turn_control import TurnAborted, turn_control
from ui import ui
from ui.prefs import (
    ALWAYS_ON_TOOLS,
    effort_label,
    get_model,
    get_reasoning_effort,
    is_mcp_tool_enabled,
    is_skill_enabled,
    is_tool_enabled,
    model_label,
    set_last_session_id,
    toggle_mcp_tool,
    toggle_skill,
    toggle_tool,
)
from ui.web.bridge import WebBridge
from ui.web.research_api import evals_payload, lessons_payload, list_runs_payload, run_detail_payload
from ui.web.ticker import fetch_ticker

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("FIAGENT_WEB_PORT", "8787"))

_COLLABORATION_MODES = {
    AgentMode.RESEARCH.value: AgentMode.RESEARCH,
    AgentMode.COMMITTEE.value: AgentMode.COMMITTEE,
    AgentMode.TRADE_REVIEW.value: AgentMode.TRADE_REVIEW,
}


def _collaboration_mode(value: str | None) -> AgentMode | None:
    """Resolve a one-shot Web collaboration selection."""
    return _COLLABORATION_MODES.get((value or "").strip().lower())


def _is_authorized_post(headers: Any, *, expected_origin: str, csrf_token: str) -> bool:
    """Allow state-changing requests only from this UI with its per-run token."""
    origin = headers.get("Origin")
    if origin and origin != expected_origin:
        return False
    supplied = headers.get("X-Atrading-CSRF", "")
    return bool(supplied) and secrets.compare_digest(str(supplied), csrf_token)


def _current_model_status() -> dict[str, Any]:
    """Return UI-safe readiness for the selected model without exposing secrets."""
    from core.llm.catalog import get_model_spec
    from core.llm.client import provider_status

    model = get_model()
    try:
        spec = get_model_spec(model)
        status = provider_status(spec.provider)
    except (KeyError, ValueError) as exc:
        return {
            "ready": False,
            "provider": "",
            "note": f"模型配置异常: {exc}",
        }
    return {
        "ready": bool(status.get("ready")),
        "provider": spec.provider,
        "note": str(status.get("note") or ""),
    }


class WebRuntime:
    def __init__(
        self,
        *,
        client: Any,
        hooks: Any,
        store: Any,
        ctx: Any,
        current: Any,
        messages: list,
        loaded_hooks: list,
        args: argparse.Namespace,
    ) -> None:
        self.client = client
        self.hooks = hooks
        self.store = store
        self.ctx = ctx
        self.current = current
        self.messages = messages
        self.loaded_hooks = loaded_hooks
        self.args = args
        self._lock = threading.Lock()
        self.csrf_token = secrets.token_urlsafe(32)
        self._busy = False
        self._subs: list[queue.Queue] = []
        self._subs_lock = threading.Lock()
        self.bridge = WebBridge(self.emit)
        self._reexec_resume: str | None | bool = False
        self.shutdown_callback: Any | None = None

    def emit(self, event: dict[str, Any]) -> None:
        event = {**event, "ts": time.time()}
        with self._subs_lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                # 丢最旧的，确保最新思考/工具事件能进
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=2048)
        with self._subs_lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._subs_lock:
            if q in self._subs:
                self._subs.remove(q)

    def bootstrap_payload(self) -> dict[str, Any]:
        cur = self.current
        tools = self.ctx.tools.all()
        skills = self.ctx.skills.all()
        model_status = _current_model_status()
        latest_trade_date = None
        try:
            from market.trade_calendar import latest_trading_day

            latest_trade_date = latest_trading_day()
        except Exception:
            latest_trade_date = None
        return {
            "app": APP_NAME,
            "tagline": TAGLINE,
            "tagline_zh": TAGLINE_ZH,
            "ui_mode": "web",
            "session_id": cur.id if cur else None,
            "session_title": cur.title if cur else "新对话",
            "skills": [s.name for s in skills],
            "skill_count": len(skills),
            "tool_count": len(tools),
            "hooks": list(self.loaded_hooks),
            "busy": self._busy,
            "commands": SESSION_COMMANDS,
            "messages": _public_messages(self.messages),
            "sessions": self._sessions_rows(limit=40),
            "model": get_model(),
            "model_label": model_label(),
            "model_status": model_status,
            "effort": get_reasoning_effort(),
            "effort_label": effort_label(),
            "now": self.ctx.format_now(),
            "has_history": bool(_public_messages(self.messages)),
            "latest_trade_date": latest_trade_date,
            "csrf_token": self.csrf_token,
        }

    def _sessions_rows(self, *, limit: int = 40, query: str = "") -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        q = (query or "").strip()
        try:
            if q:
                sessions = self.store.search_sessions(q, limit=limit)
            else:
                try:
                    sessions = self.store.list_sessions(limit=limit)
                except TypeError:
                    sessions = self.store.list_sessions()[:limit]
            for s in sessions:
                item = {
                    "id": s.id,
                    "title": s.title or s.id,
                    "messages": s.message_count,
                    "updated": str(getattr(s, "updated_at", "")),
                }
                snippet = getattr(s, "match_snippet", None)
                if snippet:
                    item["snippet"] = snippet
                rows.append(item)
        except Exception:
            return []
        return rows

    def sessions_payload(self, *, query: str = "") -> dict[str, Any]:
        cur = self.current
        return {
            "ok": True,
            "query": (query or "").strip(),
            "items": self._sessions_rows(limit=40, query=query),
            "current_id": cur.id if cur else None,
        }

    def _emit_sessions_list(self) -> None:
        cur = self.current
        self.emit(
            {
                "type": "sessions_list",
                "items": self._sessions_rows(limit=40),
                "current_id": cur.id if cur else None,
            }
        )

    def handle_chat(self, text: str, collaboration: str = "") -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "empty"}
        mode = _collaboration_mode(collaboration)
        if collaboration and mode is None:
            return {"ok": False, "error": "invalid collaboration"}
        if mode is not None and text.startswith("/"):
            return {"ok": False, "error": "commands cannot use collaboration"}
        with self._lock:
            if self._busy:
                return {"ok": False, "error": "busy"}
            self._busy = True

        def worker() -> None:
            try:
                self._run_one(text, collaboration=mode)
            finally:
                with self._lock:
                    self._busy = False
                self.bridge.set_idle()

        threading.Thread(target=worker, name="web-turn", daemon=True).start()
        return {"ok": True}

    def abort(self) -> dict[str, Any]:
        turn_control.request_abort()
        self.emit({"type": "status", "text": "正在中止…"})
        return {"ok": True}

    # Web 侧栏直调：只开放只读、表格式结果工具
    _DIRECT_TOOLS = frozenset({
        "screen_market",
        "get_limit_board",
        "screen_fundamental",
        "run_backtest",
    })

    def handle_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
        if name not in self._DIRECT_TOOLS:
            return {"ok": False, "error": f"不支持直调: {name}"}
        try:
            raw = self.ctx.execute_tool(name, json.dumps(args, ensure_ascii=False))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if isinstance(raw, str) and not raw.lstrip().startswith(("{", "[")):
            return {"ok": False, "error": raw}
        try:
            result = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            result = {"ok": False, "error": "工具返回非 JSON", "raw": str(raw)[:2000]}
        return {"ok": True, "tool": name, "result": result}

    def _emit_prefs(self) -> None:
        cur = self.current
        self.emit(
            {
                "type": "prefs",
                "model": get_model(),
                "model_label": model_label(),
                "model_status": _current_model_status(),
                "effort": get_reasoning_effort(),
                "effort_label": effort_label(),
                "session_id": cur.id if cur else None,
                "session_title": cur.title if cur else "新对话",
                "tool_count": len(self.ctx.tools.all()),
                "skill_count": len(self.ctx.skills.all()),
            }
        )
        self._emit_sessions_list()

    def _open_session_picker(self) -> dict[str, Any]:
        items = [
            {
                "id": "__new__",
                "label": "新建对话",
                "hint": "清空上下文",
                "current": self.current is None,
            }
        ]
        try:
            sessions = self.store.list_sessions(limit=30)
        except TypeError:
            sessions = self.store.list_sessions()[:30]
        for s in sessions:
            ts = str(getattr(s, "updated_at", ""))[:19].replace("T", " ")
            items.append(
                {
                    "id": s.id,
                    "label": s.title or s.id,
                    "hint": f"{s.id} · {s.message_count} 条 · {ts}",
                    "current": bool(self.current and self.current.id == s.id),
                }
            )
        return self._emit_picker(
            kind="session",
            title="Session",
            hint="选择要进入的对话 · Esc 关闭",
            items=items,
        )

    def _open_model_picker(self) -> dict[str, Any]:
        from core.llm.catalog import list_models
        from core.llm.client import provider_status
        from ui.prefs import get_model

        current = get_model()
        items = []
        for m in list_models():
            st = provider_status(m.provider)
            items.append(
                {
                    "id": m.id,
                    "label": m.label,
                    "hint": f"{m.group} · {m.api_model} · {st['note']}",
                    "current": m.id == current,
                    "ready": bool(st["ready"]),
                }
            )
        return self._emit_picker(
            kind="model",
            title="模型",
            hint="绿=Key 可用 · Claude 需 ANTHROPIC_BASE_URL · Esc 关闭",
            items=items,
        )

    def _open_effort_picker(self) -> dict[str, Any]:
        from ui.prefs import (
            AVAILABLE_EFFORTS,
            EFFORT_LABELS,
            current_model_supports_thinking,
            get_reasoning_effort,
            model_label,
        )

        current = get_reasoning_effort()
        supports = current_model_supports_thinking()
        items = [
            {
                "id": effort,
                "label": EFFORT_LABELS.get(effort, effort),
                "hint": (
                    "当前模型不支持思考强度"
                    if not supports
                    else ("关闭后走非 thinking 模式" if effort == "off" else "thinking 开启")
                ),
                "current": effort == current,
            }
            for effort in AVAILABLE_EFFORTS
        ]
        return self._emit_picker(
            kind="effort",
            title="思考强度",
            hint=(
                f"{model_label()} 不支持 · 仍可改偏好"
                if not supports
                else "选择强度 · Esc 关闭"
            ),
            items=items,
        )

    def _emit_picker(
        self,
        *,
        kind: str,
        title: str,
        hint: str,
        items: list[dict],
        parent: str | None = None,
        back: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "picker",
            "kind": kind,
            "title": title,
            "hint": hint,
            "items": items,
        }
        if parent is not None:
            payload["parent"] = parent
        if back is not None:
            payload["back"] = back
        self.emit(payload)
        return payload

    def _open_tools_picker(self) -> None:
        from ui.capability_groups import category_counts, group_tools

        self.ctx.refresh()
        toggleable = [
            (n, s) for n, s in self.ctx.tools.all() if n not in ALWAYS_ON_TOOLS
        ]
        if not toggleable:
            ui.warn("没有可开关的工具")
            return
        items = []
        for cat_id, hint, members in group_tools([n for n, _ in toggleable]):
            on_n, total = category_counts(members, is_enabled=is_tool_enabled)
            tone: bool | None
            if on_n == total:
                tone = True
            elif on_n == 0:
                tone = False
            else:
                tone = None
            items.append(
                {
                    "id": f"cat:{cat_id}",
                    "label": cat_id,
                    "hint": f"{on_n}/{total} · {hint}",
                    "on": tone,
                }
            )
        return self._emit_picker(
            kind="tools-cat",
            title="管理工具 · 分类",
            hint="绿=全开 · 红=全关 · 点进分类 · Esc 关闭",
            items=items,
        )

    def _open_tools_category(self, cat_pick_id: str) -> None:
        from ui.capability_groups import group_tools

        if not cat_pick_id.startswith("cat:"):
            return
        cat_id = cat_pick_id.removeprefix("cat:")
        self.ctx.refresh()
        toggleable = {
            n: s for n, s in self.ctx.tools.all() if n not in ALWAYS_ON_TOOLS
        }
        groups = {c: (h, m) for c, h, m in group_tools(list(toggleable))}
        if cat_id not in groups:
            ui.warn(f"未知分类: {cat_id}")
            return
        _hint, members = groups[cat_id]
        items = [
            {
                "id": name,
                "label": name,
                "hint": toggleable.get(name) or "",
                "on": is_tool_enabled(name),
            }
            for name in members
        ]
        return self._emit_picker(
            kind="tools",
            title=f"工具 · {cat_id}",
            hint="点击切换 · 绿开红关 · Esc 返回分类",
            items=items,
            parent=cat_id,
            back="tools-cat",
        )

    def _open_skills_picker(self) -> None:
        from ui.capability_groups import category_counts, group_skills

        self.ctx.refresh()
        skills = list(self.ctx.skills.all())
        if not skills:
            ui.warn("没有可开关的 skill")
            return
        by_name = {s.name: s for s in skills}
        items = []
        for cat_id, hint, members in group_skills([s.name for s in skills]):
            on_n, total = category_counts(members, is_enabled=is_skill_enabled)
            if on_n == total:
                tone: bool | None = True
            elif on_n == 0:
                tone = False
            else:
                tone = None
            items.append(
                {
                    "id": f"cat:{cat_id}",
                    "label": cat_id,
                    "hint": f"{on_n}/{total} · {hint}",
                    "on": tone,
                }
            )
        return self._emit_picker(
            kind="skills-cat",
            title="管理 Skills · 分类",
            hint="绿=全开 · 红=全关 · 点进分类 · Esc 关闭",
            items=items,
        )

    def _open_skills_category(self, cat_pick_id: str) -> None:
        from ui.capability_groups import group_skills

        if not cat_pick_id.startswith("cat:"):
            return
        cat_id = cat_pick_id.removeprefix("cat:")
        self.ctx.refresh()
        skills = list(self.ctx.skills.all())
        by_name = {s.name: s for s in skills}
        groups = {c: (h, m) for c, h, m in group_skills(list(by_name))}
        if cat_id not in groups:
            ui.warn(f"未知分类: {cat_id}")
            return
        _hint, members = groups[cat_id]
        items = []
        for name in members:
            skill = by_name[name]
            items.append(
                {
                    "id": name,
                    "label": name,
                    "hint": (
                        f"[{'内置' if skill.bundled else '用户'}] "
                        + (skill.description or "")[:40]
                    ),
                    "on": is_skill_enabled(name),
                }
            )
        return self._emit_picker(
            kind="skills",
            title=f"Skills · {cat_id}",
            hint="点击切换 · 绿开红关 · Esc 返回分类",
            items=items,
            parent=cat_id,
            back="skills-cat",
        )

    def _open_mcp_picker(self) -> None:
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
                tone: bool | None = True if on_n == total else False if on_n == 0 else None
            elif server.enabled:
                hint = "无工具"
                tone = True
            else:
                hint = "server 已关"
                tone = False
            if server.note:
                hint = f"{hint} · {server.note[:36]}"
            items.append(
                {
                    "id": f"srv:{server.id}",
                    "label": server.id,
                    "hint": hint,
                    "on": tone,
                }
            )
        return self._emit_picker(
            kind="mcp-srv",
            title="管理 MCP · Server",
            hint="绿开红关 · 点进 server · Esc 关闭",
            items=items,
        )

    def _open_mcp_server(self, server_id: str) -> None:
        self.ctx.refresh()
        server = self.ctx.mcp.get_server(server_id)
        if server is None:
            ui.warn(f"未知 MCP server: {server_id}")
            return
        items: list[dict[str, Any]] = [
            {
                "id": "__toggle_server__",
                "label": f"{'关闭' if server.enabled else '启用'} server `{server_id}`",
                "hint": "写入 mcps/mcp.json",
                "on": server.enabled,
            }
        ]
        for tool in server.tools:
            effective = server.enabled and is_mcp_tool_enabled(tool.name)
            items.append(
                {
                    "id": tool.name,
                    "label": tool.name,
                    "hint": (tool.description or "")[:48],
                    "on": effective,
                }
            )
        if len(items) == 1:
            items.append(
                {
                    "id": "__empty__",
                    "label": "（该 server 未声明 tools）",
                    "hint": "在 mcp.json 的 tools 数组补充",
                    "on": None,
                }
            )
        return self._emit_picker(
            kind="mcp",
            title=f"MCP · {server_id}",
            hint="点击切换 · 绿开红关 · Esc 返回 server 列表",
            items=items,
            parent=server_id,
            back="mcp-srv",
        )

    def handle_picker(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Nested picker navigation / toggles (TUI open_*_picker parity)."""
        op = str(payload.get("op") or "pick")
        kind = str(payload.get("kind") or "")
        item_id = str(payload.get("id") or "")
        parent = str(payload.get("parent") or "")

        with self._lock:
            if self._busy:
                return {"ok": False, "error": "busy"}

        try:
            if op == "open":
                picker: dict[str, Any] | None = None
                if kind in ("tools", "tools-cat"):
                    picker = self._open_tools_picker()
                elif kind in ("skills", "skills-cat"):
                    picker = self._open_skills_picker()
                elif kind in ("mcp", "mcp-srv"):
                    picker = self._open_mcp_picker()
                elif kind == "session":
                    picker = self._open_session_picker()
                elif kind == "model":
                    picker = self._open_model_picker()
                elif kind == "effort":
                    picker = self._open_effort_picker()
                else:
                    return {"ok": False, "error": f"unknown kind: {kind}"}
                if picker:
                    return {"ok": True, "picker": picker}
                return {"ok": True}

            if op == "back":
                back = str(payload.get("back") or kind)
                picker = None
                if back == "tools-cat":
                    picker = self._open_tools_picker()
                elif back == "skills-cat":
                    picker = self._open_skills_picker()
                elif back == "mcp-srv":
                    picker = self._open_mcp_picker()
                else:
                    return {"ok": True, "closed": True}
                if picker:
                    return {"ok": True, "picker": picker}
                return {"ok": True}

            if op != "pick":
                return {"ok": False, "error": f"unknown op: {op}"}

            if kind == "tools-cat":
                picker = self._open_tools_category(item_id)
                return {"ok": True, "picker": picker} if picker else {"ok": True}
            if kind == "tools":
                if item_id in ALWAYS_ON_TOOLS:
                    ui.warn(f"`{item_id}` 为常开工具，不可关闭")
                    return {"ok": True}
                if self.ctx.tools.get(item_id) is None:
                    ui.warn(f"未找到工具: {item_id}")
                    return {"ok": True}
                enabled = toggle_tool(item_id)
                self.ctx.sync_system_message(self.messages)
                ui.success(f"工具 `{item_id}` 已{'启用' if enabled else '禁用'}")
                if parent:
                    picker = self._open_tools_category(f"cat:{parent}")
                else:
                    picker = self._open_tools_picker()
                return {"ok": True, "picker": picker} if picker else {"ok": True}

            if kind == "skills-cat":
                picker = self._open_skills_category(item_id)
                return {"ok": True, "picker": picker} if picker else {"ok": True}
            if kind == "skills":
                if self.ctx.skills.get(item_id) is None:
                    ui.warn(f"未找到 skill: {item_id}")
                    return {"ok": True}
                enabled = toggle_skill(item_id)
                self.ctx.sync_system_message(self.messages)
                ui.success(f"Skill `{item_id}` 已{'启用' if enabled else '禁用'}")
                if parent:
                    picker = self._open_skills_category(f"cat:{parent}")
                else:
                    picker = self._open_skills_picker()
                return {"ok": True, "picker": picker} if picker else {"ok": True}

            if kind == "mcp-srv":
                if item_id.startswith("srv:"):
                    picker = self._open_mcp_server(item_id.removeprefix("srv:"))
                    return {"ok": True, "picker": picker} if picker else {"ok": True}
                return {"ok": True}
            if kind == "mcp":
                if item_id in ("__empty__", "__help__"):
                    if parent:
                        picker = self._open_mcp_server(parent)
                        return {"ok": True, "picker": picker} if picker else {"ok": True}
                    return {"ok": True}
                if item_id == "__toggle_server__":
                    try:
                        enabled = self.ctx.mcp.toggle_server(parent)
                    except KeyError:
                        ui.warn(f"未知 MCP server: {parent}")
                        return {"ok": True}
                    self.ctx.sync_system_message(self.messages)
                    ui.success(
                        f"MCP server `{parent}` 已{'启用' if enabled else '关闭'}"
                    )
                    picker = self._open_mcp_server(parent)
                    return {"ok": True, "picker": picker} if picker else {"ok": True}
                enabled = toggle_mcp_tool(item_id)
                self.ctx.refresh()
                self.ctx.sync_system_message(self.messages)
                ui.success(f"MCP 工具 `{item_id}` 已{'启用' if enabled else '禁用'}")
                if parent:
                    picker = self._open_mcp_server(parent)
                else:
                    picker = self._open_mcp_picker()
                return {"ok": True, "picker": picker} if picker else {"ok": True}

            return {"ok": False, "error": f"unknown kind: {kind}"}
        except Exception as exc:
            ui.error(str(exc))
            return {"ok": False, "error": str(exc)}

    def _run_one(
        self,
        text: str,
        *,
        collaboration: AgentMode | None = None,
    ) -> None:
        self.bridge.set_busy("处理中…")

        if text.startswith("/"):
            from core.commands import (
                COMMAND_ALIASES,
                HANDLED_QUIT,
                HANDLED_REEXEC,
                HANDLED_RESTART,
            )

            raw_name = text.split(maxsplit=1)[0].lower()
            if raw_name in COMMAND_ALIASES:
                rest = text[len(raw_name) :].lstrip()
                text = COMMAND_ALIASES[raw_name] + ((" " + rest) if rest else "")

            low = text.strip().lower()
            is_collaboration_command = (
                low.startswith("/research") or low.startswith("/committee")
                or low.startswith("/review")
            )
            if not is_collaboration_command:
                if low in ("/sessions", "/session"):
                    ui.show_user_message(text)
                    self._open_session_picker()
                    self._emit_prefs()
                    return
                if low == "/model":
                    ui.show_user_message(text)
                    self._open_model_picker()
                    self._emit_prefs()
                    return
                if low == "/effort":
                    ui.show_user_message(text)
                    self._open_effort_picker()
                    self._emit_prefs()
                    return
                if low in ("/tools", "/tool"):
                    ui.show_user_message(text)
                    self._open_tools_picker()
                    self._emit_prefs()
                    return
                if low in ("/skills", "/skill"):
                    ui.show_user_message(text)
                    self._open_skills_picker()
                    self._emit_prefs()
                    return
                if low in ("/mcp", "/mcps"):
                    ui.show_user_message(text)
                    self._open_mcp_picker()
                    self._emit_prefs()
                    return

                ui.show_user_message(text)
                self.current, new_messages, handled = handle_session_command(
                    text, self.store, self.ctx, self.current
                )
                if not handled:
                    ui.warn(f"未知命令: {text}，输入 /help 查看帮助")
                elif handled == HANDLED_QUIT:
                    ui.goodbye()
                    self.emit({"type": "shutdown"})
                    if self.shutdown_callback is not None:
                        threading.Thread(target=self.shutdown_callback, daemon=True).start()
                elif handled in (HANDLED_REEXEC, HANDLED_RESTART):
                    self._reexec_resume = self.current.id if self.current else None
                    self.emit({"type": "reexec"})
                    threading.Thread(target=self._do_reexec, daemon=True).start()
                elif new_messages is not None:
                    self.messages = new_messages
                    self.emit(
                        {
                            "type": "startup",
                            "session_id": self.current.id if self.current else None,
                            "session_title": self.current.title if self.current else "新对话",
                            "skills": [s.name for s in self.ctx.skills.all()],
                            "hook_count": len(self.loaded_hooks),
                            "messages": _public_messages(self.messages),
                            "clear": True,
                        }
                    )
                self._emit_prefs()
                return

        turn_ctx = self.hooks.emit(
            "turn.start",
            {"input": text, "session_id": self.current.id if self.current else None},
        )
        if turn_ctx.cancel:
            ui.hook_blocked()
            return
        text = turn_ctx.get("input", text)

        turn_start = len(self.messages)
        ui.show_user_message(text)
        self.messages.append({"role": "user", "content": text})
        try:
            dispatch_turn(
                self.client,
                self.messages,
                self.ctx,
                self.hooks,
                text,
                mode_override=collaboration,
            )
            if self.current is None:
                self.current = self.store.create()
                self.store.auto_title(self.current.id, text)
                updated = self.store.get(self.current.id)
                if updated:
                    self.current = updated
            self.store.save_messages(self.current.id, self.messages)
            set_last_session_id(self.current.id)
            self.hooks.emit(
                "turn.end",
                {
                    "input": text,
                    "messages": self.messages,
                    "session_id": self.current.id,
                },
            )
        except TurnAborted:
            del self.messages[turn_start:]
            ui.warn("本轮已中止，对话未保存本轮内容")
            self.emit({"type": "aborted"})
        except Exception as exc:
            del self.messages[turn_start:]
            from core.stream import LlmStreamError
            if not isinstance(exc, LlmStreamError):
                ui.error(str(exc))
            self.emit({"type": "error", "text": str(exc)})

    def _do_reexec(self) -> None:
        time.sleep(0.35)
        resume = self._reexec_resume if isinstance(self._reexec_resume, str) else None
        reexec_self(resume_id=resume)


def _public_messages(messages: list) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        item: dict[str, Any] = {"role": role, "content": content}
        private_meta = m.get("_fiagent")
        if isinstance(private_meta, dict):
            collaboration = private_meta.get("collaboration")
            if isinstance(collaboration, dict) and collaboration.get("run_id"):
                item["collaboration"] = collaboration
        out.append(item)
    return out[-80:]


def run_web(
    *,
    client: Any,
    hooks: Any,
    store: Any,
    ctx: Any,
    current: Any,
    messages: list,
    loaded_hooks: list,
    session_commands: dict,
    handle_command: Any,
    args: argparse.Namespace,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
) -> None:
    del session_commands, handle_command  # parity with run_tui signature
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("Web UI 仅允许绑定本机 (127.0.0.1)")

    rt = WebRuntime(
        client=client,
        hooks=hooks,
        store=store,
        ctx=ctx,
        current=current,
        messages=messages,
        loaded_hooks=loaded_hooks,
        args=args,
    )
    turn_control.set_tui_mode(True)
    ui.bind_tui(rt.bridge)
    from ui.web.collaboration_progress import set_progress_emitter
    set_progress_emitter(rt.bridge.mount_collaboration)
    rt.bridge.start_ticks()
    rt.bridge.set_idle()
    ui.show_startup(
        session_id=current.id if current else None,
        session_title=current.title if current else "新对话",
        skills=[s.name for s in ctx.skills.all()],
        hooks=loaded_hooks,
        current_time=ctx.format_now(),
        ui_mode="web",
    )
    ui.hydrate_messages(messages)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        _static_cache: dict[str, tuple[float, bytes]] = {}

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _send(
            self,
            code: int,
            body: bytes,
            content_type: str,
            *,
            cache_control: str = "no-store",
        ) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache_control)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(code, data, "application/json; charset=utf-8")

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                html = (STATIC_DIR / "index.html").read_bytes()
                self._send(200, html, "text/html; charset=utf-8")
                return
            if path == "/api/bootstrap":
                self._json(200, rt.bootstrap_payload())
                return
            if path == "/api/sessions":
                qs = parse_qs(urlparse(self.path).query)
                q = (qs.get("q") or [""])[0]
                self._json(200, rt.sessions_payload(query=q))
                return
            if path == "/api/ticker":
                self._json(200, fetch_ticker())
                return
            if path == "/api/research/runs":
                qs = parse_qs(urlparse(self.path).query)
                limit = int((qs.get("limit") or ["30"])[0])
                self._json(200, list_runs_payload(limit=limit))
                return
            if path.startswith("/api/research/runs/"):
                run_id = path.split("/api/research/runs/", 1)[1].strip("/")
                if run_id:
                    self._json(200, run_detail_payload(run_id))
                    return
            if path == "/api/evals":
                self._json(200, evals_payload())
                return
            if path == "/api/lessons":
                qs = parse_qs(urlparse(self.path).query)
                sym = (qs.get("symbol") or [""])[0] or None
                limit = int((qs.get("limit") or ["20"])[0])
                self._json(200, lessons_payload(symbol=sym, limit=limit))
                return
            if path == "/api/events":
                self._sse()
                return
            if path.startswith("/static/"):
                rel = path[len("/static/") :]
                target = (STATIC_DIR / rel).resolve()
                if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
                    self._json(404, {"ok": False, "error": "not found"})
                    return
                ctype = {
                    ".css": "text/css; charset=utf-8",
                    ".js": "application/javascript; charset=utf-8",
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".webp": "image/webp",
                    ".svg": "image/svg+xml",
                    ".html": "text/html; charset=utf-8",
                }.get(target.suffix.lower(), "application/octet-stream")
                self._send_static(target, ctype)
                return
            self._json(404, {"ok": False, "error": "not found"})

        def _read_static(self, target: Path) -> tuple[float, bytes]:
            """Read a static file with an mtime-keyed in-memory cache."""
            mtime = target.stat().st_mtime
            key = str(target)
            hit = self._static_cache.get(key)
            if hit and hit[0] == mtime:
                return hit
            entry = (mtime, target.read_bytes())
            self._static_cache[key] = entry
            return entry

        def _send_static(self, target: Path, ctype: str) -> None:
            mtime, body = self._read_static(target)
            last_modified = self.date_time_string(mtime)
            ims = self.headers.get("If-Modified-Since")
            if ims and ims == last_modified:
                self.send_response(304)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Last-Modified", last_modified)
            # 静态资源带 ?v= 指纹的可让浏览器缓存更久；本地开发用 no-cache + 304 最稳
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if not _is_authorized_post(
                self.headers,
                expected_origin=f"http://{host}:{port}",
                csrf_token=rt.csrf_token,
            ):
                self._json(403, {"ok": False, "error": "forbidden"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._json(400, {"ok": False, "error": "bad json"})
                return
            if path == "/api/chat":
                self._json(200, rt.handle_chat(
                    str(payload.get("text") or ""),
                    str(payload.get("collaboration") or ""),
                ))
                return
            if path == "/api/picker":
                self._json(200, rt.handle_picker(payload if isinstance(payload, dict) else {}))
                return
            if path == "/api/tool":
                self._json(200, rt.handle_tool(payload if isinstance(payload, dict) else {}))
                return
            if path == "/api/abort":
                self._json(200, rt.abort())
                return
            self._json(404, {"ok": False, "error": "not found"})

        def _sse(self) -> None:
            q = rt.subscribe()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                # hello
                hello = json.dumps({"type": "hello", "app": APP_NAME}, ensure_ascii=False)
                self.wfile.write(f"data: {hello}\n\n".encode("utf-8"))
                self.wfile.flush()
                while True:
                    try:
                        evt = q.get(timeout=15.0)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    try:
                        line = json.dumps(evt, ensure_ascii=False, default=str)
                    except Exception:
                        line = json.dumps(
                            {"type": "error", "text": "sse serialize failed"},
                            ensure_ascii=False,
                        )
                    self.wfile.write(f"data: {line}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            finally:
                rt.unsubscribe(q)

    httpd = ThreadingHTTPServer((host, port), Handler)
    rt.shutdown_callback = httpd.shutdown
    url = f"http://{host}:{port}/"
    ui.info(f"{APP_NAME} Web UI → {url}")
    ui.info("终端保持运行；浏览器对话。Ctrl+C 退出。偏好: /tui /plain /web")
    if open_browser and os.environ.get("FIAGENT_WEB_NO_BROWSER", "").strip() not in (
        "1",
        "true",
        "yes",
    ):
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        ui.info("Web UI 已退出")
    finally:
        rt.bridge.stop_ticks()
        ui.unbind_tui()
        httpd.server_close()
        store.close()
