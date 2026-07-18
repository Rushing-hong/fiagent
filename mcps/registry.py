import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MCPTool:
    name: str
    description: str
    parameters: dict
    server_id: str = ""


@dataclass
class MCPServer:
    id: str
    enabled: bool
    tools: list[MCPTool] = field(default_factory=list)
    note: str = ""


class MCPRegistry:
    """MCP 工具注册表。在 mcps/mcp.json 中配置 server；支持按 server / tool 开关。"""

    def __init__(self, mcp_dir: Path) -> None:
        self.mcp_dir = mcp_dir
        self.config_path = mcp_dir / "mcp.json"
        self._tools: list[MCPTool] = []
        self._servers: list[MCPServer] = []
        self.refresh()

    def _load_config(self) -> dict:
        if not self.config_path.exists():
            return {"servers": {}}
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"servers": {}}
        if not isinstance(data, dict):
            return {"servers": {}}
        servers = data.get("servers")
        if not isinstance(servers, dict):
            data["servers"] = {}
        return data

    def _save_config(self, config: dict) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def refresh(self) -> None:
        from ui.prefs import get_disabled_mcp_tools

        config = self._load_config()
        disabled_tools = get_disabled_mcp_tools()
        self._servers = []
        self._tools = []
        for server_id, server_cfg in (config.get("servers") or {}).items():
            if not isinstance(server_cfg, dict):
                continue
            enabled = bool(server_cfg.get("enabled", True))
            note = str(server_cfg.get("description") or server_cfg.get("url") or "")
            tools: list[MCPTool] = []
            for tool in server_cfg.get("tools") or []:
                if not isinstance(tool, dict) or not tool.get("name"):
                    continue
                mcp_tool = MCPTool(
                    name=str(tool["name"]),
                    description=str(tool.get("description", "")),
                    parameters=tool.get("parameters")
                    or {"type": "object", "properties": {}},
                    server_id=str(server_id),
                )
                tools.append(mcp_tool)
            server = MCPServer(
                id=str(server_id),
                enabled=enabled,
                tools=tools,
                note=note,
            )
            self._servers.append(server)
            if enabled:
                for t in tools:
                    if t.name not in disabled_tools:
                        self._tools.append(t)

    def servers(self) -> list[MCPServer]:
        return list(self._servers)

    def get_server(self, server_id: str) -> MCPServer | None:
        for s in self._servers:
            if s.id == server_id:
                return s
        return None

    def set_server_enabled(self, server_id: str, enabled: bool) -> bool:
        config = self._load_config()
        servers = config.setdefault("servers", {})
        if server_id not in servers or not isinstance(servers[server_id], dict):
            raise KeyError(server_id)
        servers[server_id]["enabled"] = enabled
        self._save_config(config)
        self.refresh()
        return enabled

    def toggle_server(self, server_id: str) -> bool:
        server = self.get_server(server_id)
        if server is None:
            raise KeyError(server_id)
        return self.set_server_enabled(server_id, not server.enabled)

    def all(self) -> list[MCPTool]:
        """当前生效的 MCP 工具（server 开 + tool 未禁用）。"""
        return list(self._tools)

    def build_schemas(self) -> list[dict]:
        # 真实 MCP 调用未实现前不注入 schema，避免模型点选 stub
        return []

    def execute(self, name: str, args: dict) -> str:
        return f"MCP 工具 {name} 尚未连接（请在 mcps/mcp.json 配置 server 并实现调用）"
