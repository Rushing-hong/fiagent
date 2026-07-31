"""Deterministic order execution simulator for Committee / research gates.

LLM must not assume close-price fills. This engine models:
- limit-up/down lock rejection
- T+1 sell lock
- partial fill via participation rate vs daily volume
- square-root impact slippage
- lot-size rounding (100 shares)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

_LIMIT_EPS = 1e-4
_PRICE_LIMITS: dict[str, float] = {
    "main": 0.10,
    "star": 0.20,
    "chinext": 0.20,
    "bse": 0.30,
    "st": 0.10,
    "default": 0.10,
}
_MARKET_MAKER_BOARDS = {"star", "chinext"}


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


def _is_limit_up(bar: dict[str, float], code: str) -> bool:
    prev = bar.get("prev_close") or 0.0
    if prev <= 0:
        return False
    up = prev * (1 + _limit_pct(code))
    px = bar.get("close") or 0.0
    return px >= up * (1 - _LIMIT_EPS)


def _is_limit_down(bar: dict[str, float], code: str) -> bool:
    prev = bar.get("prev_close") or 0.0
    if prev <= 0:
        return False
    down = prev * (1 - _limit_pct(code))
    px = bar.get("close") or 0.0
    return px <= down * (1 + _LIMIT_EPS)


def bar_from_ohlcv_row(row: dict[str, Any], *, prev_close: float | None = None) -> dict[str, float]:
    """Normalize a market-data row into execution bar dict."""
    close = float(row.get("close") or 0.0)
    prev = float(prev_close if prev_close is not None else row.get("prev_close") or close)
    volume = float(row.get("volume") or 0.0)
    amount = float(row.get("amount") or 0.0)
    adv = float(row.get("adv") or amount or (close * volume))
    return {
        "open": float(row.get("open") or close),
        "high": float(row.get("high") or close),
        "low": float(row.get("low") or close),
        "close": close,
        "prev_close": prev,
        "volume": volume,
        "amount": amount,
        "adv": adv,
    }


def weight_to_quantity(
    target_weight: float,
    nav: float,
    price: float,
    *,
    lot_size: int = 100,
) -> int:
    if price <= 0 or nav <= 0 or target_weight <= 0:
        return 0
    raw = int(nav * target_weight / price)
    return max(0, (raw // lot_size) * lot_size)


@dataclass
class ExecutionConfig:
    lot_size: int = 100
    slippage: float = 0.001
    use_impact_model: bool = True
    impact_coef: float = 0.001
    participation_rate: float = 0.10
    """Max fraction of bar volume fillable in one slice (partial fill)."""
    reject_limit_lock: bool = True
    commission: float = 0.0003
    min_commission: float = 5.0
    transfer_fee: float = 0.00001
    stamp_duty: float = 0.0005


@dataclass
class PortfolioSnapshot:
    nav: float = 1_000_000.0
    cash: float = 1_000_000.0
    positions: dict[str, int] = field(default_factory=dict)
    buy_dates: dict[str, str] = field(default_factory=dict)
    trade_date: str = ""


@dataclass
class OrderIntent:
    symbol: str
    side: str
    target_weight: float | None = None
    quantity: int | None = None
    exec_style: str = "next_open"


@dataclass
class FillReport:
    status: str
    symbol: str
    side: str
    requested_qty: int
    filled_qty: int
    avg_price: float
    reject_reason: str | None = None
    slippage_bps: float = 0.0
    participation_pct: float | None = None
    fees: float = 0.0
    exec_style: str = "next_open"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "symbol": self.symbol,
            "side": self.side,
            "requested_qty": self.requested_qty,
            "filled_qty": self.filled_qty,
            "avg_price": self.avg_price,
            "reject_reason": self.reject_reason,
            "slippage_bps": round(self.slippage_bps, 2),
            "participation_pct": self.participation_pct,
            "fees": round(self.fees, 2),
            "exec_style": self.exec_style,
            "notes": self.notes,
        }


class ExecutionEngine:
    """Simulate a single order against one bar — deterministic, no LLM."""

    def __init__(self, cfg: ExecutionConfig | None = None) -> None:
        self.cfg = cfg or ExecutionConfig()

    def _exec_price(self, intent: OrderIntent, bar: dict[str, float]) -> float:
        style = (intent.exec_style or "next_open").lower()
        if style == "close":
            return float(bar["close"])
        if style == "vwap":
            vol = float(bar.get("volume") or 0.0)
            amt = float(bar.get("amount") or 0.0)
            if vol > 0 and amt > 0:
                return amt / vol
            return (float(bar["open"]) + float(bar["close"])) / 2.0
        return float(bar.get("open") or bar["close"])

    def _calc_slip(self, code: str, amount: float, bar: dict[str, float]) -> float:
        board_mult = 0.5 if _detect_board(code) in _MARKET_MAKER_BOARDS else 1.0
        base = self.cfg.slippage * board_mult
        if not self.cfg.use_impact_model:
            return base
        adv = float(bar.get("adv") or 0.0)
        if adv <= 0 or amount <= 0:
            return base
        impact = self.cfg.impact_coef * math.sqrt(amount / adv)
        return max(base, impact)

    def _apply_limit_price(self, code: str, price: float, prev_close: float) -> float:
        limit = _limit_pct(code)
        return max(prev_close * (1 - limit), min(prev_close * (1 + limit), price))

    def _can_sell(self, symbol: str, portfolio: PortfolioSnapshot) -> bool:
        buy_d = portfolio.buy_dates.get(symbol)
        if not buy_d or not portfolio.trade_date:
            return bool(portfolio.positions.get(symbol, 0) > 0 and not buy_d)
        try:
            return date.fromisoformat(portfolio.trade_date) > date.fromisoformat(buy_d)
        except ValueError:
            return False

    def simulate(
        self,
        intent: OrderIntent,
        bar: dict[str, float],
        *,
        portfolio: PortfolioSnapshot | None = None,
    ) -> FillReport:
        pf = portfolio or PortfolioSnapshot()
        code = intent.symbol.upper()
        side = intent.side.lower()
        notes: list[str] = []

        if side not in {"buy", "sell"}:
            return FillReport(
                status="rejected", symbol=code, side=side, requested_qty=0,
                filled_qty=0, avg_price=0.0, reject_reason="invalid_side",
                exec_style=intent.exec_style,
            )
        if self.cfg.lot_size <= 0:
            return FillReport(
                status="rejected", symbol=code, side=side, requested_qty=0,
                filled_qty=0, avg_price=0.0, reject_reason="invalid_lot_size",
                exec_style=intent.exec_style,
            )
        if not 0 < self.cfg.participation_rate <= 1:
            return FillReport(
                status="rejected", symbol=code, side=side, requested_qty=0,
                filled_qty=0, avg_price=0.0, reject_reason="invalid_participation_rate",
                exec_style=intent.exec_style,
            )

        base_px = self._exec_price(intent, bar)
        prev_close = float(bar.get("prev_close") or bar["close"])
        ref_px = self._apply_limit_price(code, base_px, prev_close)
        if ref_px <= 0 or prev_close <= 0:
            return FillReport(
                status="rejected", symbol=code, side=side, requested_qty=0,
                filled_qty=0, avg_price=0.0, reject_reason="invalid_market_price",
                exec_style=intent.exec_style,
            )

        qty = intent.quantity
        if qty is None and intent.target_weight is not None:
            qty = weight_to_quantity(intent.target_weight, pf.nav, ref_px, lot_size=self.cfg.lot_size)
        qty = max(0, int(qty or 0))

        if qty <= 0:
            return FillReport(
                status="rejected",
                symbol=code,
                side=side,
                requested_qty=0,
                filled_qty=0,
                avg_price=0.0,
                reject_reason="zero_quantity",
                exec_style=intent.exec_style,
                notes=["目标仓位换算为 0 股（价格/权重/整手约束）"],
            )
        if qty % self.cfg.lot_size:
            return FillReport(
                status="rejected", symbol=code, side=side, requested_qty=qty,
                filled_qty=0, avg_price=0.0, reject_reason="invalid_lot_size",
                exec_style=intent.exec_style,
                notes=[f"A 股订单需为 {self.cfg.lot_size} 股整手"],
            )

        if self.cfg.reject_limit_lock:
            if side == "buy" and _is_limit_up(bar, code):
                return FillReport(
                    status="rejected",
                    symbol=code,
                    side=side,
                    requested_qty=qty,
                    filled_qty=0,
                    avg_price=0.0,
                    reject_reason="limit_up_locked",
                    exec_style=intent.exec_style,
                    notes=["涨停封板，无法买入"],
                )
            if side == "sell" and _is_limit_down(bar, code):
                return FillReport(
                    status="rejected",
                    symbol=code,
                    side=side,
                    requested_qty=qty,
                    filled_qty=0,
                    avg_price=0.0,
                    reject_reason="limit_down_locked",
                    exec_style=intent.exec_style,
                    notes=["跌停封板，无法卖出"],
                )

        vol = float(bar.get("volume") or 0.0)
        if vol <= 0:
            return FillReport(
                status="rejected",
                symbol=code,
                side=side,
                requested_qty=qty,
                filled_qty=0,
                avg_price=0.0,
                reject_reason="halted_or_no_volume",
                exec_style=intent.exec_style,
                notes=["成交量为 0，视为停牌/无量"],
            )

        if side == "sell":
            held = pf.positions.get(code, 0)
            if held <= 0:
                return FillReport(
                    status="rejected",
                    symbol=code,
                    side=side,
                    requested_qty=qty,
                    filled_qty=0,
                    avg_price=0.0,
                    reject_reason="no_position",
                    exec_style=intent.exec_style,
                )
            if not self._can_sell(code, pf):
                return FillReport(
                    status="rejected",
                    symbol=code,
                    side=side,
                    requested_qty=qty,
                    filled_qty=0,
                    avg_price=0.0,
                    reject_reason="tplus1_locked",
                    exec_style=intent.exec_style,
                    notes=["T+1：当日买入不可卖出"],
                )
            qty = min(qty, held)

        max_by_participation = int(vol * self.cfg.participation_rate)
        max_by_participation = (max_by_participation // self.cfg.lot_size) * self.cfg.lot_size
        if max_by_participation <= 0:
            return FillReport(
                status="rejected", symbol=code, side=side, requested_qty=qty,
                filled_qty=0, avg_price=0.0, reject_reason="participation_limit_below_lot",
                exec_style=intent.exec_style,
                notes=["参与率上限不足一手，无法形成有效成交"],
            )
        fill_qty = qty
        participation_pct: float | None = None
        if fill_qty > max_by_participation:
            fill_qty = max_by_participation
            participation_pct = round(fill_qty / vol * 100, 2) if vol else None
            notes.append(
                f"参与率上限 {self.cfg.participation_rate:.0%} → 部分成交 {fill_qty}/{qty}"
            )

        provisional = ref_px * fill_qty
        slip = self._calc_slip(code, provisional, bar)
        slip_bps = slip * 10_000
        trade_px = ref_px * (1 + slip) if side == "buy" else ref_px * (1 - slip)
        trade_px = self._apply_limit_price(code, trade_px, prev_close)

        amount = trade_px * fill_qty
        fee = max(self.cfg.min_commission, amount * self.cfg.commission)
        fee += amount * self.cfg.transfer_fee
        if side == "sell":
            fee += amount * self.cfg.stamp_duty

        if side == "buy" and amount + fee > pf.cash:
            affordable = int((pf.cash * 0.99) / (trade_px * (1 + self.cfg.commission + self.cfg.transfer_fee)))
            affordable = (affordable // self.cfg.lot_size) * self.cfg.lot_size
            if affordable <= 0:
                return FillReport(
                    status="rejected",
                    symbol=code,
                    side=side,
                    requested_qty=qty,
                    filled_qty=0,
                    avg_price=0.0,
                    reject_reason="insufficient_cash",
                    exec_style=intent.exec_style,
                )
            fill_qty = min(fill_qty, affordable)
            amount = trade_px * fill_qty
            fee = max(self.cfg.min_commission, amount * self.cfg.commission) + amount * self.cfg.transfer_fee
            notes.append(f"现金不足，缩减至 {fill_qty} 股")

        status = "filled" if fill_qty >= qty else ("partial" if fill_qty > 0 else "rejected")
        if fill_qty <= 0:
            return FillReport(
                status="rejected",
                symbol=code,
                side=side,
                requested_qty=qty,
                filled_qty=0,
                avg_price=0.0,
                reject_reason="cannot_fill",
                exec_style=intent.exec_style,
                notes=notes,
            )

        return FillReport(
            status=status,
            symbol=code,
            side=side,
            requested_qty=qty,
            filled_qty=fill_qty,
            avg_price=round(trade_px, 4),
            slippage_bps=slip_bps,
            participation_pct=participation_pct,
            fees=round(fee, 2),
            exec_style=intent.exec_style,
            notes=notes,
        )
