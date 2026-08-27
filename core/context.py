from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from core.tool_routing import select_tool_names
from mcps.registry import MCPRegistry
from skills.registry import Skill, SkillRegistry
from tools.base import ToolRegistry

if TYPE_CHECKING:
    from core.agents.profile import AgentProfile

from ui.prefs import (
    get_disabled_skills,
    get_disabled_tools,
    is_mcp_tool_enabled,
    is_tool_enabled,
)


class AgentContext:
    """运行时自动组装 system prompt 与 tools，各模块独立维护。"""

    def __init__(self, root: Path, profile: AgentProfile | None = None) -> None:
        self.root = root
        self.profile = profile
        self.skills = SkillRegistry(root / "skills")
        self.tools = ToolRegistry(root / "tools")
        self.mcp = MCPRegistry(root / "mcps")
        self._disabled_tools = frozenset(get_disabled_tools())
        self._disabled_skills = frozenset(get_disabled_skills())
        self._base_prompt_path = root / "prompts" / "base.md"
        self._base_prompt_cache: tuple[tuple[int, int] | None, str] | None = None
        self._capabilities_cache: tuple[tuple[Any, ...], str] | None = None
        self._system_prompt_cache: tuple[tuple[Any, ...], str] | None = None
        self._runtime_prompt_cache: tuple[tuple[Any, ...], str] | None = None
        self._tools_schema_cache: tuple[tuple[Any, ...], list[dict[str, Any]]] | None = None

    def _tool_allowed_by_profile(self, name: str) -> bool:
        if self.profile is None:
            return True
        return self.profile.tool_allowed(name)

    def _skill_allowed_by_profile(self, name: str) -> bool:
        if self.profile is None:
            return True
        return self.profile.skill_allowed(name)

    def refresh(self) -> None:
        self.skills.refresh()
        self.tools.refresh()
        self.mcp.refresh()
        # Preferences and prompt files are snapshotted once per turn. All UI
        # toggle paths call refresh(), so repeated model rounds stay in-memory.
        self._disabled_tools = frozenset(get_disabled_tools())
        self._disabled_skills = frozenset(get_disabled_skills())
        self._base_prompt_cache = None
        self._tools_schema_cache = None
        self._capabilities_cache = None
        self._system_prompt_cache = None
        self._runtime_prompt_cache = None

    def enabled_tools(self) -> list[tuple[str, str]]:
        disabled = get_disabled_tools()
        return [
            (n, s) for n, s in self.tools.all()
            if n not in disabled and self._tool_allowed_by_profile(n)
        ]

    def enabled_skills(self) -> list[Skill]:
        disabled = get_disabled_skills()
        return [
            s for s in self.skills.all()
            if s.name not in disabled and self._skill_allowed_by_profile(s.name)
        ]

    def load_base_prompt(self) -> str:
        try:
            stat = self._base_prompt_path.stat()
            signature: tuple[int, int] | None = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            signature = None
        if self._base_prompt_cache and self._base_prompt_cache[0] == signature:
            return self._base_prompt_cache[1]
        if signature is not None:
            try:
                prompt = self._base_prompt_path.read_text(encoding="utf-8").strip()
            except OSError:
                prompt = "你是一个有用的 AI Agent。"
                signature = None  # transient read failures should be retried
        else:
            prompt = "你是一个有用的 AI Agent。"
        self._base_prompt_cache = (signature, prompt)
        return prompt

    def _now(self) -> datetime:
        tz_name = os.getenv("FIAGENT_TZ", "Asia/Shanghai")
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("Asia/Shanghai")
        return datetime.now(tz)

    def format_now(self) -> str:
        now = self._now()
        weekdays = "一二三四五六日"
        tz_name = os.getenv("FIAGENT_TZ", "Asia/Shanghai")
        return (
            f"{now.strftime('%Y-%m-%d %H:%M:%S')} 星期{weekdays[now.weekday()]} "
            f"({tz_name})"
        )

    def build_time_context(self) -> str:
        """Day-level env block (OpenCode-style) — stable within a calendar day for prefix cache."""
        now = self._now()
        weekdays = "一二三四五六日"
        tz_name = os.getenv("FIAGENT_TZ", "Asia/Shanghai")
        offset = now.strftime("%z")
        offset_fmt = f"UTC{offset[:3]}:{offset[3:]}" if offset else ""
        return "\n".join([
            "## 环境信息（按日刷新；精确到秒的时间见本轮请求末尾提示或 `get_current_time`）",
            f"- 今天：{now.strftime('%Y-%m-%d')} 星期{weekdays[now.weekday()]}",
            f"- 时区：{tz_name} {offset_fmt}".rstrip(),
            "- 回答「今天/本周/交易日」以此处日期为准；问「现在几点」须用末尾时钟或调用 `get_current_time`",
        ])

    def build_clock_hint(self) -> str:
        """Trailing API-only hint — appended as the last user message, not in system prefix."""
        return (
            f"【系统实时时钟】{self.format_now()}。"
            "此时间仅在本轮 API 请求末尾提供；回答「现在几点/今天几号」必须用这个值，"
            "不要沿用更早对话或记忆中的日期。"
        )

    def with_clock_for_api(self, messages: list[dict]) -> list[dict]:
        """Append precise clock at the end of the API copy (OpenCode: dynamic hints after prefix)."""
        mode = os.getenv("FIAGENT_API_CLOCK", "trailing").strip().lower()
        if mode in ("off", "0", "false", "no"):
            return list(messages)
        if not messages:
            return [{"role": "user", "content": self.build_clock_hint()}]
        # The request pipeline already owns message dictionaries; only the
        # outer list needs copying before appending the transient clock hint.
        out = list(messages)
        out.append({"role": "user", "content": self.build_clock_hint()})
        return out

    def _capability_snapshot(
        self,
    ) -> tuple[tuple[Any, ...], frozenset[str], frozenset[str]]:
        disabled_tools = self._disabled_tools
        disabled_skills = self._disabled_skills
        key = (
            self.tools.generation,
            self.skills.generation,
            self.mcp.generation,
            disabled_tools,
            disabled_skills,
            getattr(self.profile, "name", None),
        )
        return key, disabled_tools, disabled_skills

    def build_capabilities_index(self) -> str:
        """渐进披露第一层：工具看 tools 参数；Skills 只给短索引。"""
        cache_key, disabled_tools, disabled_skills = self._capability_snapshot()
        if self._capabilities_cache and self._capabilities_cache[0] == cache_key:
            return self._capabilities_cache[1]
        lines = ["## 当前能力索引（自动生成）", ""]

        tools_on = [
            (name, summary)
            for name, summary in self.tools.all()
            if name not in disabled_tools and self._tool_allowed_by_profile(name)
        ]
        tools_all = self.tools.all()
        lines.append("### 工具")
        lines.append(
            f"- 已启用工具 {len(tools_on)}/{len(tools_all)}；每轮会按请求注入相关"
            " function schema，意图不明确时自动回退全部。只调用当前 schema，勿臆造。"
        )
        mcp_n = len(self.mcp.all())
        if mcp_n:
            lines.append(
                f"- MCP 工具 {mcp_n} 个已一并注入（description 带 `[MCP]` 前缀）。"
            )
        lines.append("- 可用 `/tools`、`/mcp` 或 Ctrl+P 开关。")

        skills = [
            skill
            for skill in self.skills.all()
            if skill.name not in disabled_skills
            and self._skill_allowed_by_profile(skill.name)
        ]
        if skills:
            lines.append("")
            lines.append(
                "### Skills（与 tools 同级；仅短索引。选用某 skill 时须先 `load_skill`）"
            )
            lines.append(self.skills.get_descriptions(skills))

        result = "\n".join(lines)
        self._capabilities_cache = (cache_key, result)
        return result

    def build_system_prompt_for_profile(self) -> str:
        """Compatibility alias: every profile now shares the same system prefix."""
        return self.build_system_prompt()

    def build_system_prompt(self) -> str:
        """Return the identical, cache-friendly system prefix for every agent.

        Date/time is deliberately excluded and appended at the request tail by
        ``with_clock_for_api`` so the longest static prefix survives day changes.
        """
        cache_key = (str(self._base_prompt_path),)
        if self._system_prompt_cache and self._system_prompt_cache[0] == cache_key:
            return self._system_prompt_cache[1]
        result = self.load_base_prompt()
        self._system_prompt_cache = (cache_key, result)
        return result

    def build_runtime_prompt(self) -> str:
        """Profile/scoped capabilities injected after the shared system prefix."""
        capability_key, _, _ = self._capability_snapshot()
        profile_signature = (
            hash(self.profile.system_prompt) if self.profile is not None else None
        )
        cache_key = (capability_key, profile_signature)
        if self._runtime_prompt_cache and self._runtime_prompt_cache[0] == cache_key:
            return self._runtime_prompt_cache[1]
        parts: list[str] = []
        if self.profile is not None:
            parts.extend([
                "## 当前 Agent 运行时角色与边界",
                self.profile.system_prompt,
            ])
        index = self.build_capabilities_index()
        if index:
            parts.append(index)
        result = "\n\n".join(parts)
        self._runtime_prompt_cache = (cache_key, result)
        return result

    def with_runtime_context_for_api(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Insert agent-specific constraints after the shared cached prefix.

        This works on the transient API copy, keeping persisted sessions and
        every agent's first system message identical.
        """
        runtime = self.build_runtime_prompt()
        if not runtime:
            return list(messages)
        out = list(messages)
        insert_at = 1 if out and out[0].get("role") == "system" else 0
        runtime_block = "【应用注入的 Agent 运行时约束】\n" + runtime
        for index in range(insert_at, len(out)):
            message = out[index]
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                merged = dict(message)
                merged["content"] = runtime_block + "\n\n## 用户任务\n" + content
                out[index] = merged
                return out
        out.insert(
            insert_at,
            {
                "role": "user",
                "content": runtime_block,
            },
        )
        return out

    def build_openai_tools(
        self,
        messages: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        disabled = self._disabled_tools
        cache_key = (
            self.tools.generation,
            self.mcp.generation,
            disabled,
            getattr(self.profile, "name", None),
        )
        if self._tools_schema_cache is not None and self._tools_schema_cache[0] == cache_key:
            schemas = self._tools_schema_cache[1]
        else:
            schemas = [
                s for s in self.tools.build_schemas(self)
                if s.get("function", {}).get("name") not in disabled
                and self._tool_allowed_by_profile(s.get("function", {}).get("name", ""))
            ]
            if self.profile is None:
                schemas.extend(self.mcp.build_schemas())
            schemas.sort(key=lambda s: s.get("function", {}).get("name", ""))
            self._tools_schema_cache = (cache_key, schemas)
        if messages is None:
            return list(schemas)
        selected = select_tool_names(
            messages,
            (s.get("function", {}).get("name", "") for s in schemas),
        )
        if selected is None:
            return list(schemas)
        return [s for s in schemas if s.get("function", {}).get("name") in selected]

    def execute_tool(self, name: str, arguments: str) -> str:
        if self.profile is not None and not self.profile.tool_allowed(name):
            return f"工具 `{name}` 不在当前 Agent 授权范围内，无法调用。"
        if not is_tool_enabled(name):
            return f"工具 `{name}` 已被用户禁用（Ctrl+P → 管理工具 可重新开启）"
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError as exc:
            return f"工具 `{name}` 参数 JSON 非法: {exc}"
        if not isinstance(args, dict):
            return f"工具 `{name}` 参数必须是 JSON 对象"
        mcp_hit = name.startswith("mcp_") or self.mcp.is_configured(name)
        if mcp_hit:
            if not is_mcp_tool_enabled(name):
                return f"MCP 工具 `{name}` 已被用户禁用（Ctrl+P → 管理 MCP 可重新开启）"
            # server 关闭时 all() 不含该工具
            if not self.mcp.is_enabled(name):
                return f"MCP 工具 `{name}` 所属 server 未启用或未加载"
            return self.mcp.execute(name, args)
        return self.tools.execute(name, args, self)

    def fresh_messages(self) -> list[dict]:
        return [{"role": "system", "content": self.build_system_prompt()}]

    def sync_system_message(self, messages: list[dict]) -> list[dict]:
        prompt = self.build_system_prompt()
        if messages and messages[0].get("role") == "system":
            if messages[0].get("content") == prompt:
                return messages
            messages[0] = {"role": "system", "content": prompt}
        else:
            messages.insert(0, {"role": "system", "content": prompt})
        return messages

    def is_readonly_tool(self, name: str) -> bool:
        tool = self.tools.get(name)
        if tool is None:
            return False
        return tool.is_readonly

    def is_repeatable_tool(self, name: str) -> bool:
        tool = self.tools.get(name)
        if tool is None:
            return True
        return tool.repeatable
