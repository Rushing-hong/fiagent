# ui/__init__.py

**路径**：`/ui/__init__.py`

## 作用

`AgentUI` 类与全局单例 `ui`：统一 Rich 纯终端与 Textual TUI 的输出接口。

## 模式

- `ui.bind_tui(app)` 后，各 `show_*` 方法通过 `_tui_call` 转发到 `FiagentApp`
- 纯终端：Rich Panel / Table / Live 流式输出

## 主要 API

| 类别 | 方法 |
|------|------|
| 对话 | `show_user_message`、`show_thinking`、`show_reply`、`show_tool_*` |
| 流式 | `llm_round_start`、`stream_reply_begin/update/end` |
| 折叠 | `expand_slot`、`list_collapsed`（纯终端） |
| 状态 | `llm_status`、`set_busy`（经 TUI） |

## 缓存

长内容折叠项写入 `data/ui_cache/`，供 `e` / 数字键展开。
