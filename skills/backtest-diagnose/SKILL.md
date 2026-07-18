---
name: backtest-diagnose
description: 诊断 A 股 run_backtest 失败或表现异常：读工具返回 / 信号 CSV，定位根因并重跑验证。
category: tool
---

# 回测诊断（A 股）

用户反馈回测失败、报错或结果异常时使用本技能。

工作流：`get_market_data` →（可选自定义 `signal_file`）→ `run_backtest`。

## 诊断流程

1. **读工具返回**：上一轮 `run_backtest` / `get_market_data` 的 JSON 信封（`ok` / `error` / `data.metrics` / `data.trades`）
2. **若 strategy=custom**：用 `read` 检查 `signal_file` CSV（index=日期，columns=代码，values∈[-1,1]）
3. **归类问题**（见下表）
4. **修复**：`edit` 改信号脚本或调整 `run_backtest` 参数；勿无关大改策略逻辑
5. **重跑**：再调一次 `run_backtest`，对照 metrics / trade_count

## 错误分类

### 工具直接失败（`ok: false`）

| 症状 | 常见原因 | 处理 |
|------|----------|------|
| 未获取到行情 | 代码非 `.SH/.SZ/.BJ`、日期无交易日、源全挂 | 核对代码；换 `source`；收窄区间重试 |
| 需要 codes 或 universe_asof | 入参缺失 | 补 `codes` 或先 `build_tradable_universe` |
| custom 缺 signal_file | 未传路径或文件缺失 | 补齐 CSV 路径 |
| 信号列对不上 | CSV 列名 ≠ codes | 对齐列名与代码后缀 |

### 跑通但结果异常

1. **零成交**（`trade_count=0`）：信号过严或全 0；检查 CSV / 内置参数（RSI 阈值、均线周期）
2. **很晚才第一笔**：回看窗口过长或 `dropna` 过猛；缩短 `strategy_params` 窗口
3. **长期空仓**：信号稀疏或仓位逻辑过紧
4. **期末仍持仓**：自定义信号缺少平仓；可接受时在报告中说明强制清算行为

### 数据侧

若错误含 `rate limit` / `API limit` / `daily limit` / 源「无数据」：换 `get_market_data` 的 `source`，或等待限流恢复。

## 硬门禁（修复后须满足）

1. `run_backtest` 返回 `ok: true`
2. `trade_count > 0`（除非用户明确要空仓基准）
3. metrics 中收益/回撤无异常 `NaN`（若字段存在）
4. 修复迭代 ≤ 3 次；每次只改一类问题后立即重跑

## 执行假设相关参数

诊断「过度乐观」时检查：`commission`、`stamp_duty`、`signal_lag`、`exec_price`、`use_impact_model`、`impact_coef`、`reject_limit_lock`、`skip_halted`。详见 `execution-model`。

## `action_items` 写法

- 具体到参数名与取值：`"把 rsi oversold 从 30 改为 25 后重跑 run_backtest"`
- 自定义信号：`"在 signal.csv 生成脚本里对信号 fillna(0)，并保证列名含 600519.SH"`
- 至少给出 2 条可执行项
