# ui/tui/app.py

**路径**：`/ui/tui/app.py`

## 作用

Textual 全屏应用 `FiagentApp` 与 `run_tui()` 入口。

## 功能

- 聊天区 `VerticalScroll`：用户行、Thought 折叠、Assistant 流式、工具块
- 底部 `ImeInput` + 状态栏
- Esc 暂停、后台线程跑 `run_agent_turn`
- `/` 命令委托 `agent.handle_session_command`

## 样式

`CSS_PATH` → 同目录 `tui.tcss`

## 注意

`mount_rule()` 使用 `Rule.horizontal()` + `Static` 标题（Textual 8.x API）。
