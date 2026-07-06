# core/loop.py

**路径**：`/core/loop.py`

## 作用

ReAct 主循环 `run_agent_turn()`：

1. 同步 system message
2. 流式调用 LLM（`core/stream.py`）
3. 若有 `tool_calls`：并行执行只读工具、串行执行写入工具
4. 展示思考 / 工具 / 回复到 UI
5. 重复直至无工具或超过 `MAX_TOOL_ROUNDS`

## 策略

- 同工具重复调用：注入提醒文本，不硬阻断（写入类除外）
- 工具结果以 OpenAI `tool` 角色消息回传模型

## 依赖

`core/stream`、`core/context`、`core/turn_control`、`hooks/registry`、`ui`
