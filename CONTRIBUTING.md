# 贡献指南

## 新增工具

1. 在 `tools/` 下创建 `your_tool.py`
2. 继承 `BaseTool`，设置 `name` / `description` / `parameters` / `is_readonly`
3. 实现 `execute(self, args, ctx) -> str` 方法
4. 运行 `python agent.py` → 输入 `/reload` 即可自动发现

```python
from tools.base import BaseTool

class MyTool(BaseTool):
    name = "my_tool"
    summary = "一句话描述"
    description = "详细描述，给 LLM 看的"
    parameters = {
        "type": "object",
        "properties": {
            "param1": {"type": "string", "description": "参数说明"},
        },
        "required": ["param1"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        # 实现逻辑，返回 JSON 字符串
        return '{"ok": true, "data": ...}'
```

- 只读工具（查数据）→ `is_readonly = True`，可并行执行
- 写入工具（写文件）→ `is_readonly = False`, `repeatable = False`，串行执行
- 数据获取建议封装在 `market/` 下，工具层只做参数转换和结果格式化

## 新增技能

1. 在 `skills/<name>/` 下创建 `SKILL.md`
2. 使用 YAML frontmatter 声明元信息
3. 正文写领域知识（公式、代码、参数、陷阱）

```markdown
---
name: your-skill
description: 一句话描述
category: strategy
---

# 技能标题

## 核心逻辑
...

## 参数
...

## 常见陷阱
...
```

- `category` 可选：`strategy` / `analysis` / `asset-class` / `flow` / `knowledge` / `tool`
- Agent 会在 system prompt 中看到摘要，按需 `load_skill` 加载全文
- 运行 `/reload` 后生效

## 测试

```bash
pip install pytest
pytest tests/ -v
```

## 代码规范

- Python 3.10+，类型注解推荐
- 工具返回值统一用 JSON（`market/envelope.py` 提供 `ok()` / `err()` 辅助函数）
- 成功信封默认含 `quality`（normal/degraded/partial）、`as_of`；有 caveat 时写一行 `note`
- 异常不直接暴露给用户，用 `err(f"xxx失败: {type(e).__name__}")`
- 数据源请求用 `market/http.py` 的节流函数，避免 IP 被封

## 提交规范

- `feat: 新增 xxx 工具`
- `fix: 修复 xxx`
- `refactor: 重构 xxx`
- `docs: 更新 xxx 文档`
