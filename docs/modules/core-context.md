# core/context.py

**路径**：`/core/context.py`

## 作用

`AgentContext`：运行时组装 Agent 所需上下文。

## 职责

| 方法 | 说明 |
|------|------|
| `refresh()` | 重新扫描 skills / tools / mcps |
| `build_system_prompt()` | 构建所有 Agent 完全一致的共享 system 前缀 |
| `build_runtime_prompt()` | 构建 profile 角色、权限和 skills 索引等运行时约束 |
| `with_runtime_context_for_api()` | 在 API 副本中把运行时约束并入首个动态 user 上下文 |
| `build_openai_tools(messages=None)` | 导出 function 定义；传入消息时对明确意图做保守路由，模糊请求回退全量 |
| `fresh_messages()` | 新对话初始 messages |
| `sync_system_message()` | 更新已有 messages 中的 system 条 |
| `format_now()` | 当前时间（`FIAGENT_TZ`） |

## 依赖

`skills/registry`、`tools/base`、`mcps/registry`、`core/tool_routing`

## 性能与 Token

- 基础提示词、能力索引、system prompt 和工具 schema 都按文件状态与 registry generation 缓存。
- 主 Agent、研究 Agent、Red-Team 与 CIO 只有一条完全一致且不含日期的 system；profile 差异作为其后的动态 user 上下文注入，日期/精确时钟放在请求末尾。
- 工具 schema 会移除仅作注释的默认值等冗余字段，并限制过长说明；类型、必填项、枚举和边界约束保留。
- 默认采用缓存优先的稳定 tools schema；`FIAGENT_TOOL_ROUTING=on` 可显式开启按请求路由，适合更看重单次 Token 而非跨请求缓存的场景。
- `/cache` 展示服务端返回的缓存命中/未命中 Token 和累计命中率，`/cache reset` 清零当前进程统计。
- API 请求副本会移除历史 reasoning，并折叠正文完全一致的重复只读结果；持久化 session 保持原样。
