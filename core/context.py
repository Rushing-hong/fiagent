import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from mcps.registry import MCPRegistry
from skills.registry import SkillRegistry
from tools.base import ToolRegistry


class AgentContext:
    """运行时自动组装 system prompt 与 tools，各模块独立维护。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.skills = SkillRegistry(root / "skills")
        self.tools = ToolRegistry(root / "tools")
        self.mcp = MCPRegistry(root / "mcps")
        self._base_prompt_path = root / "prompts" / "base.md"

    def refresh(self) -> None:
        self.skills.refresh()
        self.tools.refresh()
        self.mcp.refresh()

    def load_base_prompt(self) -> str:
        if self._base_prompt_path.exists():
            return self._base_prompt_path.read_text(encoding="utf-8").strip()
        return "你是一个有用的 AI Agent。"

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
        now = self._now()
        weekdays = "一二三四五六日"
        tz_name = os.getenv("FIAGENT_TZ", "Asia/Shanghai")
        offset = now.strftime("%z")
        offset_fmt = f"UTC{offset[:3]}:{offset[3:]}" if offset else ""
        return "\n".join([
            "## 当前时间（每轮自动更新，与现实一致）",
            f"- 现在：{now.strftime('%Y-%m-%d %H:%M:%S')} 星期{weekdays[now.weekday()]}",
            f"- 时区：{tz_name} {offset_fmt}".rstrip(),
            "- 回答涉及「今天」「本周」「现在」「最近」等问题时，请以此时间为准",
        ])

    def build_capabilities_index(self) -> str:
        lines = ["## 当前能力索引（自动生成）", ""]

        native = self.tools.all()
        if native:
            lines.append("### 内置工具")
            for name, summary in native:
                lines.append(f"- `{name}`: {summary}")

        mcp_tools = self.mcp.all()
        if mcp_tools:
            lines.append("")
            lines.append("### MCP 工具")
            for tool in mcp_tools:
                lines.append(f"- `{tool.name}`: {tool.description}")

        skills = self.skills.all()
        if skills:
            lines.append("")
            lines.append("### Skills（摘要；全文用 load_skill 加载）")
            lines.append(self.skills.get_descriptions())

        if len(lines) <= 2:
            return ""
        return "\n".join(lines)

    def build_system_prompt(self) -> str:
        parts = [self.load_base_prompt(), self.build_time_context()]
        index = self.build_capabilities_index()
        if index:
            parts.append(index)
        return "\n\n".join(parts)

    def build_openai_tools(self) -> list[dict[str, Any]]:
        schemas = self.tools.build_schemas(self)
        schemas.extend(self.mcp.build_schemas())
        return schemas

    def execute_tool(self, name: str, arguments: str) -> str:
        args = json.loads(arguments or "{}")
        if name.startswith("mcp_") or any(t.name == name for t in self.mcp.all()):
            return self.mcp.execute(name, args)
        return self.tools.execute(name, args, self)

    def fresh_messages(self) -> list[dict]:
        return [{"role": "system", "content": self.build_system_prompt()}]

    def sync_system_message(self, messages: list[dict]) -> list[dict]:
        prompt = self.build_system_prompt()
        if messages and messages[0].get("role") == "system":
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
