---
name: options-strategy
description: A 股 ETF 期权策略框架（方法论）+ get_option_chain 取 T 型报价；情景分析用 run_python 自算 BS/Greeks。
category: asset-class
---

> 🔧 **可用工具**：`get_option_chain` — ETF 期权 T 型报价（IV/Greeks/PCR 等，视数据源字段而定）。支持 50ETF / 300ETF / 500ETF / 1000ETF / 科创50ETF / 创业板ETF。

## 用途

讲解常见期权组合结构、BS 定价直觉与 Greeks，并用链上报价做分析。  
标的侧可用 `get_market_data` / `run_backtest`；期权侧用 `get_option_chain` + `run_python` 做静态情景或 Greeks，分开报告。

## 支持的策略结构

| Strategy | Structure | View |
|----------|-----------|------|
| Covered Call | 持标的 + 空 call | 温和看多、收权利金 |
| Protective Put | 持标的 + 多 put | 看多但要下行保护 |
| Straddle | 同执行价 call+put | 大波动、方向不明 |
| Strangle | 不同执行价 call+put | 大波动、成本更低 |
| Iron Condor | 卖 put 价差 + 卖 call 价差 | 震荡收权利金 |
| Butterfly | 多空 call/put 蝶式 | 窄幅波动 |
| Calendar Spread | 近月空 + 远月多（同执行价） | 吃时间价值差 |

## 取链上数据

```
get_option_chain(underlying="50ETF")
```

结合 `get_market_data` 看标的走势；需要定价/Greeks 时用 `run_python` 实现 BS。

## BS 公式（自算参考）

```
Call = S * N(d1) - K * e^(-rT) * N(d2)
Put  = K * e^(-rT) * N(-d2) - S * N(-d1)

d1 = [ln(S/K) + (r + sigma^2/2) * T] / (sigma * sqrt(T))
d2 = d1 - sigma * sqrt(T)
```

历史波动率可近似 IV，但真实市场有波动率微笑；深度虚值流动性差，忽略买卖价差会高估可成交性。

## Greeks

| Greek | 含义 | 用途 |
|-------|------|------|
| Delta | 标的变动 1 单位时期权价格变动 | 方向暴露 / 对冲比 |
| Gamma | Delta 对标的的敏感度 | 对冲稳定性 |
| Theta | 时间价值衰减（通常为负） | 卖方策略收益来源 |
| Vega | 波动率变动 1% 的价格敏感度 | 波动率交易核心 |

## 常见陷阱

1. **欧式行权**：A 股 ETF 期权为欧式；分红标的注意行权价值边界  
2. **合约乘数**：ETF 期权常见乘数 10000，自算 PnL 时必须写入  
3. **流动性**：深度虚值报价宽，链上中间价 ≠ 可成交价  
