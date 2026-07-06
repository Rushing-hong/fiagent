"""A-share event-driven backtesting engine.

Design goals:
- ~500 lines, zero new dependencies (pandas + numpy only)
- Enforces Chinese A-share rules: T+1, price limits, stamp duty, minimum lots
- Two input modes: built-in strategy names OR user-supplied signal DataFrame
- JSON output for AI agent consumption
- Integrates with existing get_market_data output format

Architecture:
  BacktestConfig  →  strategy params + fees + limits
  DataFeed        →  wraps {code: OHLCV DataFrame}
  Broker          →  cash, positions, order queue, T+1 lock
  Engine.run()    →  event loop over dates → Metrics

Signal convention:
  Weight in [-1.0, 1.0] where 1.0 = full long, 0.5 = half, 0 = flat.
  For A-shares, short signals (<0) are ignored (no short selling).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

# ============================================================================
# Configuration
# ============================================================================

# Board → price limit (fraction, e.g. 0.10 = ±10%)
_PRICE_LIMITS: dict[str, float] = {
    "main":      0.10,   # 主板 60xxxx, 00xxxx
    "star":      0.20,   # 科创板 688xxx
    "chinext":   0.20,   # 创业板 300xxx
    "bse":       0.30,   # 北交所 8xxxxx, 4xxxxx
    "st":        0.10,   # ST/*ST (2026.7.6 新规: 5%→10%)
    "default":   0.10,
}

# Boards with market makers → lower effective slippage (2026.4 创业板做市商)
_MARKET_MAKER_BOARDS = {"star", "chinext"}


def _detect_board(code: str) -> str:
    """Detect A-share board from stock code."""
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

    # Capital
    initial_cash: float = 1_000_000.0

    # Fees (fractions, not percentages)
    commission: float = 0.0003       # 佣金 0.03%
    stamp_duty: float = 0.0005       # 印花税 0.05% (sell only)
    transfer_fee: float = 0.00001    # 过户费 0.001% (both ways)
    min_commission: float = 5.0      # 最低佣金 5 元

    # Position
    position_pct: float = 1.0        # 单次开仓用多少仓位 (1.0=全仓)
    max_positions: int = 10          # 最大同时持仓品种数

    # Execution
    slippage: float = 0.001          # 滑点 0.1%
    lot_size: int = 100              # 最小交易单位 (A股100股)
    after_hours: bool = False        # 盘后固定价格交易 (2026.7.6 新规: 15:05-15:30)

    # Naming
    name: str = "backtest"


# ============================================================================
# Broker
# ============================================================================

@dataclass
class Order:
    code: str
    date: pd.Timestamp
    side: str         # "buy" | "sell"
    quantity: int
    price: float      # target price (used for limit check)


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


class Broker:
    """Manages cash, positions, orders, and T+1 lock."""

    def __init__(self, cfg: BacktestConfig):
        self.cfg = cfg
        self.cash = cfg.initial_cash
        self.positions: dict[str, int] = {}              # code → shares held
        self.avg_cost: dict[str, float] = {}             # code → average buy price
        self.tplus1_lock: dict[str, pd.Timestamp] = {}   # code → unlock date
        self.trades: list[Trade] = []
        self.open_trades: dict[str, Trade] = {}          # code → active trade
        self.equity: list[dict[str, Any]] = []

    def can_sell(self, code: str, date: pd.Timestamp) -> bool:
        """Check T+1: can only sell if bought on or before previous trading day."""
        lock_until = self.tplus1_lock.get(code)
        if lock_until is None:
            return False
        return date > lock_until

    def can_buy(self) -> bool:
        """Check if we have cash and position slots."""
        return self.cash > 0 and len(self.positions) < self.cfg.max_positions

    def _apply_price_limit(self, code: str, target_price: float, prev_close: float) -> float:
        """Clamp target price to daily limit."""
        limit = _limit_pct(code)
        max_p = prev_close * (1 + limit)
        min_p = prev_close * (1 - limit)
        return max(min_p, min(max_p, target_price))

    def _calc_commission(self, amount: float) -> float:
        return max(self.cfg.min_commission, amount * self.cfg.commission)

    def execute_order(self, order: Order, bar: dict[str, float], date: pd.Timestamp) -> str:
        """Execute a single order against the current bar. Returns status string."""
        code = order.code
        prev_close = bar.get("prev_close", bar["close"])
        trade_price = self._apply_price_limit(code, order.price or bar["close"], prev_close)
        # Market maker boards have narrower spreads → halve slippage
        slip = self.cfg.slippage * 0.5 if _detect_board(code) in _MARKET_MAKER_BOARDS else self.cfg.slippage
        trade_price *= (1 - slip) if order.side == "buy" else (1 + slip)

        # After-hours: use closing price for unfilled orders (2026.7.6 新规)
        if self.cfg.after_hours:
            trade_price = bar["close"]

        amount = trade_price * order.quantity

        if order.side == "buy":
            fee = self._calc_commission(amount) + amount * self.cfg.transfer_fee
            total = amount + fee
            if total > self.cash:
                return "insufficient_cash"
            if not self.can_buy():
                return "position_limit"
            self.cash -= total
            self.positions[code] = self.positions.get(code, 0) + order.quantity
            # Update average cost
            old_qty = self.positions[code] - order.quantity
            old_cost = self.avg_cost.get(code, 0) * old_qty
            self.avg_cost[code] = (old_cost + amount) / self.positions[code]
            # T+1 lock: can only sell next trading day
            self.tplus1_lock[code] = date
            # Track open trade
            if code not in self.open_trades:
                self.open_trades[code] = Trade(
                    code=code, entry_date=date, exit_date=None,
                    entry_price=trade_price, exit_price=None, quantity=order.quantity,
                )
            else:
                # Add to existing position
                t = self.open_trades[code]
                t.entry_price = (t.entry_price * t.quantity + trade_price * order.quantity) / (t.quantity + order.quantity)
                t.quantity += order.quantity
            return "filled"

        elif order.side == "sell":
            if not self.can_sell(code, date):
                return "tplus1_locked"
            held = self.positions.get(code, 0)
            if held <= 0:
                return "no_position"
            qty = min(order.quantity, held)
            fee = self._calc_commission(amount) + amount * (self.cfg.stamp_duty + self.cfg.transfer_fee)
            self.cash += amount - fee
            self.positions[code] -= qty
            if self.positions[code] <= 0:
                self.positions.pop(code, None)
                self.avg_cost.pop(code, None)
                self.tplus1_lock.pop(code, None)
                # Close trade
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
        """Total equity = cash + market value of positions."""
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


def _signal_rsi(df: pd.DataFrame, period: int = 14, oversold: float = 30, overbought: float = 70) -> pd.Series:
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

def compute_metrics(equity_df: pd.DataFrame, trades: list[Trade], cfg: BacktestConfig) -> dict[str, Any]:
    """Compute standard performance metrics."""
    if equity_df.empty or len(equity_df) < 2:
        return {"error": "not enough data for metrics"}

    eq = equity_df["equity"].values
    rets = pd.Series(eq).pct_change().dropna()

    total_return = (eq[-1] / eq[0] - 1) if eq[0] > 0 else 0
    n_days = len(equity_df)
    annual_return = (1 + total_return) ** (252 / max(n_days, 1)) - 1

    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0
    sortino = float(rets[rets < 0].std()) if (rets < 0).sum() > 0 else float('inf')
    sortino_ratio = float(rets.mean() / sortino * np.sqrt(252)) if sortino > 0 and sortino != float('inf') else 0

    rolling_max = pd.Series(eq).cummax()
    drawdowns = pd.Series(eq) / rolling_max - 1
    max_dd = float(drawdowns.min())

    calmar = annual_return / abs(max_dd) if max_dd != 0 else 0

    win_trades = [t for t in trades if t.pnl is not None and t.pnl > 0]
    loss_trades = [t for t in trades if t.pnl is not None and t.pnl <= 0]
    win_rate = len(win_trades) / len(trades) if trades else 0

    avg_win = float(sum(float(t.pnl or 0) for t in win_trades)) / len(win_trades) if win_trades else 0.0
    avg_loss = float(sum(float(t.pnl or 0) for t in loss_trades)) / len(loss_trades) if loss_trades else 0.0
    win_sum = float(sum(float(t.pnl or 0) for t in win_trades))
    loss_sum = float(sum(float(t.pnl or 0) for t in loss_trades))
    profit_factor = abs(win_sum / loss_sum) if loss_sum != 0 else float('inf')

    # Buy & hold comparison
    bh_return = 0
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
        "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else None,
        "final_equity": round(eq[-1], 2),
        "buy_hold_return": round(bh_return * 100, 2),
        "n_days": n_days,
    }


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
    ) -> dict[str, Any]:
        """Run backtest.

        Args:
            data: {code: OHLCV DataFrame} with DatetimeIndex.
            signal: User-supplied signal DataFrame (index=date, columns=codes, values=-1..1).
                    If None, uses built-in strategy.
            strategy: Built-in strategy name (if signal is None).
            strategy_params: Parameters for built-in strategy.

        Returns:
            Dict with keys: metrics, trades, equity_curve, config.
        """
        codes = list(data.keys())
        if not codes:
            return {"ok": False, "error": "no data provided"}

        # Validate and align dates
        all_dates = sorted(set().union(*[set(df.index) for df in data.values()]))
        if len(all_dates) < 2:
            return {"ok": False, "error": "not enough data points (need >= 2)"}

        # Generate signals if not provided
        if signal is None and strategy in _BUILTIN_STRATEGIES:
            params = strategy_params or {}
            sig_gen = _BUILTIN_STRATEGIES[strategy]
            signal = pd.DataFrame(index=pd.DatetimeIndex(all_dates))
            for code in codes:
                s = sig_gen(data[code], **params)
                signal[code] = s
            signal = signal.fillna(0.0)
        elif signal is None:
            return {"ok": False, "error": f"strategy '{strategy}' not found. Available: {list(_BUILTIN_STRATEGIES.keys())}"}

        broker = Broker(self.cfg)
        main_code = codes[0]
        benchmark_prices = []

        for i, date in enumerate(all_dates):
            # Build current bar snapshot
            bar: dict[str, dict[str, float]] = {}
            for code in codes:
                df = data[code]
                if date not in df.index:
                    continue
                row = df.loc[date]
                prev_close = df["close"].iloc[df.index.get_loc(date) - 1] if df.index.get_loc(date) > 0 else row["close"]
                bar[code] = {
                    "open": float(row["open"]), "high": float(row["high"]),
                    "low": float(row["low"]), "close": float(row["close"]),
                    "volume": float(row.get("volume", 0) or 0),
                    "prev_close": float(prev_close),
                }

            if not bar:
                continue

            # Get current signals
            current_sig: dict[str, float] = {}
            if signal is not None and date in signal.index:
                for code in codes:
                    if code in signal.columns:
                        current_sig[code] = float(signal.loc[date, code])
                    else:
                        current_sig[code] = 0.0

            # Rebalance: compute target positions from signals
            for code in codes:
                if code not in bar:
                    continue
                sig = current_sig.get(code, 0.0)
                sig = max(0.0, sig)  # A-share: no shorting
                price = bar[code]["close"]
                current_qty = broker.positions.get(code, 0)

                # Target quantity from signal weight
                total_equity = broker.current_equity({c: bar.get(c, {}).get("close", 0) for c in codes})
                target_value = total_equity * sig * self.cfg.position_pct
                target_qty = int(target_value / price / self.cfg.lot_size) * self.cfg.lot_size if price > 0 else 0

                delta = target_qty - current_qty
                if delta > 0:
                    broker.execute_order(Order(code, date, "buy", delta, price), bar[code], date)
                elif delta < 0:
                    broker.execute_order(Order(code, date, "sell", -delta, price), bar[code], date)

            # Record equity
            prices = {c: bar[c]["close"] for c in bar}
            broker.record_equity(date, prices)
            if main_code in prices:
                benchmark_prices.append({"date": str(date.date()), "price": prices[main_code]})

        # Compute results
        equity_df = pd.DataFrame(broker.equity)
        if benchmark_prices:
            bm_df = pd.DataFrame(benchmark_prices)
            bm_indexed: pd.DataFrame = bm_df.set_index("date")  # type: ignore[assignment]
            equity_df["benchmark_price"] = bm_indexed["price"].reindex(equity_df["date"]).values  # type: ignore[union-attr]

        metrics = compute_metrics(equity_df, broker.trades, self.cfg)

        # Sample equity curve (max 200 points)
        eq_sample = broker.equity[::max(1, len(broker.equity) // 200)] if broker.equity else []

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

        return {
            "ok": True,
            "config": {
                "initial_cash": self.cfg.initial_cash,
                "commission": self.cfg.commission,
                "stamp_duty": self.cfg.stamp_duty,
                "strategy": strategy or "custom_signal",
                "codes": codes,
                "date_range": f"{all_dates[0].date()} ~ {all_dates[-1].date()}",
                "n_trading_days": len(all_dates),
            },
            "metrics": metrics,
            "trades": trade_list[:100],   # Cap to 100 trades in output
            "equity_curve": eq_sample,
        }
