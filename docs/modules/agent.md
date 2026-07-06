# agent.py

**路径**：`/agent.py`

## 作用

程序唯一入口。负责：

- 解析 CLI（`--resume`、`--list`、`--plain`、`--tui`）
- 加载 `.env` / 提示并保存 `DEEPSEEK_API_KEY`
- 选择 TUI 或 Rich 纯终端模式
- 注册并分发 `/` 会话命令
- 驱动主对话循环或启动 Textual 应用

## 关键符号

| 名称 | 说明 |
|------|------|
| `SESSION_COMMANDS` | 内置 `/` 命令表 |
| `HANDLED_RESTART` | `/tui` `/plain` 切换后退出重启的信号 |
| `bootstrap()` | 共享启动：API、hooks、session、context |
| `main_plain()` | Rich 交互循环 |
| `main()` | 默认走 TUI，失败回退 plain |

## 依赖

`core/`、`session/`、`hooks/`、`ui/`、`paths.py`
