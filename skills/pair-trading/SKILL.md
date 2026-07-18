---
name: pair-trading
description: Pair trading strategy using spread/ratio Z-score mean reversion. Fully implemented workflow using fiagent tools.
category: strategy
---

# 配对交易策略

## 核心逻辑

选择两个高度相关的 **A 股** 标的（同行业龙头等），监控其价格比值（或价差）偏离均值的程度，在极端偏离时建仓，回归时平仓。`get_market_data` / `run_backtest` 仅支持 A 股代码。

```
信号逻辑:
  ratio = close_A / close_B
  z_score = (ratio - rolling_mean) / rolling_std

  z_score < -entry  → 买入 A, 卖出 B（比值过低，预期回归）
  z_score > +entry  → 卖出 A, 买入 B（比值过高，预期回归）
  |z_score| < exit  → 平仓
```

## fiagent 完整实施流程

### Step 1: 获取双标的行情

```
get_market_data(codes=["600519.SH", "000858.SZ"], start_date="2023-01-01", end_date="2025-12-31")
```

→ 得到两个 OHLCV DataFrame

### Step 2: 计算配对交易信号

用 `write` 工具创建 Python 脚本 `compute_pair_signal.py`：

```python
import pandas as pd
import numpy as np
import json

# 从 get_market_data 的 JSON 输出加载数据
data = json.loads(open("market_data.json").read())
df_a = pd.DataFrame(data["data"]["600519.SH"]["data"]).set_index("trade_date")
df_b = pd.DataFrame(data["data"]["000858.SZ"]["data"]).set_index("trade_date")

# 参数
lookback = 60
entry_z = 2.0
exit_z = 0.5

# 计算比值和 Z-score
close_a = df_a["close"].astype(float)
close_b = df_b["close"].astype(float)
ratio = close_a / close_b
ratio_mean = ratio.rolling(lookback).mean()
ratio_std = ratio.rolling(lookback).std()
z = (ratio - ratio_mean) / ratio_std

# 生成信号 (-1 = 卖A买B, 0 = 平仓, 1 = 买A卖B)
signal = pd.Series(0.0, index=z.index)
position = 0  # 0=flat, 1=long_spread(A多B空), -1=short_spread(A空B多)

for i in range(lookback, len(z)):
    if position == 0:
        if z.iloc[i] < -entry_z:
            signal.iloc[i] = 1.0   # 比值低 → 买A卖B
            position = 1
        elif z.iloc[i] > entry_z:
            signal.iloc[i] = -1.0  # 比值高 → 卖A买B
            position = -1
    elif abs(z.iloc[i]) < exit_z:
        signal.iloc[i] = 0.0       # 回归 → 平仓
        position = 0
    else:
        signal.iloc[i] = signal.iloc[i-1]  # 持仓中

# 保存为 run_backtest 可用的自定义信号格式
signal_df = pd.DataFrame({"600519.SH": signal, "000858.SZ": -signal})
signal_df.to_csv("pair_signal.csv")
```

### Step 3: 回测

```
run_backtest(
  codes=["600519.SH", "000858.SZ"],
  start_date="2023-01-01",
  end_date="2025-12-31",
  strategy="custom",
  signal_file="pair_signal.csv"
)
```

→ 得到绩效指标和交易明细

### Step 4: 优化参数

修改 `entry_z` (1.5-2.5)、`exit_z` (0.3-0.8)、`lookback` (30-120)，重新运行 Step 2-3，比较夏普比率。

## 配对选择指南

| 类型 | A标的 | B标的 | 逻辑 |
|------|-------|-------|------|
| 白酒双雄 | 600519.SH 茅台 | 000858.SZ 五粮液 | 同行业龙头 |
| 银行双雄 | 601398.SH 工商银行 | 601939.SH 建设银行 | 同质化经营 |
| 保险双雄 | 601318.SH 中国平安 | 601628.SH 中国人寿 | 行业对标 |
| 券商双雄 | 600030.SH 中信证券 | 601211.SH 国泰君安 | 同质化强 |
| 新能源 | 300750.SZ 宁德时代 | 002594.SZ 比亚迪 | 产业链相关 |

## 常见陷阱

- 协整关系可能破裂（历史相关≠未来相关），需定期重检
- lookback 窗口填满前 Z-score 为 NaN，信号应填 0
- A 股不能做空，配对交易实际是"买A + 观望B"而非"买A + 卖B"
- 手续费双向收取，配对交易成本是单边策略的两倍
- 停牌风险：一只停牌时另一只还在交易，配对关系短暂失效
