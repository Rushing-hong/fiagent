# core/turn_control.py

**路径**：`/core/turn_control.py`

## 作用

单轮对话的运行控制：

- **Esc 暂停**：`request_pause()`，循环内 `checkpoint()` 阻塞等待 `resume` 或 `abort`
- **中止**：`TurnAborted` 异常，回滚本轮 messages
- **TUI 模式**：`set_tui_mode(True)` 时不启动 stdin 监听（由 Textual 处理 Esc）

## 使用

`core/loop`、`core/stream` 在关键点调用 `turn_control.checkpoint()`。
