"""Trade attribution: link journal outcomes to decision quality categories."""

from __future__ import annotations

import json
from typing import Any

from tools.trade_journal import analyze_trade_journal, pair_trades_fifo
from tools._trade_journal_parsers import parse_file, records_to_dataframe


def _classify_roundtrip(pnl: float, hold_days: float) -> str:
    """Heuristic attribution label for a closed roundtrip."""
    if pnl > 0:
        return "correct_profit"
    if pnl < 0 and hold_days <= 3:
        return "timing_error_or_variance"
    if pnl < 0:
        return "wrong_loss"
    return "breakeven"


def build_attribution_from_journal(
    journal_json: dict[str, Any],
    *,
    file_path: str | None = None,
    ctx=None,
) -> dict[str, Any]:
    """Build attribution matrix from analyze_trade_journal output + FIFO roundtrips."""
    profile = journal_json.get("profile") or {}
    behavior = journal_json.get("behavior") or {}

    roundtrips: list[dict[str, Any]] = []
    if file_path and ctx is not None:
        try:
            from tools._fs import resolve_path

            path = resolve_path(ctx, file_path)
            _, records = parse_file(path)
            df = records_to_dataframe(records)
            roundtrips = pair_trades_fifo(df)
        except Exception as exc:
            roundtrips = [{"error": str(exc)}]

    buckets: dict[str, int] = {
        "correct_profit": 0,
        "wrong_loss": 0,
        "timing_error_or_variance": 0,
        "breakeven": 0,
        "luck_profit": 0,
        "execution_not_filled": 0,
    }
    samples: list[dict[str, Any]] = []
    for rt in roundtrips[:50]:
        if "error" in rt:
            continue
        label = _classify_roundtrip(float(rt.get("pnl", 0)), float(rt.get("hold_days", 0)))
        buckets[label] = buckets.get(label, 0) + 1
        samples.append({
            "symbol": rt.get("symbol"),
            "pnl": rt.get("pnl"),
            "pnl_pct": rt.get("pnl_pct"),
            "hold_days": rt.get("hold_days"),
            "attribution": label,
        })

    disposition = (behavior.get("disposition_effect") or {}).get("severity", "low")
    overtrade = (behavior.get("overtrading") or {}).get("severity", "low")
    chasing = (behavior.get("chasing_momentum") or {}).get("severity", "low")

    learnable = []
    if disposition in ("medium", "high"):
        learnable.append("处置效应：亏损单持有过久，不宜写入正向策略记忆")
    if overtrade in ("medium", "high"):
        learnable.append("过度交易：高频日收益更差，应降低交易频率规则权重")
    if chasing in ("medium", "high"):
        learnable.append("追涨：买入集中在上涨后，需强化入场纪律")

    return {
        "status": "ok",
        "summary": {
            "win_rate": profile.get("win_rate"),
            "total_pnl": profile.get("total_pnl"),
            "sharpe": profile.get("sharpe"),
            "max_drawdown": profile.get("max_drawdown"),
            "roundtrips_analyzed": len(samples),
        },
        "attribution_buckets": buckets,
        "sample_roundtrips": samples[:10],
        "behavior_flags": {
            "disposition_effect": disposition,
            "overtrading": overtrade,
            "chasing_momentum": chasing,
        },
        "learnable_lessons": learnable,
        "matrix_note": (
            "逻辑正确+盈利=correct_profit；逻辑错误+亏损=wrong_loss；"
            "其余需结合当时研究结论人工复核（luck_profit / execution_not_filled 待关联研究 run）"
        ),
    }


def analyze_journal_with_attribution(
    file_path: str,
    *,
    ctx,
    filter_expr: str = "",
) -> dict[str, Any]:
    raw = analyze_trade_journal(file_path, analysis_type="full", filter_expr=filter_expr, ctx=ctx)
    journal = json.loads(raw)
    if journal.get("status") != "ok":
        return journal
    journal["attribution"] = build_attribution_from_journal(
        journal, file_path=file_path, ctx=ctx,
    )
    return journal
