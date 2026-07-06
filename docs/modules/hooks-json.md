# hooks.json

**路径**：`/hooks.json`

## 作用

声明各 Hook 事件要加载的 Python 模块路径（相对项目根）。

## 示例

```json
{
  "version": 1,
  "hooks": {
    "turn.start": ["hooks/log.py"],
    "llm.before": ["hooks/log.py"]
  }
}
```

## 模块约定

加载的 `.py` 需提供以下之一：

- `HANDLERS = {"event.name": callable}`
- `register(registry)` 函数
- 单个 `handle(ctx)` + 单事件绑定
