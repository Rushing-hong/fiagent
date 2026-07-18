---
name: event-driven
description: A 股事件驱动策略：新闻/公告情绪打分 → 事件 CSV → 与技术信号合成 → run_backtest(custom)。
category: strategy
---

# 事件驱动策略（A 股）

用新闻、公告、宏观事件生成情绪分数，经时间衰减后与技术信号加权，产出 **signal CSV**，再交给 `run_backtest(strategy="custom")`。

## 工作流

1. **取文本**：`read_url` / `get_stock_news` 等获取公告或新闻全文  
2. **LLM 打分**：用下方标准 prompt 输出 `[-1.0, 1.0]` 单一分数  
3. **写事件 CSV**：`date,event_type,score,source,summary`  
4. **合成交易信号**：`run_python` 读事件 CSV +（可选）OHLCV，输出 `signal.csv`  
5. **回测**：`run_backtest(codes=[...], strategy="custom", signal_file="signal.csv", ...)`

原则：**事件 CSV = 数据层；signal.csv = 交易层；run_backtest = 撮合层。**

## 事件 CSV Schema

```csv
date,event_type,score,source,summary
2024-01-15,earnings,0.8,get_stock_news,Q4 revenue beat expectations by 30%
2024-01-20,macro,-0.5,read_url,Central bank raised rates by 25bp
```

| Field | Type | Description |
|-------|------|-------------|
| date | `YYYY-MM-DD` | **可知日**（盘后发布 → 下一交易日） |
| event_type | str | `earnings / macro / policy / sentiment / insider / technical_break` |
| score | float | `[-1.0, 1.0]` |
| source | str | 来源标签 |
| summary | str | 一句摘要（避免未转义逗号） |

## 信号合成（写入 signal.csv）

用 `run_python` 实现时间衰减与加权：

```python
import numpy as np
import pandas as pd

def compute_event_signal(event_df, dates, decay_lambda=0.1,
                         min_score_threshold=0.2, event_lookback=30):
    event_df = event_df[event_df["score"].abs() >= min_score_threshold].copy()
    event_df["date"] = pd.to_datetime(event_df["date"])
    signal = pd.Series(0.0, index=dates)
    for trade_date in dates:
        mask = (event_df["date"] <= trade_date) & (
            event_df["date"] >= trade_date - pd.Timedelta(days=event_lookback)
        )
        relevant = event_df[mask]
        if relevant.empty:
            continue
        days_since = (trade_date - relevant["date"]).dt.days.values
        decayed = relevant["score"].values * np.exp(-decay_lambda * days_since)
        signal[trade_date] = float(np.clip(decayed.sum(), -1.0, 1.0))
    return signal

def combine_signals(tech_signal, event_signal, alpha=0.6):
    return (alpha * tech_signal + (1 - alpha) * event_signal).clip(-1.0, 1.0)
```

`signal.csv`：`index=date`，`columns` 为 A 股代码（如 `600519.SH`），值为 `[-1, 1]`。

## 参数

| Parameter | Default | Description |
|-----------|---------|-------------|
| alpha | 0.6 | 技术信号权重 |
| decay_lambda | 0.1 | 衰减系数 |
| event_lookback | 30 | 事件回看天数 |
| min_score_threshold | 0.2 | 分数阈值 |

## LLM 打分 Prompt

```
You are a financial event analyst. Score impact on the stock from -1.0 to 1.0.
Output one number only.

News:
{news_content}

Score:
```

## 常见陷阱

1. **前视**：事件 `date` 必须是可知日  
2. **重复计分**：按 `(date, event_type)` 去重或平均  
3. **稀疏**：多数交易日事件信号为 0 属正常  
4. **历史回测**：事件 CSV 需事先备齐  
