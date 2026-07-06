# core/stream.py

**路径**：`/core/stream.py`

## 作用

`stream_chat_completion()`：对 DeepSeek 发起 **流式** chat completion（thinking 开启）。

## 行为

- 推理阶段：`ui.llm_activity_update` 显示「思考中…」
- 正文阶段：`ui.stream_reply_*` 逐字输出
- 正文开始前：`ui.show_thinking` 挂载 Thought（在回复之上）
- 增量合并 `tool_calls` delta，返回 `SimpleNamespace` 消息对象

## 依赖

`core/turn_control`、`ui`
