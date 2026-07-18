"""
生成自定义信号 CSV：当日收盘价 > 20日均线 → 做多(+1)，否则空仓(0)
结合 RSI 过滤：RSI > 70 超买时也空仓。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# 保证从任意 cwd 可导入仓库根
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from market.market_data import fetch_market_data

codes = ["600519.SH", "300750.SZ", "002594.SZ", "000858.SZ", "601318.SH"]
start = "2025-01-01"
end = "2026-07-10"

payload = fetch_market_data(
    codes=codes,
    start_date=start,
    end_date=end,
    source="auto",
    interval="1D",
    max_rows=0,
)

frames: list[pd.DataFrame] = []
for code in codes:
    entry = payload.get(code) or {}
    if "error" in entry:
        print(f"WARN skip {code}: {entry['error']}")
        continue
    rows = entry.get("data") or []
    if not rows:
        print(f"WARN skip {code}: empty data")
        continue
    part = pd.DataFrame(rows)
    part["code"] = code
    frames.append(part)

if not frames:
    print("ERROR: no data")
    sys.exit(1)

df = pd.concat(frames, ignore_index=True)
prices = df.pivot_table(index="trade_date", columns="code", values="close")
prices = prices.sort_index()

ma20 = prices.rolling(20).mean()

delta = prices.diff()
gain = delta.clip(lower=0)
loss = (-delta).clip(lower=0)
avg_gain = gain.rolling(14).mean()
avg_loss = loss.rolling(14).mean()
rs = avg_gain / avg_loss.replace(0, np.nan)
rsi = 100 - (100 / (1 + rs))

signal = pd.DataFrame(0, index=prices.index, columns=prices.columns)
for code in prices.columns:
    cond = (prices[code] > ma20[code]) & (rsi[code] <= 70)
    signal[code] = cond.astype(int)

signal = signal.iloc[30:]

out_path = _ROOT / "signals" / "custom_ma_rsi_signal.csv"
signal.to_csv(out_path)
print(f"Signal saved to {out_path}, shape={signal.shape}")
print(f"Signal coverage: {signal.sum().sum()} signals across {len(signal.columns)} stocks")
for c in signal.columns:
    s = signal[c]
    print(f"  {c}: {s.sum()} long days / {len(s)} total = {s.sum()/len(s)*100:.1f}%")
