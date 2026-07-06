# ui/collapse.py

**路径**：`/ui/collapse.py`

## 作用

长文本折叠辅助（借鉴 **OpenCode** 终端信息密度策略）。

## 函数

| 函数 | 说明 |
|------|------|
| `collapse_output(text, max_lines, max_chars)` | 截断并标记是否溢出 |
| `first_line_summary(text)` | 取首行摘要 |
| `reasoning_summary(text)` | 思考内容标题行 |
| `max_chars_for_lines(lines, width)` | 按终端宽度估字符上限 |

## 环境变量

`FIAGENT_THINK_MAX_LINES`（默认 3）、`FIAGENT_TOOL_MAX_LINES`（默认 10）
