"""P2 helpers: futures hedge book, sleeve blend/attribution, industry caps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

# CFFEX equity-index futures contract multipliers (yuan per index point)
FUTURES_MULTIPLIER: dict[str, float] = {
    "IF": 300.0,
    "IH": 300.0,
    "IC": 200.0,
    "IM": 200.0,
}


def futures_multiplier(symbol: str) -> float:
    root = "".join(c for c in str(symbol).upper() if c.isalpha())[:2]
    return FUTURES_MULTIPLIER.get(root, 300.0)


def blend_sleeve_signals(
    codes: list[str],
    dates: list[pd.Timestamp],
    sleeves: dict[str, pd.DataFrame],
    weights: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Equal-or-custom weight blend of sleeve signal panels → one signal DataFrame."""
    if not sleeves:
        raise ValueError("sleeves empty")
    w = weights or {k: 1.0 for k in sleeves}
    # normalize positive weights
    w = {k: max(0.0, float(w.get(k, 0.0))) for k in sleeves}
    s = sum(w.values()) or 1.0
    w = {k: v / s for k, v in w.items()}

    out = pd.DataFrame(0.0, index=pd.DatetimeIndex(dates), columns=codes)
    for name, sdf in sleeves.items():
        wi = w.get(name, 0.0)
        if wi <= 0:
            continue
        for code in codes:
            if code not in sdf.columns:
                continue
            aligned = sdf[code].reindex(out.index).fillna(0.0).clip(lower=0.0)
            out[code] = out[code] + aligned * wi
    out = out.clip(upper=1.0)
    return out, w


def apply_industry_cap(
    target_weights: dict[str, float],
    industry_map: dict[str, str],
    max_industry_weight: float,
) -> dict[str, float]:
    """Scale down names in industries exceeding max_industry_weight (sum of weights)."""
    if max_industry_weight <= 0 or not industry_map:
        return target_weights
    # group
    by_ind: dict[str, list[str]] = {}
    for code, w in target_weights.items():
        ind = industry_map.get(code) or industry_map.get(code.split(".")[0]) or "UNKNOWN"
        by_ind.setdefault(ind, []).append(code)
    out = dict(target_weights)
    for ind, members in by_ind.items():
        total = sum(out.get(c, 0.0) for c in members)
        if total > max_industry_weight + 1e-12:
            scale = max_industry_weight / total
            for c in members:
                out[c] = out.get(c, 0.0) * scale
    return out


@dataclass
class FuturesHedgeBook:
    """Short index-futures hedge against stock book (daily MTM into cash)."""

    symbol: str
    multiplier: float
    hedge_ratio: float = 1.0
    margin_rate: float = 0.12
    commission: float = 0.000023
    roll_cost_bps: float = 2.0  # applied on month change (continuous series proxy)

    contracts: int = 0  # >0 means short hedge lots
    last_price: float | None = None
    last_month: int | None = None
    realized_pnl: float = 0.0
    commission_paid: float = 0.0
    roll_cost_paid: float = 0.0
    equity_series: list[dict[str, Any]] = field(default_factory=list)

    def step(
        self,
        date: pd.Timestamp,
        fut_bar: dict[str, float] | None,
        stock_mv: float,
        cash_ref: float,
    ) -> float:
        """Rebalance hedge & MTM. Returns cash delta (PnL - fees)."""
        if fut_bar is None:
            self.equity_series.append({
                "date": str(date.date()),
                "contracts": self.contracts,
                "fut_pnl": 0.0,
                "stock_mv": stock_mv,
            })
            return 0.0

        px = float(fut_bar.get("close") or 0.0)
        prev = float(fut_bar.get("prev_close") or px)
        if px <= 0:
            return 0.0

        cash_delta = 0.0
        # Daily MTM for short hedge: profit when futures fall
        if self.contracts != 0 and self.last_price is not None:
            # contracts>0 short: pnl = contracts * (last_price - px) * mult
            pnl = self.contracts * (self.last_price - px) * self.multiplier
            self.realized_pnl += pnl
            cash_delta += pnl

        # Target short contracts
        notional_per = px * self.multiplier
        target = 0
        if stock_mv > 0 and notional_per > 0 and self.hedge_ratio > 0:
            target = int(round(stock_mv * self.hedge_ratio / notional_per))
            target = max(0, target)

        # Roll cost proxy: month change while holding
        month = int(date.month)
        if (
            self.contracts > 0
            and self.last_month is not None
            and month != self.last_month
            and self.roll_cost_bps > 0
        ):
            roll = self.contracts * notional_per * (self.roll_cost_bps / 10000.0)
            self.roll_cost_paid += roll
            cash_delta -= roll

        delta_c = target - self.contracts
        if delta_c != 0:
            fee = abs(delta_c) * notional_per * self.commission
            self.commission_paid += fee
            cash_delta -= fee
            self.contracts = target

        self.last_price = px
        self.last_month = month
        margin = abs(self.contracts) * notional_per * self.margin_rate
        self.equity_series.append({
            "date": str(date.date()),
            "contracts": self.contracts,
            "fut_price": px,
            "margin": round(margin, 2),
            "stock_mv": round(stock_mv, 2),
            "cum_pnl": round(self.realized_pnl, 2),
        })
        return cash_delta

    def summary(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "multiplier": self.multiplier,
            "hedge_ratio": self.hedge_ratio,
            "final_contracts": self.contracts,
            "realized_pnl": round(self.realized_pnl, 2),
            "commission_paid": round(self.commission_paid, 2),
            "roll_cost_paid": round(self.roll_cost_paid, 2),
            "net_hedge_pnl": round(
                self.realized_pnl - self.commission_paid - self.roll_cost_paid, 2
            ),
        }


def sleeve_day_exposures(
    sleeves: dict[str, pd.DataFrame],
    weights: dict[str, float],
    codes: list[str],
    sig_date: pd.Timestamp | None,
) -> dict[str, float]:
    """Mean long signal per sleeve on sig_date (0..1)."""
    out: dict[str, float] = {}
    if sig_date is None:
        return {k: 0.0 for k in sleeves}
    for name, sdf in sleeves.items():
        if sig_date not in sdf.index:
            out[name] = 0.0
            continue
        vals = []
        for c in codes:
            if c in sdf.columns:
                vals.append(max(0.0, float(sdf.loc[sig_date, c])))
        out[name] = (sum(vals) / len(vals)) if vals else 0.0
    return out


def attribute_day_return(
    day_return: float,
    exposures: dict[str, float],
    sleeve_weights: dict[str, float],
    hedge_pnl: float,
    equity: float,
) -> dict[str, float]:
    """Split portfolio day return across sleeves by exposure×prior weight; hedge separate."""
    contrib = {k: 0.0 for k in exposures}
    eff = {k: sleeve_weights.get(k, 0.0) * exposures.get(k, 0.0) for k in exposures}
    s = sum(eff.values())
    if s > 1e-12 and equity != 0:
        for k, e in eff.items():
            contrib[k] = day_return * (e / s)
    hedge_ret = hedge_pnl / equity if equity else 0.0
    return {**contrib, "_hedge": hedge_ret}


def zscore(arr: list[float] | np.ndarray) -> np.ndarray:
    a = np.asarray(arr, dtype=float)
    if a.size == 0:
        return a
    if not np.any(np.isfinite(a)):
        return np.zeros_like(a)
    m = np.nanmean(a)
    s = np.nanstd(a)
    if not np.isfinite(s) or s < 1e-12:
        return np.zeros_like(a)
    return (a - m) / s


def momentum_factor(
    data: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    codes: list[str],
    window: int = 20,
) -> dict[str, float]:
    """Trailing return z-score as momentum style factor."""
    raw: list[float] = []
    for code in codes:
        df = data.get(code)
        if df is None or date not in df.index:
            raw.append(float("nan"))
            continue
        loc = df.index.get_loc(date)
        if isinstance(loc, slice) or int(loc) < window:
            raw.append(float("nan"))
            continue
        c0 = float(df["close"].iloc[int(loc) - window])
        c1 = float(df["close"].iloc[int(loc)])
        raw.append((c1 / c0 - 1.0) if c0 > 0 else float("nan"))
    zs = zscore(raw)
    return {codes[i]: float(zs[i]) if np.isfinite(zs[i]) else 0.0 for i in range(len(codes))}


def size_factor(
    data: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    codes: list[str],
    window: int = 20,
) -> dict[str, float]:
    """Liquidity/size proxy: log(ADV) z-score (higher = larger/more liquid)."""
    raw: list[float] = []
    for code in codes:
        df = data.get(code)
        if df is None or date not in df.index:
            raw.append(float("nan"))
            continue
        loc = df.index.get_loc(date)
        i = int(loc) if not isinstance(loc, slice) else -1
        if i < 0:
            raw.append(float("nan"))
            continue
        start = max(0, i - window + 1)
        sub = df.iloc[start : i + 1]
        if "close" in sub.columns and "volume" in sub.columns:
            amt = (sub["close"].astype(float) * sub["volume"].astype(float)).mean()
        else:
            amt = float("nan")
        raw.append(float(np.log(amt)) if amt and amt > 0 and np.isfinite(amt) else float("nan"))
    zs = zscore(raw)
    return {codes[i]: float(zs[i]) if np.isfinite(zs[i]) else 0.0 for i in range(len(codes))}


def vol_factor(
    data: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    codes: list[str],
    window: int = 20,
) -> dict[str, float]:
    """Trailing realized-vol z-score."""
    raw: list[float] = []
    for code in codes:
        df = data.get(code)
        if df is None or date not in df.index:
            raw.append(float("nan"))
            continue
        loc = df.index.get_loc(date)
        i = int(loc) if not isinstance(loc, slice) else -1
        if i < window:
            raw.append(float("nan"))
            continue
        closes = df["close"].astype(float).iloc[i - window : i + 1]
        rets = closes.pct_change().dropna()
        if len(rets) < max(5, window // 2):
            raw.append(float("nan"))
            continue
        raw.append(float(rets.std() * np.sqrt(252)))
    zs = zscore(raw)
    return {codes[i]: float(zs[i]) if np.isfinite(zs[i]) else 0.0 for i in range(len(codes))}


def apply_style_exposure_cap(
    weights: dict[str, float],
    factor: dict[str, float],
    max_abs_exposure: float,
) -> dict[str, float]:
    """If |w·f| exceeds cap, shrink weights on the side that drives exposure."""
    if max_abs_exposure <= 0 or not weights:
        return weights
    codes = list(weights.keys())
    w = np.array([weights[c] for c in codes], dtype=float)
    f = np.array([factor.get(c, 0.0) for c in codes], dtype=float)
    expo = float(w @ f)
    if abs(expo) <= max_abs_exposure:
        return weights
    sign = 1.0 if expo > 0 else -1.0
    mask = (f * sign) > 0
    if not mask.any():
        return weights
    excess = abs(expo) - max_abs_exposure
    contrib = float((w[mask] * f[mask]).sum())
    if abs(contrib) < 1e-12:
        return weights
    scale = max(0.0, 1.0 - excess / abs(contrib))
    out = dict(weights)
    for i, c in enumerate(codes):
        if mask[i]:
            out[c] = float(w[i] * scale)
    return out
