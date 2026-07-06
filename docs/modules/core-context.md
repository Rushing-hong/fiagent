# core/context.py

**路径**：`/core/context.py`

## 作用

`AgentContext`：运行时组装 Agent 所需上下文。

## 职责

| 方法 | 说明 |
|------|------|
| `refresh()` | 重新扫描 skills / tools / mcps |
| `build_system_prompt()` | 合并 `prompts/base.md`、时间、skills 索引 |
| `build_openai_tools()` | 导出 OpenAI function 定义列表 |
| `fresh_messages()` | 新对话初始 messages |
| `sync_system_message()` | 更新已有 messages 中的 system 条 |
| `format_now()` | 当前时间（`FIAGENT_TZ`） |

## 依赖

`skills/registry`、`tools/base`、`mcps/registry`
