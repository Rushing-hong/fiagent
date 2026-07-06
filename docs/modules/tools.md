# tools/

**路径**：`/tools/`

## 作用

Agent 可调用的 Python 工具（OpenAI function calling），扫描自 `tools/*.py`。

## 基类

`base.py` — `BaseTool` + `ToolRegistry` 自动发现。

## 主要工具

| 文件 | 工具名 | 说明 |
|------|--------|------|
| `stock_market.py` | 行情/K线 | A 股实时与历史 |
| `stock_flow.py` | 资金流 | 主力/北向等 |
| `stock_disclosure.py` | 公告披露 | |
| `stock_research.py` | 研报摘要 | |
| `iwencai.py` | iwencai_search | 问财选股 |
| `web.py` | web_search / read_url | DuckDuckGo + Jina |
| `grep.py` | grep | 代码库搜索 |
| `read.py` / `write.py` / `edit.py` | 文件读写 | |
| `skills.py` | load_skill | 加载 SKILL.md |
| `factor_analysis.py` | 因子分析 | |
| `pattern.py` | 形态识别 | |
| `trade_journal.py` | 交易日志 | |

内部模块 `_fs.py`、`_pattern_lib.py` 等以 `_` 开头，不注册为工具。

## 扩展

新增工具类 → `tools/` 下建文件 → `/reload` 或重启。
