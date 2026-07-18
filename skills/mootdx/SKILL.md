---
name: mootdx
category: data-source
description: Mootdx A-share market data via TCP-direct 通达信 servers. Free, no API key, no IP rate limits. Use as the stable A-share OHLCV fallback when akshare's East Money scrape is throttled.
---

## Overview

Mootdx talks the native 通达信 (TDX) binary protocol over TCP, bypassing the HTTP scrapers that periodically fail under load (akshare → East Money is the canonical example). Public market data only — no token, no per-IP throttling, no captcha.

- GitHub: https://github.com/mootdx/mootdx
- Install: `pip install mootdx && pip install 'httpx>=0.28.1'`

> Mootdx pins `httpx<0.26` in `setup.py`；第二个 `pip install` 用于对齐本仓库使用的较新 httpx。

## Quick Start

```python
from mootdx.quotes import Quotes

client = Quotes.factory(market="std")  # std = 沪/深/京; ext = 期货/期权 (upstream-broken)

# Daily OHLCV with a date range (preferred API).
df = client.get_k_data(code="000001", start_date="2025-01-01", end_date="2025-02-01")

# Intraday — offset-from-latest only, no native date range.
df_15m = client.bars(symbol="600519", frequency=1, offset=800)
```

## Frequency Codes

`bars(frequency=N)` uses integer codes from `mootdx.consts`:

| Code | Bar |
|------|-----|
| 8 | 1m |
| 0 | 5m |
| 1 | 15m |
| 2 | 30m |
| 3 | 1H |
| 4 | 1D |
| 5 | 1W |
| 6 | 1M |

`get_k_data()` is **daily only** but accepts `start_date / end_date`. For intraday, `bars()` returns the latest N rows — the built-in loader over-fetches `offset=800` then clips to the requested window.

## Key Methods

| Method | Use | Returns |
|--------|-----|---------|
| `get_k_data(code, start_date, end_date)` | Daily OHLCV with date range | `[open, close, high, low, vol, amount, date, code]` |
| `bars(symbol, frequency, offset=800)` | Intraday / weekly / monthly | `[open, close, high, low, vol, amount, datetime, volume, ...]` |
| `minute(symbol)` | Current trading day 1m bars | Same schema as `bars()` |
| `quotes(symbol)` | Real-time L1 snapshot | `{price, bid, ask, volume, ...}` |
| `stocks(market)` | List all tickers on an exchange | DataFrame of `code/name` |
| `F10(symbol)` / `finance(symbol)` | Fundamentals snapshot | Heterogeneous dict |

## Symbol Format

- Pure 6-digit: `"000001"`, `"600519"`, `"835174"` — mootdx auto-detects exchange from prefix:
  - `60x / 68x` → SH
  - `00x / 30x / 002 / 003` → SZ
  - `4x / 8x` → BJ
- The built-in loader also accepts `"000001.SZ"`, `"600519.SH"`, `"835174.BJ"` and strips the suffix.

## Column Names

`get_k_data()` returns lowercase English: `open / close / high / low / vol / amount / date / code`. The built-in loader renames `vol` → `volume` to match the project's OHLCV contract.

`bars()` returns the same OHLC columns plus a duplicate `volume` (alongside the legacy `vol`), a `datetime` string column, and decomposed `year / month / day / hour / minute` columns.

## 项目内接入（A 股）

封装在 `market/loaders.py` → `fetch_mootdx`，由 `get_market_data(source="mootdx"|"auto")` 调用。

A 股日线默认链（`source=auto`）：`tencent → mootdx → eastmoney → baostock → akshare`。

```python
# 工具侧
# get_market_data(codes=["600519.SH"], start_date="2024-01-01", end_date="2024-12-31", source="mootdx")

from market.loaders import fetch_mootdx
rows = fetch_mootdx("600519.SH", "2024-01-01", "2024-12-31")
```

## Known Limitations

| Limitation | Workaround |
|------------|------------|
| 北交所 (BJ): `get_k_data` 可能 KeyError / 空 | 换 `source=akshare` / `eastmoney` |
| `bars()` 单页约 800 行 | 更长分钟史用 `run_backtest` 的 akshare 分钟源 |
| 首连选服较慢 | 首次调用可多等约 2s |
| 默认前复权 | 需要其它复权口径时换源核对 |

## Reference Docs

- Mootdx 文档: https://www.mootdx.com/
- 通达信协议参考: https://github.com/rainx/pytdx
