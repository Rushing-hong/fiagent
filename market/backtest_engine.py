"""A-share event-driven backtesting engine.

Design goals:
- ~600 lines, zero new dependencies (pandas + numpy only)
- Enforces Chinese A-share rules: T+1, price-limit lock, stamp duty, lot size
- Two input modes: built-in strategy names OR user-supplied signal DataFrame
- JSON output for AI agent consumption

Architecture:
  BacktestConfig  →  strategy params + fees + limits + impact
  Broker          →  cash, positions, T+1 lock, limit-lock reject, √ impact
  Engine.run()    →  event loop (optional signal lag + open/close exec)

Signal convention:
  Weight in [-1.0, 1.0] where 1.0 = full long, 0.5 = half, 0 = flat.
  For A-shares, short signals (<0) are ignored (no short selling).

P0 realism (vs naive fill-at-close):
  - signal_lag=1: day-T signal executes on T+1 (default)
  - reject_limit_lock: 涨停无法买入 / 跌停无法卖出
  - impact model: slip = max(fixed, coef * sqrt(trade_amt / ADV))
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from market.backtest_p2 import (
    FuturesHedgeBook,
    apply_industry_cap,
    apply_style_exposure_cap,
    attribute_day_return,
    blend_sleeve_signals,
    futures_multiplier,
    momentum_factor,
    size_factor,
    sleeve_day_exposures,
    vol_factor,
)

# ============================================================================
# Configuration
# ============================================================================

_PRICE_LIMITS: dict[str, float] = {
    "main": 0.10,
    "star": 0.20,
    "chinext": 0.20,
    "bse": 0.30,
    "st": 0.10,
    "default": 0.10,
}

_MARKET_MAKER_BOARDS = {"star", "chinext"}
_LIMIT_EPS = 1e-4  # relative tolerance for limit-lock detection


def _detect_board(code: str) -> str:
    code = str(code).strip().upper()
    bare = code.split(".")[0]
    first = bare[0] if bare else ""
    if first == "6" and bare.startswith("688"):
        return "star"
    if first == "3":
        return "chinext"
    if first in ("8", "4"):
        return "bse"
    return "main"


def _limit_pct(code: str) -> float:
    return _PRICE_LIMITS.get(_detect_board(code), _PRICE_LIMITS["default"])


@dataclass
class BacktestConfig:
    """All tunable backtest parameters."""

    initial_cash: float = 1_000_000.0

    commission: float = 0.0003
    stamp_duty: float = 0.0005
    transfer_fee: float = 0.00001
    min_commission: float = 5.0

    position_pct: float = 1.0
    max_positions: int = 10

    slippage: float = 0.001
    lot_size: int = 100
    after_hours: bool = False

    # --- P0 realism ---
    signal_lag: int = 1
    """Day-T signal executes on the trading day that is `signal_lag` days later.
    1 = standard A-share (signal after close → next open). 0 = same-bar (look-ahead risky)."""

    exec_price: str = "open"
    """Execution reference: 'open' | 'close'. With signal_lag>=1, 'open' is preferred."""

    reject_limit_lock: bool = True
    """If True, cannot buy when locked limit-up; cannot sell when locked limit-down."""

    use_impact_model: bool = True
    """If True, slippage = max(fixed slippage, impact_coef * sqrt(trade_amt / ADV))."""

    impact_coef: float = 0.001
    """Coefficient for square-root impact (single-side)."""

    adv_window: int = 20
    """Trailing window (trading days) for average daily yuan volume."""

    skip_halted: bool = True
    """Missing bar or volume==0 → treat as halted: cannot trade; mark-to-market with last price."""

    cash_annual_rate: float = 0.0
    """Idle-cash overnight yield (e.g. 0.015 ≈ GC001). Applied as cash * rate/365 per trading day."""

    # --- P2: hedge / industry ---
    hedge_enabled: bool = False
    hedge_symbol: str = "IF"
    hedge_ratio: float = 1.0
    """Short futures notional / stock MV. 1.0 ≈ fully hedged."""

    futures_margin_rate: float = 0.12
    futures_commission: float = 0.000023
    futures_roll_cost_bps: float = 2.0

    max_industry_weight: float | None = None
    """Cap sum of target weights per industry (e.g. 0.30). Needs industry_map in run()."""

    max_momentum_exposure: float | None = None
    """Barra-lite: cap |w·momentum_z| (e.g. 0.30)."""

    max_size_exposure: float | None = None
    """Barra-lite: cap |w·size_z| where size≈log(ADV)."""

    max_vol_exposure: float | None = None
    """Barra-lite: cap |w·vol_z| (trailing realized vol)."""

    momentum_window: int = 20
    style_window: int = 20

    name: str = "backtest"


# ============================================================================
# Broker
# ============================================================================

@dataclass
class Order:
    code: str
    date: pd.Timestamp
    side: str
    quantity: int
    price: float


@dataclass
class Trade:
    code: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp | None
    entry_price: float
    exit_price: float | None
    quantity: int
    pnl: float | None = None
    pnl_pct: float | None = None


def _is_limit_up(bar: dict[str, float], code: str) -> bool:
    prev = bar.get("prev_close") or 0.0
    if prev <= 0:
        return False
    limit = _limit_pct(code)
    up = prev * (1 + limit)
    # locked if close (and preferably high) near limit
    px = bar.get("close") or 0.0
    return px >= up * (1 - _LIMIT_EPS)


def _is_limit_down(bar: dict[str, float], code: str) -> bool:
    prev = bar.get("prev_close") or 0.0
    if prev <= 0:
        return False
    limit = _limit_pct(code)
    down = prev * (1 - limit)
    px = bar.get("close") or 0.0
    return px <= down * (1 + _LIMIT_EPS)


class Broker:
    """Manages cash, positions, orders, T+1 lock, and fill realism."""

    def __init__(self, cfg: BacktestConfig):
        self.cfg = cfg
        self.cash = cfg.initial_cash
        self.positions: dict[str, int] = {}
        self.avg_cost: dict[str, float] = {}
        self.tplus1_lock: dict[str, pd.Timestamp] = {}
        self.trades: list[Trade] = []
        self.open_trades: dict[str, Trade] = {}
        self.equity: list[dict[str, Any]] = []
        self.reject_log: list[dict[str, Any]] = []

    def can_sell(self, code: str, date: pd.Timestamp) -> bool:
        lock_until = self.tplus1_lock.get(code)
        if lock_until is None:
            return False
        return date > lock_until

    def can_buy(self) -> bool:
        return self.cash > 0 and len(self.positions) < self.cfg.max_positions

    def _apply_price_limit(self, code: str, target_price: float, prev_close: float) -> float:
        limit = _limit_pct(code)
        max_p = prev_close * (1 + limit)
        min_p = prev_close * (1 - limit)
        return max(min_p, min(max_p, target_price))

    def _calc_commission(self, amount: float) -> float:
        return max(self.cfg.min_commission, amount * self.cfg.commission)

    def _calc_slip(self, code: str, side: str, amount: float, bar: dict[str, float]) -> float:
        board_mult = 0.5 if _detect_board(code) in _MARKET_MAKER_BOARDS else 1.0
        base = self.cfg.slippage * board_mult
        if not self.cfg.use_impact_model:
            return base
        adv = float(bar.get("adv") or 0.0)
        if adv <= 0 or amount <= 0:
            return base
        impact = self.cfg.impact_coef * math.sqrt(amount / adv)
        return max(base, impact)

    def execute_order(self, order: Order, bar: dict[str, float], date: pd.Timestamp) -> str:
        code = order.code

        # Limit lock: cannot trade through locked boards
        if self.cfg.reject_limit_lock:
            if order.side == "buy" and _is_limit_up(bar, code):
                self.reject_log.append({
                    "date": str(date.date()), "code": code, "side": "buy",
                    "reason": "limit_up_locked", "qty": order.quantity,
                })
                return "limit_up_locked"
            if order.side == "sell" and _is_limit_down(bar, code):
                self.reject_log.append({
                    "date": str(date.date()), "code": code, "side": "sell",
                    "reason": "limit_down_locked", "qty": order.quantity,
                })
                return "limit_down_locked"

        prev_close = bar.get("prev_close", bar["close"])
        trade_price = self._apply_price_limit(code, order.price or bar["close"], prev_close)

        # Provisional amount for impact (pre-slip)
        provisional = trade_price * order.quantity
        slip = self._calc_slip(code, order.side, provisional, bar)
        trade_price *= (1 + slip) if order.side == "buy" else (1 - slip)

        if self.cfg.after_hours:
            trade_price = bar["close"]

        amount = trade_price * order.quantity

        if order.side == "buy":
            fee = self._calc_commission(amount) + amount * self.cfg.transfer_fee
            total = amount + fee
            if total > self.cash:
                self.reject_log.append({
                    "date": str(date.date()), "code": code, "side": "buy",
                    "reason": "insufficient_cash", "qty": order.quantity,
                })
                return "insufficient_cash"
            if not self.can_buy():
                self.reject_log.append({
                    "date": str(date.date()), "code": code, "side": "buy",
                    "reason": "position_limit", "qty": order.quantity,
                })
                return "position_limit"
            self.cash -= total
            self.positions[code] = self.positions.get(code, 0) + order.quantity
            old_qty = self.positions[code] - order.quantity
            old_cost = self.avg_cost.get(code, 0) * old_qty
            self.avg_cost[code] = (old_cost + amount) / self.positions[code]
            self.tplus1_lock[code] = date
            if code not in self.open_trades:
                self.open_trades[code] = Trade(
                    code=code, entry_date=date, exit_date=None,
                    entry_price=trade_price, exit_price=None, quantity=order.quantity,
                )
            else:
                t = self.open_trades[code]
                t.entry_price = (
                    (t.entry_price * t.quantity + trade_price * order.quantity)
                    / (t.quantity + order.quantity)
                )
                t.quantity += order.quantity
            return "filled"

        if order.side == "sell":
            if not self.can_sell(code, date):
                self.reject_log.append({
                    "date": str(date.date()), "code": code, "side": "sell",
                    "reason": "tplus1_locked", "qty": order.quantity,
                })
                return "tplus1_locked"
            held = self.positions.get(code, 0)
            if held <= 0:
                return "no_position"
            qty = min(order.quantity, held)
            amount = trade_price * qty
            fee = self._calc_commission(amount) + amount * (
                self.cfg.stamp_duty + self.cfg.transfer_fee
            )
            self.cash += amount - fee
            self.positions[code] -= qty
            if self.positions[code] <= 0:
                self.positions.pop(code, None)
                self.avg_cost.pop(code, None)
                self.tplus1_lock.pop(code, None)
                if code in self.open_trades:
                    t = self.open_trades.pop(code)
                    t.exit_date = date
                    t.exit_price = trade_price
                    cost = t.entry_price * t.quantity
                    t.pnl = (trade_price - t.entry_price) * t.quantity - fee
                    t.pnl_pct = t.pnl / cost if cost else 0
                    self.trades.append(t)
            return "filled"

        return "unknown_side"

    def current_equity(self, prices: dict[str, float]) -> float:
        mv = sum(self.positions.get(c, 0) * prices.get(c, 0) for c in self.positions)
        return self.cash + mv

    def record_equity(self, date: pd.Timestamp, prices: dict[str, float]) -> None:
        self.equity.append({
            "date": str(date.date()),
            "equity": round(self.current_equity(prices), 2),
            "cash": round(self.cash, 2),
        })


# ============================================================================
# Built-in signal generators
# ============================================================================

def _signal_ma_cross(df: pd.DataFrame, fast: int = 5, slow: int = 20) -> pd.Series:
    """MA crossover: fast MA > slow MA → long."""
    fast_ma = df["close"].rolling(fast).mean()
    slow_ma = df["close"].rolling(slow).mean()
    signal = pd.Series(0.0, index=df.index)
    signal[fast_ma > slow_ma] = 1.0
    signal[fast_ma.isna() | slow_ma.isna()] = 0.0
    return signal


def _signal_rsi(
    df: pd.DataFrame, period: int = 14, oversold: float = 30, overbought: float = 70
) -> pd.Series:
    """RSI mean reversion: oversold → long, overbought → flat."""
    delta: pd.Series = df["close"].diff()  # type: ignore[assignment]
    gain: pd.Series = delta.clip(lower=0)  # type: ignore[assignment]
    loss: pd.Series = (-delta).clip(lower=0)  # type: ignore[assignment]
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)  # type: ignore[union-attr]
    rsi: pd.Series = 100 - (100 / (1 + rs))  # type: ignore[assignment]
    signal = pd.Series(0.0, index=df.index)
    signal[rsi < oversold] = 1.0
    signal[(rsi > overbought) & (signal.shift(1) == 1.0)] = 0.0
    signal[rsi.isna()] = 0.0  # type: ignore[union-attr]
    return signal


def _signal_buy_hold(df: pd.DataFrame) -> pd.Series:
    """Buy at first bar, hold forever."""
    signal = pd.Series(0.0, index=df.index)
    if len(signal) > 0:
        signal.iloc[0] = 1.0
    return signal


def _signal_momentum(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Momentum: long when close > N-day high, exit when close < N-day low."""
    high_n = df["close"].rolling(window).max()
    low_n = df["close"].rolling(window).min()
    signal = pd.Series(0.0, index=df.index)
    signal[df["close"] >= high_n.shift(1)] = 1.0
    signal[df["close"] <= low_n.shift(1)] = 0.0
    signal.iloc[:window] = 0.0
    return signal


_BUILTIN_STRATEGIES = {
    "ma_cross": _signal_ma_cross,
    "rsi": _signal_rsi,
    "buy_hold": _signal_buy_hold,
    "momentum": _signal_momentum,
}


# ============================================================================
# Metrics
# ============================================================================

def compute_metrics(
    equity_df: pd.DataFrame, trades: list[Trade], cfg: BacktestConfig
) -> dict[str, Any]:
    if equity_df.empty or len(equity_df) < 2:
        return {"error": "not enough data for metrics"}

    eq = equity_df["equity"].values
    rets = pd.Series(eq).pct_change().dropna()

    total_return = (eq[-1] / eq[0] - 1) if eq[0] > 0 else 0
    n_days = len(equity_df)
    annual_return = (1 + total_return) ** (252 / max(n_days, 1)) - 1

    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0
    sortino = float(rets[rets < 0].std()) if (rets < 0).sum() > 0 else float("inf")
    sortino_ratio = (
        float(rets.mean() / sortino * np.sqrt(252))
        if sortino > 0 and sortino != float("inf")
        else 0
    )

    rolling_max = pd.Series(eq).cummax()
    drawdowns = pd.Series(eq) / rolling_max - 1
    max_dd = float(drawdowns.min())
    calmar = annual_return / abs(max_dd) if max_dd != 0 else 0

    win_trades = [t for t in trades if t.pnl is not None and t.pnl > 0]
    loss_trades = [t for t in trades if t.pnl is not None and t.pnl <= 0]
    win_rate = len(win_trades) / len(trades) if trades else 0

    avg_win = (
        float(sum(float(t.pnl or 0) for t in win_trades)) / len(win_trades)
        if win_trades
        else 0.0
    )
    avg_loss = (
        float(sum(float(t.pnl or 0) for t in loss_trades)) / len(loss_trades)
        if loss_trades
        else 0.0
    )
    win_sum = float(sum(float(t.pnl or 0) for t in win_trades))
    loss_sum = float(sum(float(t.pnl or 0) for t in loss_trades))
    profit_factor = abs(win_sum / loss_sum) if loss_sum != 0 else float("inf")

    bh_return = 0.0
    if "benchmark_price" in equity_df.columns:
        bh_col = equity_df["benchmark_price"]
        if len(bh_col) > 1 and bh_col.iloc[0] > 0:
            bh_return = float(bh_col.iloc[-1] / bh_col.iloc[0] - 1)

    return {
        "total_return": round(total_return * 100, 2),
        "annual_return": round(annual_return * 100, 2),
        "sharpe_ratio": round(sharpe, 3),
        "sortino_ratio": round(sortino_ratio, 3),
        "max_drawdown": round(max_dd * 100, 2),
        "calmar_ratio": round(calmar, 3),
        "total_trades": len(trades),
        "win_rate": round(win_rate * 100, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
        "final_equity": round(eq[-1], 2),
        "buy_hold_return": round(bh_return * 100, 2),
        "n_days": n_days,
    }


def _bar_amount(row: pd.Series) -> float:
    if "amount" in row.index and pd.notna(row.get("amount")):
        try:
            return float(row["amount"])
        except (TypeError, ValueError):
            pass
    close = float(row.get("close") or 0)
    vol = float(row.get("volume") or 0)
    return close * vol


def _prepare_adv(data: dict[str, pd.DataFrame], window: int) -> dict[str, pd.Series]:
    """Trailing mean of daily yuan volume for impact model."""
    out: dict[str, pd.Series] = {}
    for code, df in data.items():
        amt = df.apply(_bar_amount, axis=1)
        out[code] = amt.rolling(window, min_periods=max(1, window // 2)).mean()
    return out


# ============================================================================
# Engine
# ============================================================================

class BacktestEngine:
    """Event-driven backtesting loop for A-share markets."""

    def __init__(self, cfg: BacktestConfig | None = None):
        self.cfg = cfg or BacktestConfig()

    def run(
        self,
        data: dict[str, pd.DataFrame],
        signal: pd.DataFrame | None = None,
        strategy: str = "",
        strategy_params: dict[str, Any] | None = None,
        futures_data: pd.DataFrame | None = None,
        sleeves: dict[str, pd.DataFrame] | None = None,
        sleeve_weights: dict[str, float] | None = None,
        industry_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        codes = list(data.keys())
        if not codes:
            return {"ok": False, "error": "no data provided"}

        raw_dates = set().union(*[set(df.index) for df in data.values()])
        if len(raw_dates) < 2:
            return {"ok": False, "error": "not enough data points (need >= 2)"}
        d_min, d_max = min(raw_dates), max(raw_dates)
        sample = list(raw_dates)[:80]
        has_intraday = any(
            (getattr(ts, "hour", 0) or 0) != 0 or (getattr(ts, "minute", 0) or 0) != 0
            for ts in sample
        )
        if self.cfg.skip_halted and not has_intraday:
            try:
                from market.trade_calendar import trading_days_index
                cal = trading_days_index(
                    d_min.strftime("%Y-%m-%d"),
                    d_max.strftime("%Y-%m-%d"),
                )
                # keep timestamps that fall on exchange sessions; preserve time=00:00
                all_dates = list(cal) if len(cal) >= 2 else list(pd.bdate_range(d_min, d_max))
            except Exception:
                all_dates = list(pd.bdate_range(d_min, d_max))
        else:
            all_dates = sorted(raw_dates)
        if len(all_dates) < 2:
            return {"ok": False, "error": "not enough data points (need >= 2)"}

        sleeve_w: dict[str, float] = {}
        if sleeves:
            signal, sleeve_w = blend_sleeve_signals(
                codes, all_dates, sleeves, sleeve_weights
            )
        elif signal is None and strategy in _BUILTIN_STRATEGIES:
            params = strategy_params or {}
            sig_gen = _BUILTIN_STRATEGIES[strategy]
            signal = pd.DataFrame(index=pd.DatetimeIndex(all_dates))
            for code in codes:
                s = sig_gen(data[code], **params)
                signal[code] = s
            signal = signal.fillna(0.0)
        elif signal is None:
            return {
                "ok": False,
                "error": (
                    f"strategy '{strategy}' not found. "
                    f"Available: {list(_BUILTIN_STRATEGIES.keys())}"
                ),
            }

        lag = max(0, int(self.cfg.signal_lag))
        exec_field = self.cfg.exec_price if self.cfg.exec_price in ("open", "close") else "close"
        adv_map = _prepare_adv(data, self.cfg.adv_window)
        ind_map = industry_map or {}

        broker = Broker(self.cfg)
        main_code = codes[0]
        benchmark_prices: list[dict[str, Any]] = []
        fill_stats: dict[str, int] = {}
        last_prices: dict[str, float] = {}
        halt_days = 0
        cash_interest_total = 0.0
        daily_cash_rate = float(self.cfg.cash_annual_rate) / 365.0
        prev_equity: float | None = None
        attrib_cum: dict[str, float] = {}
        hedge_book: FuturesHedgeBook | None = None
        if self.cfg.hedge_enabled:
            hedge_book = FuturesHedgeBook(
                symbol=self.cfg.hedge_symbol,
                multiplier=futures_multiplier(self.cfg.hedge_symbol),
                hedge_ratio=float(self.cfg.hedge_ratio),
                margin_rate=float(self.cfg.futures_margin_rate),
                commission=float(self.cfg.futures_commission),
                roll_cost_bps=float(self.cfg.futures_roll_cost_bps),
            )

        for i, date in enumerate(all_dates):
            bar: dict[str, dict[str, float]] = {}
            halted: set[str] = set()
            for code in codes:
                df = data[code]
                if date not in df.index:
                    if self.cfg.skip_halted:
                        halted.add(code)
                    continue
                row = df.loc[date]
                loc = df.index.get_loc(date)
                if isinstance(loc, slice):
                    halted.add(code)
                    continue
                vol = float(row.get("volume", 0) or 0)
                if self.cfg.skip_halted and vol <= 0:
                    halted.add(code)
                    if pd.notna(row.get("close")):
                        last_prices[code] = float(row["close"])
                    continue
                prev_close = (
                    float(df["close"].iloc[loc - 1]) if int(loc) > 0 else float(row["close"])
                )
                adv_val = 0.0
                if code in adv_map and date in adv_map[code].index:
                    v = adv_map[code].loc[date]
                    adv_val = float(v) if pd.notna(v) else 0.0
                bar[code] = {
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": vol,
                    "prev_close": prev_close,
                    "adv": adv_val,
                }
                last_prices[code] = float(row["close"])

            if halted:
                halt_days += len(halted)

            sig_date = all_dates[i - lag] if i >= lag else None
            current_sig: dict[str, float] = {c: 0.0 for c in codes}
            if sig_date is not None and signal is not None and sig_date in signal.index:
                for code in codes:
                    if code in signal.columns:
                        current_sig[code] = float(signal.loc[sig_date, code])

            raw_w = {
                c: max(0.0, current_sig.get(c, 0.0)) * self.cfg.position_pct for c in codes
            }
            if self.cfg.max_industry_weight is not None and ind_map:
                raw_w = apply_industry_cap(
                    raw_w, ind_map, float(self.cfg.max_industry_weight)
                )
            if self.cfg.max_momentum_exposure is not None:
                mom = momentum_factor(
                    data, date, codes, window=int(self.cfg.momentum_window)
                )
                raw_w = apply_style_exposure_cap(
                    raw_w, mom, float(self.cfg.max_momentum_exposure)
                )
            sw = int(self.cfg.style_window)
            if self.cfg.max_size_exposure is not None:
                sz = size_factor(data, date, codes, window=sw)
                raw_w = apply_style_exposure_cap(
                    raw_w, sz, float(self.cfg.max_size_exposure)
                )
            if self.cfg.max_vol_exposure is not None:
                vo = vol_factor(data, date, codes, window=sw)
                raw_w = apply_style_exposure_cap(
                    raw_w, vo, float(self.cfg.max_vol_exposure)
                )

            mtm = {c: last_prices.get(c, 0.0) for c in codes}
            for c in bar:
                mtm[c] = bar[c]["close"]
            total_equity = broker.current_equity(mtm)

            for code in codes:
                if code in halted or code not in bar:
                    if code in halted:
                        fill_stats["halted"] = fill_stats.get("halted", 0) + 1
                    continue
                sig_w = raw_w.get(code, 0.0)
                price = bar[code][exec_field]
                if price <= 0:
                    price = bar[code]["close"]
                current_qty = broker.positions.get(code, 0)
                target_value = total_equity * sig_w
                target_qty = (
                    int(target_value / price / self.cfg.lot_size) * self.cfg.lot_size
                    if price > 0
                    else 0
                )
                delta = target_qty - current_qty
                if delta == 0:
                    continue
                side = "buy" if delta > 0 else "sell"
                status = broker.execute_order(
                    Order(code, date, side, abs(delta), price), bar[code], date
                )
                fill_stats[status] = fill_stats.get(status, 0) + 1

            mtm = {c: last_prices.get(c, 0.0) for c in codes}
            for c in bar:
                mtm[c] = bar[c]["close"]
            stock_mv = sum(
                broker.positions.get(c, 0) * mtm.get(c, 0.0) for c in broker.positions
            )

            hedge_pnl = 0.0
            if hedge_book is not None:
                fut_bar = None
                if futures_data is not None and date in futures_data.index:
                    fr = futures_data.loc[date]
                    loc = futures_data.index.get_loc(date)
                    prev_f = (
                        float(futures_data["close"].iloc[int(loc) - 1])
                        if isinstance(loc, (int, np.integer)) and int(loc) > 0
                        else float(fr["close"])
                    )
                    fut_bar = {"close": float(fr["close"]), "prev_close": prev_f}
                hedge_pnl = hedge_book.step(date, fut_bar, stock_mv, broker.cash)
                broker.cash += hedge_pnl

            if daily_cash_rate > 0 and broker.cash > 0:
                interest = broker.cash * daily_cash_rate
                broker.cash += interest
                cash_interest_total += interest

            prices = {c: last_prices.get(c, 0.0) for c in codes}
            for c in bar:
                prices[c] = bar[c]["close"]
            broker.record_equity(date, prices)
            eq_now = broker.current_equity(prices)

            if prev_equity is not None and prev_equity > 0 and sleeves:
                day_ret = eq_now / prev_equity - 1.0
                expo = sleeve_day_exposures(sleeves, sleeve_w, codes, sig_date)
                day_attr = attribute_day_return(
                    day_ret, expo, sleeve_w, hedge_pnl, prev_equity
                )
                for k, v in day_attr.items():
                    attrib_cum[k] = attrib_cum.get(k, 0.0) + v
            prev_equity = eq_now

            if main_code in prices and prices[main_code] > 0:
                benchmark_prices.append(
                    {"date": str(date.date()), "price": prices[main_code]}
                )

        equity_df = pd.DataFrame(broker.equity)
        if benchmark_prices:
            bm_df = pd.DataFrame(benchmark_prices)
            bm_indexed: pd.DataFrame = bm_df.set_index("date")  # type: ignore[assignment]
            equity_df["benchmark_price"] = (
                bm_indexed["price"].reindex(equity_df["date"]).values  # type: ignore[union-attr]
            )

        metrics = compute_metrics(equity_df, broker.trades, self.cfg)
        metrics["fill_stats"] = fill_stats
        metrics["rejects"] = len(broker.reject_log)
        metrics["reject_sample"] = broker.reject_log[:20]
        metrics["halt_code_days"] = halt_days
        metrics["cash_interest_total"] = round(cash_interest_total, 2)
        if hedge_book is not None:
            metrics["hedge"] = hedge_book.summary()
        if attrib_cum:
            metrics["sleeve_attribution_cum_return"] = {
                k: round(v * 100, 4) for k, v in attrib_cum.items()
            }
            metrics["sleeve_weights"] = sleeve_w

        eq_sample = (
            broker.equity[:: max(1, len(broker.equity) // 200)] if broker.equity else []
        )

        trade_list = []
        for t in broker.trades:
            trade_list.append({
                "code": t.code,
                "entry_date": str(t.entry_date.date()),
                "exit_date": str(t.exit_date.date()) if t.exit_date else None,
                "entry_price": round(t.entry_price, 2),
                "exit_price": round(t.exit_price, 2) if t.exit_price else None,
                "quantity": t.quantity,
                "pnl": round(t.pnl, 2) if t.pnl else None,
                "pnl_pct": round(t.pnl_pct * 100, 2) if t.pnl_pct else None,
            })

        hedge_curve = []
        if hedge_book and hedge_book.equity_series:
            step = max(1, len(hedge_book.equity_series) // 100)
            hedge_curve = hedge_book.equity_series[::step]

        return {
            "ok": True,
            "config": {
                "initial_cash": self.cfg.initial_cash,
                "commission": self.cfg.commission,
                "stamp_duty": self.cfg.stamp_duty,
                "strategy": strategy or ("sleeves" if sleeves else "custom_signal"),
                "codes": codes,
                "date_range": f"{all_dates[0].date()} ~ {all_dates[-1].date()}",
                "n_trading_days": len(all_dates),
                "signal_lag": lag,
                "exec_price": exec_field,
                "reject_limit_lock": self.cfg.reject_limit_lock,
                "use_impact_model": self.cfg.use_impact_model,
                "impact_coef": self.cfg.impact_coef,
                "skip_halted": self.cfg.skip_halted,
                "cash_annual_rate": self.cfg.cash_annual_rate,
                "hedge_enabled": self.cfg.hedge_enabled,
                "hedge_symbol": self.cfg.hedge_symbol if self.cfg.hedge_enabled else None,
                "hedge_ratio": self.cfg.hedge_ratio if self.cfg.hedge_enabled else None,
                "max_industry_weight": self.cfg.max_industry_weight,
                "max_momentum_exposure": self.cfg.max_momentum_exposure,
                "max_size_exposure": self.cfg.max_size_exposure,
                "max_vol_exposure": self.cfg.max_vol_exposure,
            },
            "metrics": metrics,
            "trades": trade_list[:100],
            "equity_curve": eq_sample,
            "hedge_curve": hedge_curve,
        }
