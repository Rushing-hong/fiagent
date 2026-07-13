"""波动择时 → 建议股指期货对冲比例（iVIX 停更后的代理）。

官方中国波指 iVIX 约 2018 年后停更；本工具用 50ETF(510050) 历史波动率分位数
作代理，映射到 suggest_hedge_ratio ∈ [0, 1]。可选尝试拉取指数代理作对照。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from market.envelope import clamp_int, err, ok, to_float
from market.market_data import fetch_one
from tools.base import BaseTool


def _hv_percentile(closes: pd.Series, window: int = 20) -> dict[str, float]:
    rets = closes.astype(float).pct_change().dropna()
    if len(rets) < window + 5:
        raise ValueError("行情不足，无法估计波动率分位")
    hv = rets.rolling(window).std() * np.sqrt(252)
    hv = hv.dropna()
    latest = float(hv.iloc[-1])
    pct = float((hv <= latest).mean())
    return {
        "hv_annual": latest,
        "hv_percentile": pct,
        "hv_median": float(hv.median()),
        "hv_p75": float(hv.quantile(0.75)),
        "hv_p90": float(hv.quantile(0.90)),
        "n_obs": int(len(hv)),
    }


def _map_hedge_ratio(pct: float, base: float, high: float, low: float) -> float:
    """High vol → higher hedge; low vol → lower hedge."""
    if pct >= 0.90:
        return high
    if pct >= 0.75:
        return base + 0.5 * (high - base)
    if pct <= 0.25:
        return low
    if pct <= 0.40:
        return low + 0.5 * (base - low)
    return base


class SuggestHedgeRatioTool(BaseTool):
    name = "suggest_hedge_ratio"
    summary = "波动分位→建议对冲比例（iVIX代理）"
    description = (
        "官方 iVIX 已停更；默认用 50ETF(510050) 近端年化波动率在历史中的分位数，"
        "映射为建议 hedge_ratio（可直接传给 run_backtest）。\n"
        "映射：分位≥90%→high，≥75% 抬升，≤25%→low，其余 base。"
        "输出含 note 标明非官方波指。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "default": "510050.SH",
                "description": "波动代理标的，默认上证50ETF",
            },
            "start_date": {"type": "string", "description": "历史起点 YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "历史终点 YYYY-MM-DD"},
            "window": {
                "type": "integer",
                "default": 20,
                "description": "波动率滚动窗口（交易日）",
            },
            "base_ratio": {"type": "number", "default": 0.5, "description": "中性环境对冲比"},
            "high_ratio": {"type": "number", "default": 1.0, "description": "高波动对冲比"},
            "low_ratio": {"type": "number", "default": 0.2, "description": "低波动对冲比"},
        },
        "required": ["start_date", "end_date"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        code = str(args.get("code") or "510050.SH")
        start = str(args.get("start_date") or "")
        end = str(args.get("end_date") or "")
        if not start or not end:
            return err("需要 start_date 与 end_date")
        window = clamp_int(args.get("window"), 20, 5, 120)
        base = to_float(args.get("base_ratio"))
        high = to_float(args.get("high_ratio"))
        low = to_float(args.get("low_ratio"))
        if base is None:
            base = 0.5
        if high is None:
            high = 1.0
        if low is None:
            low = 0.2

        try:
            rows, source = fetch_one(code, start, end)
            if not rows:
                return err(f"无行情: {code}")
            df = pd.DataFrame(rows)
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            closes = df.set_index("trade_date")["close"].sort_index()
            stats = _hv_percentile(closes, window=window)
        except Exception as exc:
            return err(f"波动估计失败: {exc}")

        ratio = _map_hedge_ratio(
            stats["hv_percentile"], float(base), float(high), float(low)
        )
        return ok(
            {
                "suggest_hedge_ratio": round(ratio, 4),
                "proxy": code,
                "stats": {
                    k: round(v, 6) if isinstance(v, float) else v
                    for k, v in stats.items()
                },
                "mapping": {
                    "base_ratio": base,
                    "high_ratio": high,
                    "low_ratio": low,
                    "window": window,
                },
                "note": (
                    "官方中国波指 iVIX 约2018年后停更；本结果为 ETF 历史波动分位代理，"
                    "非官方隐含波动率。可直接用于 run_backtest(hedge_ratio=...)。"
                ),
            },
            market="a_share",
            source=source if isinstance(source, str) else "market_data",
            tool="suggest_hedge_ratio",
            quality="degraded",
        )
