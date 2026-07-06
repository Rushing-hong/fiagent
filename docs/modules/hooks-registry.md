# hooks/registry.py

**路径**：`/hooks/registry.py`

## 作用

Hook 事件注册与分发。

## 事件

`session.start` / `session.end` / `turn.start` / `turn.end` / `llm.before` / `llm.after` / `tool.before` / `tool.after`

## API

| 方法 | 说明 |
|------|------|
| `on(event, handler)` | 注册处理器 |
| `emit(event, data)` | 触发，返回 `HookContext`（可 `cancel`） |
| `load_from_config()` | 读取 `hooks.json` 动态加载模块 |

## HookContext

`data` 字典 + `cancel` 标志；handler 可修改并返回新 context。
