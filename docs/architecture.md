# 架构总览

## 运行时数据流

```
用户输入
  → hooks (turn.start)
  → session 追加 user 消息
  → core/loop  ReAct 循环
       ├─ hooks (llm.before)
       ├─ core/stream  流式 DeepSeek（思考 + 正文 + tool_calls）
       ├─ ui  展示思考 / 工具 / 回复
       ├─ tools  并行只读 / 串行写入
       └─ hooks (llm.after / tool.*)
  → session 持久化
  → hooks (turn.end)
```

## 分层职责

| 层 | 目录 | 职责 |
|----|------|------|
| 入口 | `agent.py` | CLI 参数、API Key、模式选择、主循环 |
| 核心 | `core/` | LLM 推理、工具编排、上下文、暂停 |
| 持久化 | `session/` | 多会话 SQLite、自动标题、过期清理 |
| 扩展 | `hooks/` | 事件钩子，不改主循环即可观测/拦截 |
| 界面 | `ui/` | Rich 纯终端 + Textual TUI，统一 `ui` 单例桥接 |
| 能力 | `tools/` `skills/` `market/` | 工具实现、技能文档、数据源 |

## 扩展方式

1. **新工具**：在 `tools/` 添加继承 `BaseTool` 的类，刷新 `/reload` 或重启。
2. **新技能**：在 `skills/<name>/SKILL.md` 编写说明，Agent 通过 `load_skill` 按需加载。
3. **新 Hook**：在 `hooks/` 写模块，在 `hooks.json` 注册事件。
4. **UI 偏好**：`data/ui_prefs.json`（思考折叠、TUI/纯终端模式）。

## 线程模型

- **TUI 模式**：用户输入在主线程；`run_agent_turn` 在后台线程执行，通过 `call_from_thread` 更新 UI。
- **Session**：每线程独立 SQLite 连接（`threading.local`），避免跨线程报错。
