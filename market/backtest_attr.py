"""回测 Layer1 归因：薄版 + 加厚版。"""

from __future__ import annotations

from typing import Any


def thin_layer1_attribution(trades: list[Any]) -> dict[str, Any]:
    """Week2：按标的汇总已平仓盈亏。"""
    by_code: dict[str, float] = {}
    n_closed = 0
    for t in trades:
        pnl = getattr(t, "pnl", None)
        if pnl is None:
            continue
        n_closed += 1
        code = str(getattr(t, "code", ""))
        by_code[code] = by_code.get(code, 0.0) + float(pnl)
    ranked = sorted(by_code.items(), key=lambda x: x[1], reverse=True)
    total = float(sum(by_code.values()))
    winners = [{"code": c, "pnl": round(p, 2)} for c, p in ranked[:5]]
    losers = [{"code": c, "pnl": round(p, 2)} for c, p in ranked[-5:][::-1]] if ranked else []
    return {
        "layer": "1_thin",
        "total_pnl": round(total, 2),
        "top5_winners": winners,
        "top5_losers": losers,
        "n_closed_trades": n_closed,
        "n_names": len(by_code),
    }


def thick_layer1_attribution(trades: list[Any]) -> dict[str, Any]:
    """Week3：出场原因、持仓区间、剔 Top5 后盈亏。"""
    base = thin_layer1_attribution(trades)
    closed = [t for t in trades if getattr(t, "pnl", None) is not None]
    # exit reason
    reason_pnl: dict[str, float] = {}
    reason_n: dict[str, int] = {}
    hold_buckets = {"short_<3d": 0, "mid_3_20d": 0, "long_>20d": 0}
    hold_pnl = {"short_<3d": 0.0, "mid_3_20d": 0.0, "long_>20d": 0.0}
    per_trade: list[tuple[str, float]] = []

    for t in closed:
        pnl = float(t.pnl)
        reason = str(getattr(t, "exit_reason", None) or "signal_exit")
        reason_pnl[reason] = reason_pnl.get(reason, 0.0) + pnl
        reason_n[reason] = reason_n.get(reason, 0) + 1
        code = str(getattr(t, "code", ""))
        per_trade.append((code, pnl))

        entry = getattr(t, "entry_date", None)
        exit_ = getattr(t, "exit_date", None)
        days = None
        if entry is not None and exit_ is not None:
            try:
                days = int((exit_ - entry).days)
            except Exception:
                days = None
        if days is None:
            bucket = "mid_3_20d"
        elif days < 3:
            bucket = "short_<3d"
        elif days <= 20:
            bucket = "mid_3_20d"
        else:
            bucket = "long_>20d"
        hold_buckets[bucket] += 1
        hold_pnl[bucket] += pnl

    # exclude top5 winner names' pnl contribution
    by_code: dict[str, float] = {}
    for code, pnl in per_trade:
        by_code[code] = by_code.get(code, 0.0) + pnl
    ranked = sorted(by_code.items(), key=lambda x: x[1], reverse=True)
    top5_codes = {c for c, _ in ranked[:5]}
    pnl_ex_top5 = sum(p for c, p in by_code.items() if c not in top5_codes)
    still_profitable = pnl_ex_top5 > 0

    base.update({
        "layer": "1_thick",
        "exit_reason_pnl": {k: round(v, 2) for k, v in reason_pnl.items()},
        "exit_reason_count": reason_n,
        "holding_buckets": hold_buckets,
        "holding_bucket_pnl": {k: round(v, 2) for k, v in hold_pnl.items()},
        "pnl_excluding_top5_winners": round(pnl_ex_top5, 2),
        "still_profitable_ex_top5": still_profitable,
        "note": "Week3 加厚 Layer1；exit_reason 缺省 signal_exit",
    })
    return base
