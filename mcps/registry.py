import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MCPTool:
    name: str
    description: str
    parameters: dict


class MCPRegistry:
    """MCP 工具注册表。在 mcps/mcp.json 中配置 server 后在此扩展加载逻辑。"""

    def __init__(self, mcp_dir: Path) -> None:
        self.mcp_dir = mcp_dir
        self.config_path = mcp_dir / "mcp.json"
        self._tools: list[MCPTool] = []
        self.refresh()

    def refresh(self) -> None:
        self._tools = []
        if not self.config_path.exists():
            return
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        for server_cfg in config.get("servers", {}).values():
            if not server_cfg.get("enabled", True):
                continue
            for tool in server_cfg.get("tools", []):
                self._tools.append(MCPTool(
                    name=tool["name"],
                    description=tool.get("description", ""),
                    parameters=tool.get("parameters", {"type": "object", "properties": {}}),
                ))

    def all(self) -> list[MCPTool]:
        return list(self._tools)

    def build_schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": f"[MCP] {tool.description}",
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools
        ]

    def execute(self, name: str, args: dict) -> str:
        return f"MCP 工具 {name} 尚未连接（请在 mcps/mcp.json 配置 server 并实现调用）"
