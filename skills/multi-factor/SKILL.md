---
name: multi-factor
description: Multi-factor cross-sectional stock ranking using Z-score standardization, IC-weighted combination, and TopN portfolio construction. Fully integrated with fiagent tools.
category: strategy
---

# 多因子截面选股

## 核心逻辑

多因子模型的核心：**在每一期截面上，计算多只股票的多个因子值，标准化后合成一个综合得分，选排名最高的股票构建组合。**

```
对每个交易日:
  1. 计算 N 个因子的截面值（动量/价值/质量/波动率...）
  2. 每个因子做截面 Z-score 标准化
  3. 按 IC 权重加权合成综合得分
  4. 选 Top N 股票，等权持有
```

## fiagent 完整流程

### Step 1: 获取候选池行情

```
get_market_data(codes=["000001.SZ","600036.SH","000858.SZ","600519.SH",...],
                 start_date="2023-01-01", end_date="2025-12-31")
```

候选池可以是：沪深300 成分股、某个行业全部股票、或 `screen_fundamental` 筛选结果。

### Step 2: 计算因子值

用 `write` 工具创建 `compute_factors.py`：

```python
import pandas as pd
import numpy as np

# 从 get_market_data JSON 加载多只股票的 OHLCV
# 输出格式: index=date, columns=stocks, values=factor_value

def momentum_factor(close_panel, window=20):
    """动量因子：过去 N 日收益率，越高越好"""
    return close_panel.pct_change(window)

def reversal_factor(close_panel, window=5):
    """反转因子：过去 5 日收益率，越低越好（取负值）"""
    return -close_panel.pct_change(window)

def volatility_factor(close_panel, window=20):
    """波动率因子：过去 N 日波动率，越低越好（取负值）"""
    return -close_panel.pct_change().rolling(window).std()

def volume_factor(volume_panel, window=20):
    """量比因子：今日量/N日均量，越高越好"""
    return volume_panel / volume_panel.rolling(window).mean()

# 计算并保存因子 CSV（每个因子一个文件）
momentum = momentum_factor(close_panel, 20)
momentum.to_csv("factor_momentum.csv")
volatility = volatility_factor(close_panel, 20)
volatility.to_csv("factor_volatility.csv")
```

### Step 3: 因子评估

用 `factor_analysis` 测试每个因子：

```
factor_analysis(
  factor_csv="factor_momentum.csv",
  return_csv="forward_return_5d.csv",
  output_dir="./output_momentum",
  n_groups=5
)
```

→ 得到每个因子的 IC 均值、IC_std、IR、分位回测净值。

### Step 4: 筛选有效因子

`factor-research` skill 的 IC 阈值标准：

| IC 均值 | 解读 |
|---------|------|
| > 0.03 | 因子有基本预测力 |
| > 0.05 | 因子有强预测力 |
| > 0.10 | 异常高，检查前视偏差 |
| IR > 0.5 | 因子稳定有效 |
| IC>0 占比 > 55% | 方向稳定 |

### Step 5: 因子合成（等权/IC加权）

```python
# 等权合成
composite = (zscore(factor1) + zscore(factor2) + zscore(factor3)) / 3

# IC 加权合成
weight_i = abs(IC_i) / sum(abs(IC_j) for j in factors)
composite = sum(w * zscore(f) for w, f in zip(weights, factors))

# zscore 实现
def zscore(df):
    return (df - df.mean(axis=1, skipna=True)) / df.std(axis=1, skipna=True)
```

### Step 6: 构建组合信号

```python
# 每期选 composite score 最高的 Top 5 只，等权
rank = composite.rank(axis=1, ascending=False, method="first")
signal = pd.DataFrame(0.0, index=composite.index, columns=composite.columns)
signal[rank <= 5] = 1.0 / 5  # 等权 = 1/5
signal = signal.fillna(0.0)
signal.to_csv("multi_factor_signal.csv")
```

### Step 7: 回测

```
run_backtest(
  codes=["000001.SZ","600036.SH","000858.SZ","600519.SH",...],
  start_date="2023-01-01", end_date="2025-12-31",
  strategy="custom",
  signal_file="multi_factor_signal.csv"
)
```

## 常见因子列表

| 因子 | 计算 | 方向 | A股 IC 均值参考 |
|------|------|------|---------------|
| 动量 | N 日收益率 | 正向 | 0.03-0.05 |
| 反转 | 5 日收益率 | 负向 | 0.02-0.04 |
| 波动率 | N 日标准差 | 负向 | 0.03-0.06 |
| 换手率 | 成交量/N日均量 | 正向 | 0.02-0.04 |
| 市值 | log(总市值) | 负向（小盘溢价） | 0.04-0.08 |
| PE | 1/PE | 正向（便宜好） | 0.02-0.05 |
| PB | 1/PB | 正向 | 0.02-0.04 |
| ROE | ROE | 正向 | 0.03-0.06 |

（用 `screen_fundamental` 可获取 PE/PB/ROE/市值数据）

## 常见陷阱

- **前视偏差**：因子值用 T 日数据，收益用 T+1 到 T+N。不要把 T 日的收益率当因子。
- **幸存者偏差**：只用现存股票回测会高估表现。尽量用全样本（含退市股）。
- **行业中性化**：同类行业因子值高度相似会导致选股集中在少数行业。解决方法：行业内部分别做 Z-score。
- **因子拥挤**：动量/价值等经典因子的超额收益近年下降。定期检查 IC 衰减。
- **截面标准化需要≥3只股票**。
