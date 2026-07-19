"""BaseTool + ToolRegistry: 自动发现注册。"""

from __future__ import annotations

import importlib.util
import inspect
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

# Schema 注入用：过长 description 浪费每轮 tokens；不改工具内部文案
_TOOL_DESC_MAX = int(os.environ.get("FIAGENT_TOOL_DESC_MAX", "160"))


def _clip_description(text: str, limit: int = _TOOL_DESC_MAX) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[: max(0, limit - 1)].rstrip() + "…"


class BaseTool(ABC):
    name: str = ""
    description: str = ""
    summary: str = ""
    parameters: dict[str, Any] = {}
    repeatable: bool = True
    is_readonly: bool = True
    dynamic_schema: bool = False

    @classmethod
    def check_available(cls) -> bool:
        return bool(cls.name)

    @abstractmethod
    def execute(self, args: dict[str, Any], ctx: Any) -> str:
        ...

    def to_openai_schema(self, ctx: Any = None) -> dict[str, Any]:
        if self.dynamic_schema:
            schema = self.build_schema(ctx)
            fn = schema.get("function")
            if isinstance(fn, dict) and "description" in fn:
                fn = {**fn, "description": _clip_description(str(fn.get("description") or ""))}
                return {**schema, "function": fn}
            return schema
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": _clip_description(self.description),
                "parameters": self.parameters or {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }

    def build_schema(self, ctx: Any) -> dict[str, Any]:
        return self.to_openai_schema(ctx)


class ToolRegistry:
    def __init__(self, tools_dir: Path) -> None:
        self.tools_dir = tools_dir
        self._tools: dict[str, BaseTool] = {}
        self._class_cache: dict[
            Path, tuple[tuple[int, int], list[type[BaseTool]]]
        ] = {}
        self.refresh()

    def _classes_in_module(self, module) -> list[type[BaseTool]]:
        found: list[type[BaseTool]] = []
        for attr_name in dir(module):
            if attr_name.startswith("_"):
                continue
            obj = getattr(module, attr_name)
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseTool)
                and obj is not BaseTool
                and not inspect.isabstract(obj)
                and obj.name
            ):
                found.append(obj)
        return found

    def _discover_classes(self) -> list[type[BaseTool]]:
        if not self.tools_dir.exists():
            return []

        classes: list[type[BaseTool]] = []
        live_paths: set[Path] = set()
        for path in sorted(self.tools_dir.glob("*.py")):
            if path.name.startswith("_") or path.name in ("base.py",) or path.name.startswith("test_"):
                continue
            live_paths.add(path)
            try:
                stat = path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                continue
            cached = self._class_cache.get(path)
            if cached is not None and cached[0] == signature:
                classes.extend(cached[1])
                continue
            module_name = f"_fiagent_tool_{path.stem}"
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            found = self._classes_in_module(module)
            self._class_cache[path] = (signature, found)
            classes.extend(found)
        stale = set(self._class_cache) - live_paths
        for path in stale:
            self._class_cache.pop(path, None)
        return classes

    def refresh(self) -> None:
        self._tools = {}
        for cls in self._discover_classes():
            if not cls.check_available():
                continue
            tool = cls()
            self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def all(self) -> list[tuple[str, str]]:
        items = []
        for tool in self._tools.values():
            summary = tool.summary or tool.description[:60]
            items.append((tool.name, summary))
        return sorted(items, key=lambda x: x[0])

    def build_schemas(self, ctx: Any) -> list[dict[str, Any]]:
        return [tool.to_openai_schema(ctx) for tool in self._tools.values()]

    def execute(self, name: str, args: dict[str, Any], ctx: Any) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"未知工具: {name}"
        try:
            return tool.execute(args, ctx)
        except TypeError as e:
            return f"工具 {name} 参数错误: {e}"
        except ValueError as e:
            return f"工具 {name} 参数值无效: {e}"
        except FileNotFoundError as e:
            return f"工具 {name} 文件未找到: {e}"
        except PermissionError as e:
            return f"工具 {name} 权限不足: {e}"
        except (ConnectionError, TimeoutError, OSError) as e:
            return f"工具 {name} 网络/IO 错误: {e}"
        except Exception as e:
            return f"工具 {name} 执行失败: {type(e).__name__}: {e}"
