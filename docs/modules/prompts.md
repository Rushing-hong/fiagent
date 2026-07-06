# prompts/

**路径**：`/prompts/`

## 作用

System prompt 静态模板。

## 文件

| 文件 | 说明 |
|------|------|
| `base.md` | Agent 角色、行为规范、工具使用原则 |

`AgentContext.build_system_prompt()` 会拼接 base + 实时时间 + skills 索引。
