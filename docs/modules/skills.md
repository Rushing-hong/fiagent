# skills/

**路径**：`/skills/`

## 作用

领域知识库：每个子目录一份 `SKILL.md`，描述何时用、怎么用相关工具与流程（自 **Vibe-Trading** 迁移）。

## 注册

`skills/registry.py` 扫描目录，将摘要注入 system prompt；Agent 通过 `load_skill` 读取全文。

## 规模

约 55+ 技能，涵盖：

- 数据源：`eastmoney`、`tushare`、`akshare`、`mootdx`、`data-routing`
- 分析：`technical-basic`、`minute-analysis`、`factor-research`、`risk-analysis` …
- 策略：`strategy-generate`、`backtest-diagnose`、`pair-trading` …
- 其他：`report-generate`、`regulatory-knowledge`、`vnpy-export` …

## 目录约定

```
skills/<name>/
  SKILL.md          # 必需
  scripts/          # 可选示例脚本
  references/       # 可选参考文档
  example_signal_engine.py  # 部分策略技能
```

单个 skill 不再单独建 md；详见各目录内 `SKILL.md`。
