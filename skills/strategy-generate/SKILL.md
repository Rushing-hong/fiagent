---
name: strategy-generate
description: Create, modify, backtest and optimize quantitative trading strategies using fiagent tools.
category: strategy
---

# 策略生成与回测

## 完整工作流

```
get_market_data  →  选择策略  →  run_backtest  →  分析 metrics  →  迭代优化
                      │
            ┌─────────┼─────────┐
            ▼         ▼         ▼
         内置策略   自定义信号   混合策略
```

## Step 1: 获取数据

```
get_market_data(codes=["600519.SH"], start_date="2020-01-01", end_date="2025-12-31")
```

支持多标的、多源（东财/腾讯/akshare），分钟线到月线。

## Step 2: 选择策略

### 方式 A — 内置策略（最简单）

4 种内置策略，传参数即可：

| 策略 | 参数 | 适用场景 |
|------|------|---------|
| `ma_cross` | `fast=5, slow=20` | 趋势跟踪，单边行情 |
| `rsi` | `period=14, oversold=30, overbought=70` | 均值回归，震荡市 |
| `momentum` | `window=20` | 突破买入 |
| `buy_hold` | — | 基准对照 |

```json
{
  "strategy": "ma_cross",
  "strategy_params": {"fast": 5, "slow": 20}
}
```

### 方式 B — 自定义信号（灵活）

1. 用 `get_market_data` 获取 OHLCV
2. 用 `write` 创建信号计算脚本，输出 CSV（index=date, columns=股票代码, values=-1~1）
3. 调用 `run_backtest(strategy="custom", signal_file="signal.csv")`

```python
# 示例：布林带突破信号
import pandas as pd
df = ...  # 从 market_data.json 加载
middle = df["close"].rolling(20).mean()
std = df["close"].rolling(20).std()
upper, lower = middle + 2*std, middle - 2*std
signal = pd.Series(0.0, index=df.index)
signal[df["close"] > upper.shift(1)] = 1.0      # 突破上轨 → 买入
signal[df["close"] < lower.shift(1)] = 0.0      # 跌破下轨 → 卖出
# 持仓延续：用 fillna(method="ffill")
signal.to_csv("signal.csv")
```

### 方式 C — 混合（内置策略 + 基本面过滤）

1. `screen_fundamental(max_pe=15, min_roe=15, top_n=10)` → 选出候选池
2. 对候选池中的每只股票分别跑 `run_backtest` 或构建组合信号

## Step 3: 执行回测

```
run_backtest(
  codes=["600519.SH"],
  start_date="2020-01-01",
  end_date="2025-12-31",
  strategy="ma_cross",
  strategy_params={"fast": 5, "slow": 20},
  initial_cash=1000000
)
```

自动执行 A 股规则：T+1、涨跌停、佣金 0.03%、印花税 0.05%（卖）。

## Step 4: 分析结果

回测返回 JSON，Agent 可直接解读：

```json
{
  "metrics": {
    "total_return": 85.3,      "annual_return": 12.5,
    "sharpe_ratio": 1.15,      "max_drawdown": -22.3,
    "win_rate": 55.3,          "total_trades": 47,
    "avg_win": 8520,            "avg_loss": -4200,
    "buy_hold_return": 35.2
  },
  "trades": [...],
  "equity_curve": [...]
}
```

## Step 5: 审查清单

### 硬门（任一不通过 → 策略无效）

1. `total_trades > 0` — 零交易 = 信号 BUG
2. `max_drawdown < 50` — 腰斩不可接受
3. `annual_return > -10` — 不是一直亏

### 评分规则

| 指标 | 优秀 | 合格 | 差 |
|------|------|------|-----|
| 夏普比率 | > 1.5 | 0.8-1.5 | < 0.8 |
| 最大回撤 | < 15% | 15-30% | > 30% |
| 胜率 | > 60% | 40-60% | < 40% |
| 跑赢买入持有 | ✓ | — | ✗ |

### BUG 分类

| 症状 | 原因 | 修复 |
|------|------|------|
| trade_count=0 | 信号条件太严或全为 NaN | 放宽阈值，加 `fillna(0)` |
| 首笔交易 >2年后 | 回看窗口过长 | 缩短 window |
| 资金利用率 <50% | 仓位计算错误 | 检查 weight 值 |
| 还有持仓未平 | 退出信号缺失 | 加 `signal.iloc[-1] = 0` |

## Step 6: 迭代优化

每次迭代：改参数 → `run_backtest` → 比较 `sharpe_ratio` 和 `max_drawdown` → 保留更优参数。

```
迭代1: ma_cross(fast=5, slow=20)  → sharpe=1.15, dd=-22%
迭代2: ma_cross(fast=5, slow=30)  → sharpe=1.32, dd=-18%  ← 更优
迭代3: ma_cross(fast=10, slow=30) → sharpe=0.98, dd=-25%  ✗ 变差
```

## 策略设计 5 问（开始编码前）

1. **数据需求**：只需 OHLCV？还是要 PE/PB/ROE？（用 `get_market_data` vs `screen_fundamental`）
2. **信号逻辑**：买入条件是什么？卖出条件？方向（只做多/多空）？
3. **仓位管理**：等权重还是按信号强度？风险控制（止损）？
4. **回测参数**：时间范围、初始资金、佣金
5. **验证清单**：信号一致性（无 NaN）、无前视偏差（用 `.shift(1)`）

## 常见前视偏差（必须避免）

```
错误：df["close"] > df["close"].rolling(20).mean()  → 用了当天的 close 算当天信号
正确：df["close"] > df["close"].shift(1).rolling(20).mean()  → 用 T-1 的 MA 算 T 日信号
```

## 更多信号生成模板

### 布林带突破
```python
middle = close.rolling(20).mean()
band = close.rolling(20).std() * 2
signal[close > middle.shift(1) + band.shift(1)] = 1.0
signal[close < middle.shift(1) - band.shift(1)] = 0.0
```

### 双均线 + 成交量确认
```python
long_signal = (fast_ma > slow_ma) & (volume > volume.rolling(20).mean() * 1.5)
signal[long_signal] = 1.0
```

### 多因子合成
```python
momentum_score = close.pct_change(20).rank(pct=True)
vol_score = (1 / close.pct_change().rolling(20).std()).rank(pct=True)
composite = momentum_score * 0.6 + vol_score * 0.4
signal[composite > composite.quantile(0.8)] = 1.0
```
