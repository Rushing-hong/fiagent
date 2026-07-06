import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from paths import PROJECT_ROOT

HookHandler = Callable[["HookContext"], "HookContext | None"]

ROOT = PROJECT_ROOT
HOOKS_DIR = ROOT / "hooks"
HOOKS_CONFIG = ROOT / "hooks.json"

# 支持的 hook 事件
EVENTS = (
    "session.start",
    "session.end",
    "turn.start",
    "turn.end",
    "llm.before",
    "llm.after",
    "tool.before",
    "tool.after",
)


@dataclass
class HookContext:
    event: str
    data: dict[str, Any] = field(default_factory=dict)
    cancel: bool = False

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value


class HookRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, list[HookHandler]] = {e: [] for e in EVENTS}

    def on(self, event: str, handler: HookHandler) -> None:
        if event not in self._handlers:
            raise ValueError(f"未知 hook 事件: {event}")
        self._handlers[event].append(handler)

    def emit(self, event: str, data: dict[str, Any] | None = None) -> HookContext:
        ctx = HookContext(event=event, data=dict(data or {}))
        for handler in self._handlers[event]:
            result = handler(ctx)
            if result is not None:
                ctx = result
            if ctx.cancel:
                break
        return ctx

    def load_from_config(self, config_path: Path = HOOKS_CONFIG) -> list[str]:
        if not config_path.exists():
            return []
        loaded = []
        config = json.loads(config_path.read_text(encoding="utf-8"))
        for event, modules in config.get("hooks", {}).items():
            if event not in self._handlers:
                continue
            for module_path in modules:
                self._load_module(event, module_path)
                loaded.append(f"{event} <- {module_path}")
        return loaded

    def _load_module(self, event: str, module_path: str) -> None:
        path = ROOT / module_path
        if not path.exists():
            raise FileNotFoundError(f"Hook 模块不存在: {path}")
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载 hook 模块: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        handlers = getattr(module, "HANDLERS", None)
        if isinstance(handlers, dict) and event in handlers:
            self.on(event, handlers[event])
            return
        register = getattr(module, "register", None)
        if callable(register):
            register(self)
            return
        handler = getattr(module, "handle", None)
        if callable(handler):
            self.on(event, handler)
