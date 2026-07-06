# hooks/log.py

**路径**：`/hooks/log.py`

## 作用

示例 Hook：在关键节点向 UI 打印一行 `hook:...` 日志。

## 注册

通过 `hooks.json` 挂到各事件；导出 `HANDLERS` 字典。

## 典型输出

```
hook:turn.start 用户输入: '...'
hook:llm.before 第 1 轮 LLM 调用
hook:llm.after 工具调用数: 2
```

生产环境可从 `hooks.json` 移除或替换为审计/限流等实现。
