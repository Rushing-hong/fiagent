"""交易日记分析：同花顺/东财/富途导出。"""

from __future__ import annotations

import json
import logging
from collections import defaultdict, deque
from typing import Any

import pandas as pd

from tools._fs import PathError, resolve_path
from tools.base import BaseTool
from tools._trade_journal_parsers import parse_file, records_to_dataframe

logger = logging.getLogger(__name__)

_ALLOWED_EXT = {".csv", ".xlsx", ".xls"}


def pair_trades_fifo(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Pair buys and sells per symbol using FIFO to compute per-roundtrip PnL.

    Args:
        df: Standardized DataFrame (datetime-sorted).

    Returns:
        List of dicts: symbol, buy_dt, sell_dt, qty, buy_price, sell_price,
        hold_days, pnl, pnl_pct. Unmatched positions are ignored.
    """
    queues: dict[str, deque] = defaultdict(deque)
    roundtrips: list[dict[str, Any]] = []

    for row in df.itertuples(index=False):
        if row.side == "buy":
            queues[row.symbol].append({
                "dt": row.datetime,
                "qty": row.quantity,
                "price": row.price,
                "fee": row.fee,
            })
            continue

        # sell: match against oldest buys
        remaining = row.quantity
        q = queues[row.symbol]
        while remaining > 1e-9 and q:
            lot = q[0]
            take = min(lot["qty"], remaining)
            hold = (row.datetime - lot["dt"]).total_seconds() / 86400.0
            gross = (row.price - lot["price"]) * take
            # Proportional fee allocation
            buy_fee = lot["fee"] * (take / lot["qty"]) if lot["qty"] else 0.0
            sell_fee = row.fee * (take / row.quantity) if row.quantity else 0.0
            pnl = gross - buy_fee - sell_fee
            cost = lot["price"] * take
            pnl_pct = pnl / cost if cost else 0.0
            roundtrips.append({
                "symbol": row.symbol,
                "buy_dt": lot["dt"],
                "sell_dt": row.datetime,
                "qty": take,
                "buy_price": lot["price"],
                "sell_price": row.price,
                "hold_days": round(hold, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 4),
            })
            lot["qty"] -= take
            remaining -= take
            if lot["qty"] <= 1e-9:
                q.popleft()
    return roundtrips


def _safe_div(a: float, b: float) -> float:
    return float(a) / float(b) if b else 0.0


def _compute_profile(df: pd.DataFrame) -> dict[str, Any]:
    """Build the trading profile dict.

    Args:
        df: Standardized DataFrame (datetime parsed, sorted).

    Returns:
        Dict with avg_holding_days, trade_frequency_per_week, win_rate,
        profit_loss_ratio, total_pnl, max_drawdown, top_symbols,
        market_distribution, hourly_distribution, roundtrips_sample.
    """
    if df.empty:
        return {"error": "empty trade journal"}

    rts = pair_trades_fifo(df)
    rts_df = pd.DataFrame(rts)

    total_trades = len(df)
    span_days = max(1, (df["datetime"].max() - df["datetime"].min()).days)
    freq_per_week = round(total_trades / span_days * 7, 2)

    if not rts_df.empty:
        wins = rts_df[rts_df["pnl"] > 0]
        losses = rts_df[rts_df["pnl"] < 0]
        avg_win = wins["pnl"].mean() if len(wins) else 0.0
        avg_loss = losses["pnl"].mean() if len(losses) else 0.0
        win_rate = round(len(wins) / len(rts_df), 4)
        pnl_ratio = round(_safe_div(avg_win, abs(avg_loss)), 2) if avg_loss else float("inf") if avg_win else 0.0
        avg_hold = round(rts_df["hold_days"].mean(), 2)
        total_pnl = round(rts_df["pnl"].sum(), 2)
        # Cumulative PnL → max drawdown
        cum = rts_df.sort_values("sell_dt")["pnl"].cumsum()
        running_max = cum.cummax()
        drawdown = (cum - running_max).min()
        max_drawdown = round(float(drawdown), 2) if pd.notna(drawdown) else 0.0
    else:
        win_rate = pnl_ratio = avg_hold = total_pnl = max_drawdown = 0.0

    top_symbols = (
        df.groupby("symbol")
        .agg(trades=("symbol", "count"), total_amount=("amount", "sum"))
        .sort_values("total_amount", ascending=False)
        .head(10)
        .round(2)
        .reset_index()
        .to_dict(orient="records")
    )

    market_dist = df["market"].value_counts().to_dict()
    hourly_dist = df["datetime"].dt.hour.value_counts().sort_index().to_dict()
    hourly_dist = {int(h): int(c) for h, c in hourly_dist.items()}

    sample = rts_df.head(5).copy()
    if not sample.empty:
        sample["buy_dt"] = sample["buy_dt"].astype(str)
        sample["sell_dt"] = sample["sell_dt"].astype(str)
        roundtrips_sample = sample.to_dict(orient="records")
    else:
        roundtrips_sample = []

    return {
        "total_trades": total_trades,
        "total_roundtrips": len(rts_df),
        "avg_holding_days": avg_hold,
        "trade_frequency_per_week": freq_per_week,
        "win_rate": win_rate,
        "profit_loss_ratio": pnl_ratio,
        "total_pnl": total_pnl,
        "max_drawdown": max_drawdown,
        "top_symbols": top_symbols,
        "market_distribution": market_dist,
        "hourly_distribution": hourly_dist,
        "roundtrips_sample": roundtrips_sample,
    }


def _compute_strategy_style(df: pd.DataFrame) -> dict[str, Any]:
    """Infer trading style from trade patterns.

    Classifies into: 短线打板 / 波段操作 / 长期价值 / 高频交易 / 追涨杀跌.
    Uses: holding period distribution, trade frequency, turnover, win rate.
    """
    if df.empty:
        return {"error": "empty trade journal"}

    rts = pd.DataFrame(pair_trades_fifo(df))
    n_trades = len(df)
    span = (df["datetime"].max() - df["datetime"].min()).days
    span = max(1, span)
    freq_per_week = round(n_trades / span * 7, 1)

    # Holding period stats
    if not rts.empty and "hold_days" in rts.columns:
        median_hold = float(rts["hold_days"].median())
        p90_hold = float(rts["hold_days"].quantile(0.9))

        intraday_ratio = float(len(rts[rts["hold_days"] <= 1]) / len(rts)) if len(rts) else 0
        short_ratio = float(len(rts[rts["hold_days"] <= 5]) / len(rts)) if len(rts) else 0
        long_ratio = float(len(rts[rts["hold_days"] >= 60]) / len(rts)) if len(rts) else 0
    else:
        median_hold = p90_hold = 0.0
        intraday_ratio = short_ratio = long_ratio = 0.0

    # Win rate
    wins = len(rts[rts["pnl"] > 0]) if not rts.empty else 0
    total_rt = max(len(rts), 1)
    win_rate = round(wins / total_rt, 3)

    # Symbol concentration
    symbol_counts = df["symbol"].value_counts()
    top_1_pct = round(float(symbol_counts.iloc[0]) / n_trades, 3) if len(symbol_counts) > 0 else 0
    top_3_pct = round(float(symbol_counts.iloc[:3].sum()) / n_trades, 3) if len(symbol_counts) > 0 else 0

    # Buy-side analysis: buying after price rise (chasing) vs after drop (dip-buying)
    buys = df[df["side"] == "buy"].sort_values(["symbol", "datetime"])
    if len(buys) > 5:
        buys["price_change"] = buys.groupby("symbol")["price"].pct_change()
        chase_ratio = float(len(buys[buys["price_change"] > 0.02]) / len(buys.dropna(subset=["price_change"]))) if len(buys.dropna(subset=["price_change"])) > 0 else 0
        dip_ratio = float(len(buys[buys["price_change"] < -0.02]) / len(buys.dropna(subset=["price_change"]))) if len(buys.dropna(subset=["price_change"])) > 0 else 0
    else:
        chase_ratio = dip_ratio = 0.0

    # Classification
    styles = []
    style_scores: dict[str, float] = {}

    # 短线打板: very short hold (<3 days median), very high frequency (>5/week)
    if median_hold <= 3 and freq_per_week >= 5:
        score = min(1.0, (1 - median_hold / 3) * 0.5 + min(freq_per_week / 10, 1) * 0.5)
        style_scores["短线打板"] = round(score, 2)
    elif median_hold <= 5 and short_ratio > 0.5:
        style_scores["短线交易"] = round(short_ratio, 2)

    # 波段操作: medium hold (5-30 days), moderate frequency
    if 5 <= median_hold <= 30 and 1 <= freq_per_week <= 10:
        score = min(1.0, (1 - abs(median_hold - 15) / 15) * 0.5 + min(freq_per_week / 5, 1) * 0.5)
        style_scores["波段操作"] = round(score, 2)

    # 长期价值: long hold (>30 days median), low frequency, high concentration
    if median_hold >= 30 and freq_per_week <= 3:
        score = min(1.0, min(median_hold / 90, 1) * 0.4 + (1 - freq_per_week / 5) * 0.3 + top_3_pct * 0.3)
        style_scores["长期价值"] = round(score, 2)

    # 高频交易: very high frequency (>20/week), very short hold
    if freq_per_week >= 10 and median_hold <= 2:
        style_scores["高频交易"] = round(min(1.0, freq_per_week / 30), 2)

    # 追涨杀跌: high chase ratio, low win rate
    if chase_ratio > 0.5 and win_rate < 0.5:
        style_scores["追涨型"] = round(chase_ratio * (1 - win_rate) * 2, 2)

    # 抄底型: high dip-buying ratio
    if dip_ratio > 0.4 and chase_ratio < 0.3:
        style_scores["抄底型"] = round(dip_ratio, 2)

    # Pick top style
    primary = max(style_scores, key=style_scores.get) if style_scores else "混合型"
    primary_score = style_scores.get(primary, 0)

    # Recommendations based on style
    recommendations = []
    if primary == "短线打板":
        recommendations = [
            "关注 get_limit_board 工具获取每日涨停板数据",
            "建议 run_backtest 回测打板策略(涨停次日开盘买入)",
            "注意 A 股 T+1 限制：当日打板次日才能卖出",
        ]
    elif primary == "波段操作":
        recommendations = [
            "用 pattern 工具识别支撑阻力位辅助止盈止损",
            "用 get_fund_flow 监控主力资金动向",
            "建议 run_backtest(strategy='ma_cross') 测试均线进出场",
        ]
    elif primary == "长期价值":
        recommendations = [
            "用 screen_fundamental 筛选低估值高 ROE 股票",
            "用 get_financial_statements 深度分析财报质量",
            "用 get_dividend_calendar 关注分红再投机会",
        ]
    elif primary in ("追涨型", "高频交易"):
        recommendations = [
            f"⚠️ 你的追涨比例 {chase_ratio:.0%}，参考 behavior 中的 chasing_momentum 诊断",
            "建议缩短持仓周期或设置更严格的止损规则",
        ]

    return {
        "primary_style": primary,
        "confidence": primary_score,
        "all_styles": style_scores,
        "evidence": {
            "n_trades": n_trades,
            "trade_frequency_per_week": freq_per_week,
            "median_hold_days": round(median_hold, 1),
            "p90_hold_days": round(p90_hold, 1),
            "intraday_ratio": round(intraday_ratio, 3),
            "short_term_ratio": round(short_ratio, 3),
            "long_term_ratio": round(long_ratio, 3),
            "win_rate": win_rate,
            "top1_symbol_pct": round(top_1_pct, 3),
            "top3_symbol_pct": round(top_3_pct, 3),
            "chase_ratio": round(chase_ratio, 3),
            "dip_buy_ratio": round(dip_ratio, 3),
        },
        "recommendations": recommendations,
    }


def _severity(score: float, thresholds: tuple[float, float]) -> str:
    """Map a numeric score to low/medium/high given (med_cutoff, high_cutoff)."""
    med, high = thresholds
    if score >= high:
        return "high"
    if score >= med:
        return "medium"
    return "low"


def _disposition_effect(rts_df: pd.DataFrame) -> dict[str, Any]:
    """Detect disposition effect: holding losers longer than winners.

    Metric = avg_loss_hold / avg_win_hold. A ratio > 1 means the user holds
    losing positions longer than winning ones — the classic disposition bias.
    """
    if rts_df.empty:
        return {"severity": "low", "evidence": "no closed roundtrips"}
    wins = rts_df[rts_df["pnl"] > 0]
    losses = rts_df[rts_df["pnl"] < 0]
    if wins.empty or losses.empty:
        return {
            "severity": "low",
            "evidence": "not enough winners and losers to compare holding times",
        }
    win_hold = float(wins["hold_days"].mean())
    loss_hold = float(losses["hold_days"].mean())
    ratio = loss_hold / win_hold if win_hold > 0 else float("inf")
    severity = _severity(ratio, (1.2, 1.5))
    return {
        "severity": severity,
        "ratio_loss_to_win_hold": round(ratio, 2),
        "avg_winner_hold_days": round(win_hold, 2),
        "avg_loser_hold_days": round(loss_hold, 2),
        "evidence": (
            f"Losing roundtrips held {loss_hold:.1f}d vs winning "
            f"{win_hold:.1f}d (ratio {ratio:.2f}). "
            + ("Classic disposition pattern." if severity == "high"
               else "Mild hold-losers-longer tendency." if severity == "medium"
               else "Hold times roughly symmetric.")
        ),
    }


def _overtrading(df: pd.DataFrame, rts_df: pd.DataFrame) -> dict[str, Any]:
    """Detect overtrading: high-activity days produce worse PnL.

    Buckets trading days into top-quartile (busy) and bottom-quartile (quiet)
    by trade count, then compares the realized PnL of roundtrips whose sell
    lands on each bucket.
    """
    if df.empty or rts_df.empty:
        return {"severity": "low", "evidence": "insufficient data"}

    daily_trades = df.groupby(df["datetime"].dt.date).size()
    if len(daily_trades) < 4:
        return {"severity": "low", "evidence": "fewer than 4 trading days"}

    busy_cut = daily_trades.quantile(0.75)
    quiet_cut = daily_trades.quantile(0.25)
    busy_days = set(daily_trades[daily_trades >= busy_cut].index)
    quiet_days = set(daily_trades[daily_trades <= quiet_cut].index)

    rts_df = rts_df.copy()
    rts_df["sell_date"] = pd.to_datetime(rts_df["sell_dt"]).dt.date
    busy_pnl = rts_df[rts_df["sell_date"].isin(busy_days)]["pnl"]
    quiet_pnl = rts_df[rts_df["sell_date"].isin(quiet_days)]["pnl"]
    if busy_pnl.empty or quiet_pnl.empty:
        return {"severity": "low", "evidence": "roundtrips not spread across busy/quiet days"}

    busy_avg = float(busy_pnl.mean())
    quiet_avg = float(quiet_pnl.mean())

    # severity rule: busy-day PnL must be meaningfully worse than quiet-day
    gap = quiet_avg - busy_avg
    base = abs(quiet_avg) if quiet_avg != 0 else 1.0
    severity = _severity(gap / base, (0.3, 1.0)) if busy_avg < quiet_avg else "low"

    return {
        "severity": severity,
        "busy_day_avg_pnl": round(busy_avg, 2),
        "quiet_day_avg_pnl": round(quiet_avg, 2),
        "busy_day_trade_threshold": round(float(busy_cut), 1),
        "evidence": (
            f"On busy days (≥{busy_cut:.0f} trades) avg PnL {busy_avg:+.0f}; "
            f"on quiet days (≤{quiet_cut:.0f}) avg PnL {quiet_avg:+.0f}. "
            + ("High activity hurts returns." if severity == "high"
               else "Some drag from busy-day trading." if severity == "medium"
               else "Activity level does not materially hurt PnL.")
        ),
    }


def _chasing_momentum(df: pd.DataFrame) -> dict[str, Any]:
    """Detect chasing: buys concentrated after recent price rises in the same symbol.

    For each BUY, look at the user's own last 3 trades of that symbol. If the
    price trended upward (last trade price > 3rd-prior by > 3%), count the buy
    as a chase. Ratio of chasing buys → severity.
    """
    buys = df[df["side"] == "buy"].sort_values(["symbol", "datetime"]).copy()
    if buys.empty:
        return {"severity": "low", "evidence": "no buys"}

    buys["prev3_price"] = buys.groupby("symbol")["price"].shift(3)
    matured = buys.dropna(subset=["prev3_price"])
    if matured.empty:
        return {
            "severity": "low",
            "evidence": "not enough repeat buys per symbol to evaluate chasing",
        }
    chased = matured[matured["price"] > matured["prev3_price"] * 1.03]
    ratio = len(chased) / len(matured)
    severity = _severity(ratio, (0.4, 0.6))
    return {
        "severity": severity,
        "chase_ratio": round(ratio, 3),
        "buys_evaluated": int(len(matured)),
        "evidence": (
            f"{len(chased)}/{len(matured)} buys ({ratio:.0%}) came after a >3% "
            "price run-up in the same symbol. "
            + ("Strong chasing pattern." if severity == "high"
               else "Some chasing tendency." if severity == "medium"
               else "No clear chasing bias.")
        ),
    }


def _anchoring(df: pd.DataFrame) -> dict[str, Any]:
    """Detect price anchoring: repeated trades cluster in a narrow price band.

    For each symbol with ≥5 trades, compute σ(price)/mean(price). A low ratio
    (<0.05) means the user consistently trades the same price area, suggesting
    they are anchored to a reference price rather than reacting to moves.
    """
    grouped = df.groupby("symbol")
    rows: list[dict[str, Any]] = []
    for sym, sub in grouped:
        if len(sub) < 5:
            continue
        mean = float(sub["price"].mean())
        std = float(sub["price"].std())
        if mean == 0:
            continue
        cv = std / mean
        rows.append({"symbol": sym, "trades": len(sub), "mean_price": round(mean, 2), "cv": round(cv, 4)})

    if not rows:
        return {"severity": "low", "evidence": "no symbol has ≥5 trades to evaluate anchoring"}

    anchored = [r for r in rows if r["cv"] < 0.05]
    ratio = len(anchored) / len(rows)
    severity = _severity(ratio, (0.33, 0.66))
    return {
        "severity": severity,
        "anchored_symbol_ratio": round(ratio, 3),
        "symbols_evaluated": len(rows),
        "anchored_symbols": anchored[:5],
        "evidence": (
            f"{len(anchored)}/{len(rows)} frequently-traded symbols stayed in a "
            "narrow price band (CV<5%). "
            + ("Strong anchoring — repeated trades at the same price." if severity == "high"
               else "Some anchoring on select symbols." if severity == "medium"
               else "Prices vary naturally across repeat trades.")
        ),
    }


def _compute_behavior(df: pd.DataFrame) -> dict[str, Any]:
    """Run all 4 behavior diagnostics.

    Args:
        df: Standardized DataFrame (datetime-sorted).

    Returns:
        Dict with disposition_effect / overtrading / chasing_momentum /
        anchoring keys, each {severity, evidence, ...metrics}.
    """
    if df.empty:
        return {"error": "empty trade journal"}
    rts_df = pd.DataFrame(pair_trades_fifo(df))
    return {
        "disposition_effect": _disposition_effect(rts_df),
        "overtrading": _overtrading(df, rts_df),
        "chasing_momentum": _chasing_momentum(df),
        "anchoring": _anchoring(df),
    }


def _apply_filter(df: pd.DataFrame, expr: str) -> pd.DataFrame:
    """Filter DataFrame by a simple expression.

    Supports:
        - "YYYY-MM to YYYY-MM" or "YYYY-MM-DD to YYYY-MM-DD" (date range)
        - "symbol=XXX" (exact match)
        - "market=china_a|us|hk|crypto"

    Args:
        df: Standardized DataFrame.
        expr: Filter expression.

    Returns:
        Filtered DataFrame (may be empty).
    """
    expr = expr.strip()
    if not expr:
        return df

    if " to " in expr:
        try:
            lo_raw, hi_raw = (p.strip() for p in expr.split(" to ", 1))
            lo = pd.to_datetime(lo_raw)
            hi = pd.to_datetime(hi_raw) + pd.Timedelta(days=1)
            return df[(df["datetime"] >= lo) & (df["datetime"] < hi)]
        except Exception as exc:
            logger.warning("filter date parse failed: %s", exc)
            return df

    if "=" in expr:
        key, val = (p.strip() for p in expr.split("=", 1))
        if key in df.columns:
            return df[df[key].astype(str).str.upper() == val.upper()]
    return df


def analyze_trade_journal(file_path: str, analysis_type: str = "full", filter_expr: str = "", *, ctx=None) -> str:
    """Parse a trade journal and return a JSON analysis.

    Args:
        file_path: Path to CSV/Excel file.
        analysis_type: "full" | "profile" | "behavior" | "strategy".
            "profile" and "behavior" are fully implemented; "strategy" still
            returns a Phase 4c placeholder.
        filter_expr: Optional filter. Examples: "2026-01 to 2026-03",
            "symbol=600519.SH", "market=china_a".

    Returns:
        JSON string. Keys: status, file, format_detected, total_records,
        date_range, market, profile / behavior (when applicable).
    """
    try:
        if ctx is None:
            return json.dumps({"status": "error", "error": "内部错误: 缺少 ctx"}, ensure_ascii=False)
        path = resolve_path(ctx, file_path)
    except PathError as exc:
        return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
    if not path.exists():
        return json.dumps({"status": "error", "error": f"File not found: {file_path}"}, ensure_ascii=False)
    if path.suffix.lower() not in _ALLOWED_EXT:
        return json.dumps(
            {"status": "error", "error": f"Unsupported extension {path.suffix}. Expected .csv/.xlsx/.xls"},
            ensure_ascii=False,
        )

    try:
        fmt, records = parse_file(path)
    except (ValueError, FileNotFoundError) as exc:
        return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

    if not records:
        return json.dumps(
            {"status": "error", "error": "No trade records parsed"}, ensure_ascii=False
        )

    df = records_to_dataframe(records)
    filtered = _apply_filter(df, filter_expr)

    result: dict[str, Any] = {
        "status": "ok",
        "file": path.name,
        "format_detected": fmt,
        "total_records": len(filtered),
        "date_range": _format_range(filtered),
        "symbols_count": int(filtered["symbol"].nunique()) if not filtered.empty else 0,
        "market": _pick_dominant_market(filtered),
    }
    if filter_expr:
        result["filter_applied"] = filter_expr

    if analysis_type in {"full", "profile"}:
        result["profile"] = _compute_profile(filtered)

    if analysis_type in {"full", "behavior"}:
        result["behavior"] = _compute_behavior(filtered)

    if analysis_type in {"full", "strategy"}:
        result["strategy_style"] = _compute_strategy_style(filtered)

    return json.dumps(result, ensure_ascii=False, default=str)


def _format_range(df: pd.DataFrame) -> str:
    """Return 'YYYY-MM-DD ~ YYYY-MM-DD' or empty."""
    if df.empty:
        return ""
    return f"{df['datetime'].min().date()} ~ {df['datetime'].max().date()}"


def _pick_dominant_market(df: pd.DataFrame) -> str:
    """Return the most-traded market; empty when df is empty."""
    if df.empty:
        return ""
    return df["market"].value_counts().idxmax()


class TradeJournalTool(BaseTool):
    name = "analyze_trade_journal"
    summary = "解析券商交割单/交易记录"
    description = (
        "分析用户交易日记（CSV/Excel）：支持同花顺、东方财富、富途、通用格式。"
        "返回交易画像与行为偏差诊断。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "工作区内 CSV/Excel 路径"},
            "analysis_type": {
                "type": "string",
                "enum": ["full", "profile", "behavior", "strategy"],
                "default": "full",
            },
            "filter_expr": {
                "type": "string",
                "description": "可选过滤，如 2026-01 to 2026-03 或 symbol=600519.SH",
                "default": "",
            },
        },
        "required": ["file_path"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        return analyze_trade_journal(
            file_path=args.get("file_path", ""),
            analysis_type=args.get("analysis_type", "full"),
            filter_expr=args.get("filter_expr", ""),
            ctx=ctx,
        )
