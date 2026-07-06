---
name: technical-analysis
description: 技术分析综合指南——15种蜡烛形态 + 7大指标(EMA/ADX/BB/RSI/OBV/量比/支撑阻力) + 缠论，纯pandas/numpy实现，涵盖趋势/振荡/量价/形态四大维度。
category: strategy
---

# 技术分析综合指南

覆盖四大维度：**趋势判断**（EMA/ADX/趋势线斜率）、**超买超卖**（BB/RSI）、**量价配合**（OBV/量比）、**形态识别**（蜡烛形态/支撑阻力/头肩顶/双底三角形）。

## 一、蜡烛形态识别（15 种）

已由 `pattern` 工具实现（`tools/pattern.py` + `tools/_pattern_lib.py`），支持自定义阈值调参。

### 单蜡烛 (5)
| 形态 | 信号 | 判定 |
|------|------|------|
| Hammer (锤子线) | 看涨 | 下影线 > 2×实体，上影线 < 实体，非Doji |
| Inverted Hammer (倒锤子) | 看涨 | 上影线 > 2×实体，下影线 < 实体 |
| Shooting Star (射击之星) | 看跌 | 出现在上升趋势后，上影线极长 |
| Doji (十字星) | 中性 | 实体/振幅 < 10% |
| Spinning Top (纺锤线) | 中性 | 实体小且上下影线均等 |

### 双蜡烛 (6)
| 形态 | 信号 |
|------|------|
| Bullish Engulfing (看涨吞没) | 看涨 — 当日阳线实体包住昨日阴线 |
| Bearish Engulfing (看跌吞没) | 看跌 — 当日阴线实体包住昨日阳线 |
| Bullish Harami (看涨孕线) | 看涨 |
| Bearish Harami (看跌孕线) | 看跌 |
| Piercing Line (刺透线) | 看涨 |
| Dark Cloud Cover (乌云盖顶) | 看跌 |

### 三蜡烛 (4)
| 形态 | 信号 |
|------|------|
| Morning Star (晨星) | 看涨 |
| Evening Star (黄昏星) | 看跌 |
| Three White Soldiers (红三兵) | 看涨 |
| Three Black Crows (三只乌鸦) | 看跌 |

### 多蜡烛形态（`pattern` 工具）
| 形态 | 工具 |
|------|------|
| 头肩顶 | `pattern(patterns="head_and_shoulders")` |
| 双顶/双底 | `pattern(patterns="double_top_bottom")` |
| 上升/下降三角形 | `pattern(patterns="triangle")` |
| 扩散形态 | `pattern(patterns="broadening")` |
| 支撑阻力位 | `pattern(patterns="support_resistance")` |

### 调参示例
```
pattern(
  code="600519.SH",
  start_date="2024-01-01", end_date="2024-12-31",
  patterns="candlestick,head_and_shoulders",
  window=10,
  cfg={"doji_body_ratio": 0.08, "hs_shoulder_symmetry": 0.04}
)
```

---

## 二、技术指标（7 大经典）

### 趋势类

**EMA 双均线**
```
fast = close.ewm(span=12).mean()
slow = close.ewm(span=26).mean()
signal = 1 if fast > slow else 0
```

**ADX 趋势强度**
```
+DM = max(high - prev_high, 0) if up_move > down_move else 0
-DM = max(prev_low - low, 0) if down_move > up_move else 0
TR  = max(high-low, abs(high-prev_close), abs(low-prev_close))
+DI = WilderSmooth(+DM) / WilderSmooth(TR) * 100
-DI = WilderSmooth(-DM) / WilderSmooth(TR) * 100
DX  = abs(+DI - -DI) / (+DI + -DI) * 100
ADX = WilderSmooth(DX)
```
> ADX > 25 → 趋势明确；ADX < 20 → 震荡市

**趋势线斜率**（`pattern` 工具已有）
```
trend_line_slope(close, window=20)
→ 滚动线性拟合斜率
```

### 振荡类

**布林带 (Bollinger Bands)**
```
middle = close.rolling(20).mean()
band   = close.rolling(20).std() * 2
upper  = middle + band
lower  = middle - band
→ 突破上轨=超买, 跌破下轨=超卖
```

**RSI 相对强弱**
```
gain = max(close - prev_close, 0).rolling(14).mean()
loss = max(prev_close - close, 0).rolling(14).mean()
RS   = gain / loss
RSI  = 100 - 100/(1+RS)
→ RSI < 30 = 超卖, RSI > 70 = 超买
```
> 注意：Wilder 用 `ewm(alpha=1/14)` 而非滚动均值

### 量价类

**OBV 能量潮**
```
OBV[t] = OBV[t-1] + volume[t]   if close[t] > close[t-1]
OBV[t] = OBV[t-1] - volume[t]   if close[t] < close[t-1]
OBV[t] = OBV[t-1]                if close[t] == close[t-1]
signal = 1 if OBV > OBV.rolling(20).mean() else 0
```
→ OBV 上升=资金流入，OBV 与价格背离=反转信号

**量比**
```
volume_ratio = volume / volume.rolling(20).mean()
→ > 2.0 = 放量（突破确认）；< 0.5 = 缩量（整理）
```

---

## 三、信号合成（三维投票）

```
长信号 = 趋势看多 + RSI 非超买 + OBV 上升   → 满足 ≥2 项才做多
短信号 = 趋势看空 + RSI 非超卖 + OBV 下降   → 满足 ≥2 项才做空
```

维度权重可按市场环境调整（趋势市重趋势、震荡市重振荡）。

---

## 四、fiagent 实操流程

```
1. get_market_data → 获取 OHLCV
2. pattern → 形态识别（蜡烛/头肩/双底/支撑阻力）
3. 手工计算指标（用 write 工具编写计算脚本）
4. 生成信号 CSV
5. run_backtest(strategy="custom", signal_file="signal.csv")
```

### 双均线 + RSI + 成交量确认信号示例

```python
import pandas as pd
df = ...  # from get_market_data

fast = df["close"].ewm(span=12).mean()
slow = df["close"].ewm(span=26).mean()
trend = (fast > slow)  # 趋势看多

delta = df["close"].diff()
gain = delta.clip(lower=0).ewm(alpha=1/14).mean()
loss = (-delta).clip(lower=0).ewm(alpha=1/14).mean()
rsi = 100 - 100 / (1 + gain / loss)
not_overbought = (rsi < 70)  # 非超买

vol_ratio = df["volume"] / df["volume"].rolling(20).mean()
volume_confirm = (vol_ratio > 1.2)  # 放量

# 三维投票
signal = pd.Series(0.0, index=df.index)
signal[trend & not_overbought & volume_confirm] = 1.0
signal.to_csv("signal.csv")
```

---

## 五、缠论（进阶）

缠中说禅理论是国内技术分析的"天花板"。fiagent 无内置缠论 tool，但可用 `get_market_data` 获取 OHLCV + `czsc` 库（`pip install czsc`）手动分析：

```python
from czsc import CZSC, RawBar, Freq
bars = [RawBar(symbol=sym, id=i, dt=d, freq=Freq.D, open=o, close=c, high=h, low=l, vol=v, amount=a) for ...]
c = CZSC(bars)
print(c.bi_list)    # 已完成笔
print(c.zs_list)    # 中枢
# 买卖点: 一买=背驰点, 二买=回调确认, 三买=中枢上移确认
```

---

## 六、常见陷阱

1. **前视偏差**：所有指标用 `.shift(1)` 确保 T 日信号仅用 T-1 及以前数据
2. **参数过拟合**：双均线 (5,20) 在茅台有效≠在所有股票有效，需多标的测试
3. **成交量陷阱**：A 股涨停板上的"无量涨停"≠缩量，是流动性限制
4. **形态假信号**：头肩顶在趋势市中假信号率高达 40%，需结合成交量确认
5. **RSI 钝化**：强趋势中 RSI 可长期 >70 或 <30，此时 RSI 信号无效（用 ADX>25 过滤）

## Dependencies

```bash
pip install pandas numpy
pip install czsc  # 可选，缠论分析
```
