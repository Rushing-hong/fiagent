"""A股因子库 v0：价量 Alpha + Barra-lite 风格风险因子。

命名空间：
  alpha_*  — 选股/预测用（可做 IC）
  risk_*   — 风险暴露用（勿与 Alpha 混作选股回归）

Phase1 Week2：仅用 OHLCV，不做财报 EP/成长（后续周补）。
"""

from __future__ import annotations

from typing import Any, Callable

import numpy as np
import pandas as pd

from market.backtest_p2 import zscore

# --- registry ---

ALPHA_FACTOR_IDS = (
    "alpha_mom_1m",
    "alpha_mom_3m",
    "alpha_rev_5d",
    "alpha_rev_10d",
    "alpha_overnight_5d",
    "alpha_vol_20d",
    "alpha_liquidity",
    "alpha_size",
)

RISK_FACTOR_IDS = (
    "risk_mom",
    "risk_size",
    "risk_vol",
    "risk_beta",
    "risk_liquidity",
    "risk_residual_vol",
    "risk_nonlinear_size",
)

# 符号假设表（Alpha 验收：不符 → invert_signal_note）
ALPHA_SIGN_EXPECTATION: dict[str, int] = {
    "alpha_mom_1m": 1,       # 中期动量常为正（A股不稳，仅基线）
    "alpha_mom_3m": 1,
    "alpha_rev_5d": -1,      # 短期反转
    "alpha_rev_10d": -1,
    "alpha_overnight_5d": -1,  # 隔夜收益偏彩票/反转假设
    "alpha_vol_20d": -1,     # 高波常惩罚
    "alpha_liquidity": -1,   # 高换手常惩罚（拥挤）
    "alpha_size": -1,        # 小市值溢价假设（A股常翻脸）
}


def _loc(df: pd.DataFrame, date: pd.Timestamp) -> int | None:
    if date not in df.index:
        return None
    loc = df.index.get_loc(date)
    if isinstance(loc, slice):
        return None
    return int(loc)


def _ret(df: pd.DataFrame, i: int, window: int) -> float:
    if i < window:
        return float("nan")
    c0 = float(df["close"].iloc[i - window])
    c1 = float(df["close"].iloc[i])
    if c0 <= 0:
        return float("nan")
    return c1 / c0 - 1.0


def _trailing_vol(df: pd.DataFrame, i: int, window: int = 20) -> float:
    if i < window:
        return float("nan")
    closes = df["close"].astype(float).iloc[i - window : i + 1]
    rets = closes.pct_change().dropna()
    if len(rets) < max(5, window // 2):
        return float("nan")
    return float(rets.std() * np.sqrt(252))


def _adv_log(df: pd.DataFrame, i: int, window: int = 20) -> float:
    start = max(0, i - window + 1)
    sub = df.iloc[start : i + 1]
    amt = (sub["close"].astype(float) * sub["volume"].astype(float)).mean()
    if not amt or amt <= 0 or not np.isfinite(amt):
        return float("nan")
    return float(np.log(amt))


def _turnover(df: pd.DataFrame, i: int, window: int = 20) -> float:
    """Proxy: volume / mean volume (no float shares) — relative activity."""
    if i < window:
        return float("nan")
    vol = df["volume"].astype(float).iloc[i - window : i + 1]
    m = float(vol.mean())
    if m <= 0:
        return float("nan")
    return float(vol.iloc[-1] / m)


def _mean_overnight(df: pd.DataFrame, i: int, window: int = 5) -> float:
    """Mean overnight return open_t / close_{t-1} - 1 over trailing window."""
    if i < window:
        return float("nan")
    opens = df["open"].astype(float).iloc[i - window + 1 : i + 1]
    prev_closes = df["close"].astype(float).iloc[i - window : i]
    if len(opens) != len(prev_closes) or len(opens) == 0:
        return float("nan")
    rets = []
    for o, c0 in zip(opens.tolist(), prev_closes.tolist()):
        if c0 and c0 > 0 and np.isfinite(o) and np.isfinite(c0):
            rets.append(o / c0 - 1.0)
    if len(rets) < max(2, window // 2):
        return float("nan")
    return float(np.nanmean(rets))


def cross_section_raw(
    data: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    codes: list[str],
    factor_id: str,
    *,
    market_rets: pd.Series | None = None,
) -> dict[str, float]:
    """Raw (pre-zscore) factor values for one day."""
    out: dict[str, float] = {}
    for code in codes:
        df = data.get(code)
        if df is None:
            out[code] = float("nan")
            continue
        i = _loc(df, date)
        if i is None:
            out[code] = float("nan")
            continue
        if factor_id in ("alpha_mom_1m", "risk_mom"):
            out[code] = _ret(df, i, 20)
        elif factor_id == "alpha_mom_3m":
            out[code] = _ret(df, i, 60)
        elif factor_id == "alpha_rev_5d":
            out[code] = _ret(df, i, 5)
        elif factor_id == "alpha_rev_10d":
            out[code] = _ret(df, i, 10)
        elif factor_id == "alpha_overnight_5d":
            out[code] = _mean_overnight(df, i, 5)
        elif factor_id in ("alpha_vol_20d", "risk_vol", "risk_residual_vol"):
            out[code] = _trailing_vol(df, i, 20)
        elif factor_id in ("alpha_liquidity", "risk_liquidity"):
            out[code] = _turnover(df, i, 20)
        elif factor_id in ("alpha_size", "risk_size", "risk_nonlinear_size"):
            out[code] = _adv_log(df, i, 20)
        elif factor_id == "risk_beta":
            out[code] = float("nan")  # filled in batch
        else:
            out[code] = float("nan")
    if factor_id == "risk_beta" and market_rets is not None:
        out = _beta_vs_market(data, date, codes, market_rets)
    if factor_id == "risk_residual_vol" and market_rets is not None:
        out = _resid_vol(data, date, codes, market_rets)
    if factor_id == "risk_nonlinear_size":
        # square of size then z later
        base = {c: (v * v if np.isfinite(v) else float("nan")) for c, v in out.items()}
        out = base
    return out


def _stock_rets(df: pd.DataFrame, i: int, window: int = 60) -> np.ndarray | None:
    if i < window:
        return None
    closes = df["close"].astype(float).iloc[i - window : i + 1]
    return closes.pct_change().dropna().values


def _beta_vs_market(
    data: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    codes: list[str],
    market_rets: pd.Series,
    window: int = 60,
) -> dict[str, float]:
    out: dict[str, float] = {}
    for code in codes:
        df = data.get(code)
        if df is None:
            out[code] = float("nan")
            continue
        i = _loc(df, date)
        if i is None:
            out[code] = float("nan")
            continue
        sr = _stock_rets(df, i, window)
        if sr is None or len(sr) < 20:
            out[code] = float("nan")
            continue
        # align last len(sr) market rets ending at date
        if date not in market_rets.index:
            out[code] = float("nan")
            continue
        loc_m = market_rets.index.get_loc(date)
        if isinstance(loc_m, slice) or int(loc_m) < len(sr):
            out[code] = float("nan")
            continue
        mr = market_rets.iloc[int(loc_m) - len(sr) + 1 : int(loc_m) + 1].values
        if len(mr) != len(sr) or np.nanstd(mr) < 1e-12:
            out[code] = float("nan")
            continue
        cov = np.nanmean((sr - np.nanmean(sr)) * (mr - np.nanmean(mr)))
        var = np.nanvar(mr)
        out[code] = float(cov / var) if var > 0 else float("nan")
    return out


def _resid_vol(
    data: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    codes: list[str],
    market_rets: pd.Series,
    window: int = 60,
) -> dict[str, float]:
    out: dict[str, float] = {}
    betas = _beta_vs_market(data, date, codes, market_rets, window=window)
    for code in codes:
        df = data.get(code)
        if df is None:
            out[code] = float("nan")
            continue
        i = _loc(df, date)
        if i is None:
            out[code] = float("nan")
            continue
        sr = _stock_rets(df, i, window)
        if sr is None or len(sr) < 20 or date not in market_rets.index:
            out[code] = float("nan")
            continue
        loc_m = market_rets.index.get_loc(date)
        if isinstance(loc_m, slice) or int(loc_m) < len(sr):
            out[code] = float("nan")
            continue
        mr = market_rets.iloc[int(loc_m) - len(sr) + 1 : int(loc_m) + 1].values
        b = betas.get(code, float("nan"))
        if not np.isfinite(b):
            out[code] = float("nan")
            continue
        resid = sr - b * mr
        out[code] = float(np.nanstd(resid) * np.sqrt(252))
    return out


def equal_weight_market_returns(data: dict[str, pd.DataFrame]) -> pd.Series:
    closes = {
        c: df["close"].astype(float)
        for c, df in data.items()
        if "close" in df.columns
    }
    if not closes:
        return pd.Series(dtype=float)
    panel = pd.DataFrame(closes).sort_index().ffill()
    return panel.pct_change().mean(axis=1)


def compute_day_zscores(
    data: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    codes: list[str],
    factor_ids: list[str],
    *,
    market_rets: pd.Series | None = None,
) -> dict[str, dict[str, float]]:
    """factor_id -> {code: zscore}."""
    if market_rets is None and any(
        f in factor_ids for f in ("risk_beta", "risk_residual_vol")
    ):
        market_rets = equal_weight_market_returns(data)
    result: dict[str, dict[str, float]] = {}
    for fid in factor_ids:
        raw = cross_section_raw(data, date, codes, fid, market_rets=market_rets)
        arr = [raw[c] for c in codes]
        zs = zscore(arr)
        result[fid] = {
            codes[i]: float(zs[i]) if np.isfinite(zs[i]) else 0.0
            for i in range(len(codes))
        }
    return result


def purpose_of(factor_id: str) -> str:
    if factor_id.startswith("risk_"):
        return "risk"
    return "alpha"


def list_factors(*, purpose: str | None = None) -> list[dict[str, Any]]:
    rows = []
    for fid in ALPHA_FACTOR_IDS:
        rows.append({
            "factor_id": fid,
            "purpose": "alpha",
            "sign_expectation": ALPHA_SIGN_EXPECTATION.get(fid),
        })
    for fid in RISK_FACTOR_IDS:
        rows.append({"factor_id": fid, "purpose": "risk", "sign_expectation": None})
    if purpose:
        rows = [r for r in rows if r["purpose"] == purpose]
    return rows
