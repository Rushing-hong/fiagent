---
name: asset-allocation
description: A 股资产配置理论与工具——MPT / BL / 风险预算 / 全天候；对接 blend_black_litterman、analyze_portfolio_risk、run_backtest。
category: asset-class
---

# Asset Allocation and Portfolio Optimization

## Overview

从资产配置理论到落地：MPT / Black-Litterman / 风险预算 / 全天候。  
工具：`blend_black_litterman`（观点→权重，可写 signal CSV）、`analyze_portfolio_risk`（Barra-lite）、`run_backtest`（验证）；逆波动 / 风险平价等可用 `run_python` 按理论节公式自算。

## Asset Allocation Theory

### 1. Modern Portfolio Theory (MPT, Markowitz)

**Core idea**: maximize expected return for a given level of risk (the efficient frontier).

```
Optimization problem:
min  w'Σw              (portfolio variance)
s.t. w'μ = target_return
     Σw = 1
     w ≥ 0              (no shorting)
```

| Advantages | Disadvantages |
|------|------|
| Mathematically rigorous | Extremely sensitive to inputs (garbage in, garbage out) |
| Efficient frontier is visualizable | Concentrated-allocation problem (often produces extreme weights) |
| Foundational framework | Assumes normality and ignores fat tails |

**Practical advice**: do not use raw MPT directly. Add constraints (upper/lower bounds, sector limits) or use a regularized version.

### 2. Black-Litterman Model

**Core idea**: start from market equilibrium and incorporate investor views.

```
Steps:
1. Reverse-imply market equilibrium returns: π = δΣw_mkt
2. Build the view matrices: P (selection matrix), Q (view returns), Ω (view uncertainty)
3. Blend the posterior: μ_BL = [(τΣ)^-1 + P'Ω^-1 P]^-1 [(τΣ)^-1 π + P'Ω^-1 Q]
4. Run Markowitz optimization using posterior μ_BL
```

**Example views**:
- Absolute view: "China A-shares will return 10% over the next year"  → `P=[1,0,0], Q=[0.10]`
- Relative view: "沪深300 将跑赢中证500 约 5%"   → `P=[1,-1,0], Q=[0.05]`

**Parameter guidance**:
- `τ` (uncertainty scaling): `0.025-0.05`
- `Ω`: set according to view confidence, where higher confidence = smaller variance

### 3. Risk Budgeting

**Core idea**: allocate by risk contribution rather than by capital share.

```
Risk contribution: RC_i = w_i × (Σw)_i / σ_p
Target: RC_i / σ_p = budget_i  (for all i)
```

| Strategy | Risk Budget | Best Use Case |
|------|---------|---------|
| Equal risk contribution | Each asset 1/N | When you do not know which asset is best |
| Equity-tilted risk budget | Stocks 60%, bonds 30%, commodities 10% | When you want equities to contribute more risk |
| Dynamic risk budget | Adjust dynamically by signal strength | When you have market-timing ability |

### 4. All-Weather Strategy

**Bridgewater framework**: allocate risk equally across economic environments.

```
Economic environment   Asset allocation
─────────              ─────────
Growth rising          Equities + commodities + corporate bonds
Growth falling         Government bonds + inflation-protected bonds
Inflation rising       Commodities + inflation-protected bonds + EM debt
Inflation falling      Equities + government bonds

Simplified allocation example for China-focused portfolios:
- 30% CSI 300 / CSI 500
- 40% government bonds / credit bonds
- 15% gold
- 15% commodities / REITs
```

## 工具对照

| 需求 | 工具 | 说明 |
|------|------|------|
| BL 观点融合 → 权重 | `blend_black_litterman` | 可写 `signal_file` 供 `run_backtest(custom)` |
| 组合风险分解 | `analyze_portfolio_risk` | mom/size/vol（可选行业）Barra-lite |
| 回测验证 | `run_backtest` | A 股 OHLCV；执行参数见 execution-model |
| 逆波动 / 风险平价 / 最大分散 | `run_python` | 公式见上文理论节 |

### 推荐工作流

```
get_market_data(codes=A股列表, ...)
  → blend_black_litterman(...) 或 run_python 算目标权重
  → 写出 signal.csv（日期 × 代码，值∈[-1,1] 或目标仓位约定）
  → run_backtest(strategy="custom", signal_file=...)
  → analyze_portfolio_risk(...) 解释风险贡献
```

### 方法选型（概念）

```
有收益观点？
├── Yes → blend_black_litterman（或自算 MV，务必加权重约束）
└── No → 需要相关结构？
    ├── Yes → run_python 风险平价 / analyze_portfolio_risk 辅助
    └── No → 逆波动等权（run_python 一行公式即可）
```

## Rebalancing Strategy

### Three Rebalancing Triggers

| Method | Trigger Condition | Advantages | Disadvantages |
|------|---------|------|------|
| Periodic rebalancing | Fixed monthly / quarterly date | Simple, predictable trading cost | May miss or delay adjustments |
| Threshold trigger | Deviation from target weight > X% | Trades only when needed | Frequent trading in high-volatility markets |
| Volatility trigger | VIX / volatility breaks a threshold | Adapts to market regime | Parameter selection is difficult |

### Suggested Rebalancing Frequency

| Asset Class | Suggested Frequency | Threshold |
|---------|---------|------|
| Equity portfolio | Monthly | ±5% |
| Stock-bond mix | Quarterly | ±10% |
| Multi-asset (股债金) | Quarterly / semiannual | ±10% |

### Rebalancing in Backtests

在生成 `signal.csv` 的 `run_python` 脚本里做再平衡：

```python
# 每 20 个交易日重算目标权重，写入各列信号
if bar_count % rebalance_freq == 0:
    new_weights = calculate_target_weights(data_map)
    for code, weight in new_weights.items():
        signals[code].iloc[i] = weight
```

## Cross-Asset Correlation Analysis

### Typical Correlation Matrix（A 股为主示例）

| | 沪深300 | 中证500 | 国债 | 黄金ETF |
|--|--------|--------|------|---------|
| 沪深300 | 1.00 | 0.85 | -0.15 | 0.05 |
| 中证500 | 0.85 | 1.00 | -0.10 | 0.03 |
| 国债 | -0.15 | -0.10 | 1.00 | 0.20 |
| 黄金ETF | 0.05 | 0.03 | 0.20 | 1.00 |

**Key patterns**:
- 股债负相关是配置基础（并非永远成立，如 2022 股债双杀）
- 黄金与权益相关性低，可作对冲
- 大盘与小盘 A 股相关性高（约 0.85），分散收益有限

## Output Format

```markdown
## Asset Allocation Recommendation

### Allocation Plan
| Asset | Weight | Risk Contribution | Expected Return (Annualized) |
|------|------|---------|--------------|
| 沪深300ETF | 35% | 50% | 8% |
| 中证500ETF | 15% | 25% | 9% |
| 国债/信用债 ETF | 40% | 15% | 3% |
| 黄金 ETF | 10% | 10% | 5% |

### 工具调用提示
- 权重：`blend_black_litterman` 或自算后写入 signal.csv  
- 风险：`analyze_portfolio_risk(codes=..., weights=..., start_date=..., end_date=...)`

### Expected Risk / Return
| Metric | Value |
|------|-----|
| Expected annualized return | 7.2% |
| Expected annualized volatility | 8.5% |
| Expected Sharpe | 0.85 |
| Expected maximum drawdown | -12% |

### Rebalancing Rules
- Frequency: quarterly (first trading day of March / June / September / December)
- Threshold: trigger when any asset deviates from target by ±10%
- Cost: estimated annual trading cost 0.15%
```

## Notes

1. **标的数量**：优化至少 3 只 A 股才有意义；2 只用逆波动即可  
2. **lookback**：过短（&lt;20）噪、过长（&gt;120）钝，60 日较常用  
3. **MV 陷阱**：最易过拟合；样本外 Sharpe 常腰斩——优先 BL 工具或强约束  
4. **再平衡成本**：A 股印花税卖出 0.05% + 佣金，高频再平衡会吃掉收益——对照 `run_backtest` 的 `commission` / `stamp_duty`  
5. **标的范围**：`run_backtest` 使用 A 股代码（`.SH` / `.SZ` / `.BJ`）  
6. **杠杆**：权重和默认 ≤ 1.0，除非用户明确允许  
7. **幸存者偏差**：历史相关会被退市/新上市扭曲
