"""因子分析工具：IC/IR + 分层净值 + IC 衰减。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from analysis.factor_core import compute_group_equity, compute_ic_series
from tools._fs import PathError, resolve_path
from tools.base import BaseTool


class FactorAnalysisTool(BaseTool):
    name = "factor_analysis"
    summary = "因子 IC/IR 与分层回测 + IC 衰减分析"
    description = (
        "对因子 CSV 与收益 CSV 计算 IC/IR、分层累计净值、IC 衰减曲线。\n"
        "CSV 格式：index=日期，columns=股票代码。\n"
        "IC 衰减: 计算因子在 forward 1/5/10/20/60 天的预测力，判断因子有效期。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "factor_csv": {"type": "string", "description": "因子值 CSV 路径"},
            "return_csv": {"type": "string", "description": "收益率 CSV 路径"},
            "output_dir": {"type": "string", "description": "输出目录（工作区内）"},
            "n_groups": {"type": "integer", "default": 5},
            "forward_periods": {
                "type": "string",
                "default": "",
                "description": "IC 衰减分析的 forward 天数，逗号分隔。如 '1,5,10,20,60'。为空则只做单期",
            },
        },
        "required": ["factor_csv", "return_csv", "output_dir"],
    }
    is_readonly = False
    repeatable = True

    def execute(self, args: dict, ctx) -> str:
        try:
            factor_path = resolve_path(ctx, args.get("factor_csv", ""))
            return_path = resolve_path(ctx, args.get("return_csv", ""))
            out_path = resolve_path(ctx, args.get("output_dir", ""))
        except PathError as e:
            return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)
        n_groups = int(args.get("n_groups", 5))
        forward_raw = str(args.get("forward_periods", ""))
        try:
            factor_df = pd.read_csv(factor_path, index_col=0, parse_dates=True)
            return_df = pd.read_csv(return_path, index_col=0, parse_dates=True)
        except Exception as e:
            return json.dumps({"status": "error", "error": f"读取 CSV 失败: {e}"}, ensure_ascii=False)
        if factor_df.empty or return_df.empty:
            return json.dumps({"status": "error", "error": "数据为空"}, ensure_ascii=False)

        # 默认：因子日 T 对齐次日收益 T+1（避免同日 look-ahead）
        fwd1 = _forward_return_panel(return_df, 1)
        ic_series = compute_ic_series(factor_df, fwd1)
        if ic_series.empty:
            return json.dumps({"status": "error", "error": "IC 计算失败，共同样本不足"}, ensure_ascii=False)
        out_path.mkdir(parents=True, exist_ok=True)
        ic_series.to_csv(out_path / "ic_series.csv", header=["IC"])
        ic_mean = float(ic_series.mean())
        ic_std = float(ic_series.std())
        ir = ic_mean / ic_std if ic_std > 0 else 0.0
        summary = {
            "ic_mean": round(ic_mean, 6),
            "ic_std": round(ic_std, 6),
            "ir": round(ir, 4),
            "ic_positive_ratio": round(float((ic_series > 0).mean()), 4),
            "ic_count": len(ic_series),
            "forward_days": 1,
            "note": "IC/分层默认因子 T vs 收益 T+1",
        }
        (out_path / "ic_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
        )

        # Group equity
        equity_df = compute_group_equity(factor_df, fwd1, n_groups)
        if not equity_df.empty:
            equity_df.to_csv(out_path / "group_equity.csv")
            long_short = float(equity_df.iloc[-1, -1] - equity_df.iloc[-1, 0])
        else:
            long_short = 0.0

        result = {
            "status": "ok",
            **summary,
            "n_groups": n_groups,
            "long_short_spread": round(long_short, 4),
            "output_dir": str(out_path),
        }

        # IC decay analysis
        if forward_raw:
            try:
                periods = [int(p.strip()) for p in forward_raw.split(",") if p.strip().isdigit()]
            except ValueError:
                periods = []
            if periods:
                decay = _compute_ic_decay(factor_df, return_df, periods)
                result["ic_decay"] = decay
                (out_path / "ic_decay.json").write_text(
                    json.dumps(decay, ensure_ascii=False, indent=2), encoding="utf-8",
                )

        return json.dumps(result, ensure_ascii=False, indent=2)


def _forward_return_panel(return_df: pd.DataFrame, n: int) -> pd.DataFrame:
    """At date T: cumulative return over trading days (T+1)..(T+n)."""
    if n < 1:
        raise ValueError("forward days must be >= 1")
    # rolling(n) at index t+n = product of (t+1)..(t+n); shift(-n) labels result at t
    cum = (1.0 + return_df).rolling(n).apply(lambda x: float(x.prod() - 1.0), raw=True)
    return cum.shift(-n)


def _compute_ic_decay(
    factor_df: pd.DataFrame,
    return_df: pd.DataFrame,
    periods: list[int],
) -> list[dict]:
    """Compute IC at multiple forward horizons to assess factor decay.

    For each forward period N: factor at T vs cumulative return from T+1 to T+N.
    """
    results = []
    for n in periods:
        forward_return = _forward_return_panel(return_df, n)
        ic = compute_ic_series(factor_df, forward_return)
        if ic.empty:
            results.append({"forward_days": n, "ic_mean": None, "ic_std": None, "ir": None, "n_obs": 0})
            continue
        ic_mean = float(ic.mean())
        ic_std = float(ic.std())
        results.append({
            "forward_days": n,
            "ic_mean": round(ic_mean, 6),
            "ic_std": round(ic_std, 6),
            "ir": round(ic_mean / ic_std, 4) if ic_std > 0 else 0.0,
            "ic_positive_ratio": round(float((ic > 0).mean()), 4),
            "n_obs": len(ic),
        })

    # Half-life estimation: find the forward period where IC drops to ~50% of max
    if results and results[0]["ic_mean"] and results[0]["ic_mean"] > 0:
        peak_ic = results[0]["ic_mean"]
        half_life = None
        for r in results[1:]:
            if r["ic_mean"] and abs(r["ic_mean"]) < abs(peak_ic) * 0.5:
                half_life = r["forward_days"]
                break
        if half_life:
            decay_verdict = f"因子半衰期约 {half_life} 天 — {'短效因子，适合高频换仓' if half_life <= 10 else '中效因子' if half_life <= 30 else '长效因子，适合低频换仓'}"
        else:
            decay_verdict = "因子衰减慢，预测力持久"
    else:
        decay_verdict = "无有效 IC 或 IC 为负"

    for r in results:
        r["verdict"] = decay_verdict

    return results

