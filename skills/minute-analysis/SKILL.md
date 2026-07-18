---
name: minute-analysis
description: A 股分钟级行情分析与回测。经 get_market_data / run_backtest（akshare 分钟）取 1/5/15/30/60 分钟 K。
category: strategy
---

# A 股分钟级分析与回测

取 A 股分钟 K，做日内指标（VWAP、量能分布等）或喂给 `run_backtest`。标的：`.SH` / `.SZ` / `.BJ`。

## 回测

```
run_backtest(
  codes=["600519.SH"],
  start_date="2026-03-01",
  end_date="2026-03-15",
  interval="5",
  strategy="ma_cross",
  strategy_params={"fast": 5, "slow": 20}
)
```

- `interval`：`1` / `5` / `15` / `30` / `60`（分钟）；`1d` 为日线  
- 分钟数据来自 `market.loaders.fetch_akshare_minute`（近端、历史较短）  
- 引擎可对超长分钟序列截断（约 3000 bar）  
- 建议窗口：`1` 分钟 ≤7 日，`5` 分钟 ≤30 日  

## 取数后自算指标

```
get_market_data(
  codes=["600519.SH"],
  start_date="2026-03-01",
  end_date="2026-03-15",
  interval="5m",
  source="auto"
)
```

再用 `run_python` / `write` 算 VWAP、分时量能等。

## 数据源

| 来源 | 周期 | 说明 |
|------|------|------|
| akshare 分钟 | 1/5/15/30/60 | `run_backtest` 非日线默认 |
| 腾讯/东财/mootdx/baostock | 日线为主 | `get_market_data` auto 链 |

## 注意

1. `run_backtest` 对 A 股仍应用 T+1 / 涨跌停规则  
2. 分钟缺口、停牌空洞需在分析里显式处理  
