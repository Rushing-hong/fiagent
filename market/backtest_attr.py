"""回测薄 Layer1 归因：Top 赢家/输家 + 总 PnL。"""

from __future__ import annotations

from typing import Any


def thin_layer1_attribution(trades: list[Any]) -> dict[str, Any]:
    """Phase1 Week2：按标的汇总已平仓盈亏。"""
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
        "note": "Week2 薄归因；出场原因/持仓区间见 Week3",
    }
