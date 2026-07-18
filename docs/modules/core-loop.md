# core/loop.py

**路径**：`/core/loop.py`

## 作用

ReAct 主循环 `run_agent_turn()`（停止设计对齐 OpenCode）：

1. 同步 system message
2. 流式调用 LLM（`core/stream.py`）
3. 若有 `tool_calls`：并行只读、串行写入
4. 展示思考 / 工具 / 回复到 UI
5. 无工具则正常结束；达软上限则关工具、注入总结提示，文本收尾

## 停止策略

| 机制 | 行为 |
|------|------|
| 模型自然结束 | 无 `tool_calls` → 结束本轮 |
| 软步骤上限 `FIAGENT_MAX_TOOL_ROUNDS`（默认 40） | **最后一步** `tool_choice=none` + 总结提示；不硬塞「已达上限」假回复 |
| `doom_loop`（默认连续 3 次同名同参） | 拒绝该次调用，可换参继续 |
| 用户 Esc / abort | `turn_control` 中止 |

## 依赖

`core/stream`、`core/context`、`core/turn_control`、`hooks/registry`、`ui`
