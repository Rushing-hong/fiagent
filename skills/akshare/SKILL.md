---
name: akshare
category: data-source
description: AKShare A 股数据接入——get_market_data / screen_market / 分钟回测兜底；亦可 run_python 直接调库。
---

## Overview

AKShare 免费开源金融数据库，聚合东财等公开源。

- GitHub: https://github.com/akfamily/akshare
- Install: `pip install akshare`

## Quick Start（A 股）

```python
import akshare as ak

df = ak.stock_zh_a_hist(
    symbol="000001", period="daily",
    start_date="20240101", end_date="20260101", adjust="qfq",
)
spot = ak.stock_zh_a_spot_em()
```

## 高频接口（A 股）

| Function | Description | Key Params |
|----------|-------------|------------|
| `stock_zh_a_hist()` | A 股日/周/月 OHLCV | symbol, period, start_date, end_date, adjust |
| `stock_zh_a_spot_em()` | 全市场实时行情 | （无） |
| `stock_individual_info_em()` | 个股基本信息 | symbol |
| `stock_zh_a_hist_min_em()` | 分钟 K | symbol, period(1/5/15/30/60) |

宏观 / 期货等其它接口见官方文档；本产品内置链路聚焦上表。

## Column Names

| Chinese | English | Description |
|---------|---------|-------------|
| 日期 | date | Trade date |
| 开盘 | open | Open price |
| 最高 | high | High price |
| 最低 | low | Low price |
| 收盘 | close | Close price |
| 成交量 | volume | Volume（手） |
| 成交额 | amount | Turnover |
| 涨跌幅 | pct_change | % change |
| 换手率 | turnover_rate | Turnover rate |

## Date / Symbol

- 日期入参：`YYYYMMDD` 字符串
- A 股 symbol：纯数字 `"000001"`（无交易所后缀）

## 项目内接入

| 入口 | 路径 / 工具 | 说明 |
|------|-------------|------|
| 日线 OHLCV | `market/loaders.fetch_akshare` | `get_market_data(source="akshare"|"auto")` 链末级 |
| 分钟线 | `fetch_akshare_minute` | `run_backtest(interval="1"|"5"|…)` |
| 全市场排行 | `stock_zh_a_spot_em` | `screen_market` 东财失败时降级 |

## Reference

https://akshare.akfamily.xyz/
