# fiagent 文档索引

## 总览

| 文档 | 说明 |
|------|------|
| [architecture.md](architecture.md) | 运行时架构、数据流、扩展点 |
| [references.md](references.md) | Vibe-Trading / OpenCode 参考对照 |

## 根目录

| 模块 | 文件 |
|------|------|
| 入口 | [modules/agent.md](modules/agent.md) |
| 路径常量 | [modules/paths.md](modules/paths.md) |
| Hook 配置 | [modules/hooks-json.md](modules/hooks-json.md) |

## core/ — Agent 运行时

| 模块 | 文件 |
|------|------|
| ReAct 循环 | [modules/core-loop.md](modules/core-loop.md) |
| 流式 LLM | [modules/core-stream.md](modules/core-stream.md) |
| 上下文组装 | [modules/core-context.md](modules/core-context.md) |
| 暂停控制 | [modules/core-turn-control.md](modules/core-turn-control.md) |

## session/ — 会话

| 模块 | 文件 |
|------|------|
| SQLite 存储 | [modules/session-store.md](modules/session-store.md) |

## hooks/ — 扩展钩子

| 模块 | 文件 |
|------|------|
| 注册表 | [modules/hooks-registry.md](modules/hooks-registry.md) |
| 日志示例 | [modules/hooks-log.md](modules/hooks-log.md) |

## ui/ — 界面

| 模块 | 文件 |
|------|------|
| UI 桥接 (Rich/TUI) | [modules/ui-init.md](modules/ui-init.md) |
| 折叠工具 | [modules/ui-collapse.md](modules/ui-collapse.md) |
| 偏好设置 | [modules/ui-prefs.md](modules/ui-prefs.md) |
| Textual 应用 | [modules/ui-tui-app.md](modules/ui-tui-app.md) |
| 输入组件 | [modules/ui-tui-widgets.md](modules/ui-tui-widgets.md) |
| TUI 样式 | [modules/ui-tui-tcss.md](modules/ui-tui-tcss.md) |

## 数据与能力

| 目录 | 文件 |
|------|------|
| market/ | [modules/market.md](modules/market.md) |
| tools/ | [modules/tools.md](modules/tools.md) |
| skills/ | [modules/skills.md](modules/skills.md) |
| mcps/ | [modules/mcps.md](modules/mcps.md) |
| prompts/ | [modules/prompts.md](modules/prompts.md) |
| analysis/ | [modules/analysis.md](modules/analysis.md) |
