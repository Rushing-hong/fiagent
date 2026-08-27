# Prompt Cache 工程约定

本项目默认采用“缓存优先”请求布局：

1. 所有 Agent 的第一条 system 仅包含稳定的 `prompts/base.md`。
2. Profile、权限与 Skills 索引并入首个动态 user 上下文，
   不创建第二条 system，也不写回 session。
3. 用户消息、工具历史和实时时钟位于稳定前缀之后；精确时钟始终在请求末尾。
4. Tool schema 保持确定性排序并默认不按问题变化。只有显式设置
   `FIAGENT_TOOL_ROUTING=on` 时才启用 token-first 路由。
5. OpenAI 官方端点使用由共享 system 哈希生成的稳定 `prompt_cache_key`；
   DeepSeek 使用其默认磁盘缓存。
6. `/cache` 查看当前进程收到的服务端缓存命中、未命中和写入 Token。

## 取舍

缓存依赖精确前缀。动态裁剪工具可减少单次输入，但工具定义变化可能使后续
system/messages 缓存失效，因此不再作为默认策略。一次性任务或缓存复用很低的
工作负载可以开启工具路由，并通过 `/cache` 与实际延迟对比后决定。

缓存是尽力而为：并行请求可能在首个缓存尚未完成写入时同时 miss，且不同模型、
端点或变化的思考配置不能假设共享缓存。优化效果必须以 API usage 返回值为准。

## 官方依据

- OpenAI Prompt Caching：<https://developers.openai.com/api/docs/guides/prompt-caching>
- DeepSeek Context Caching：<https://api-docs.deepseek.com/guides/kv_cache/>
- Anthropic Prompt Caching：<https://platform.claude.com/docs/en/build-with-claude/prompt-caching>
- Gemini Context Caching：<https://ai.google.dev/gemini-api/docs/caching>
