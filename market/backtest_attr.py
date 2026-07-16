"""回测 Layer1/Layer2 归因。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


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

    by_code: dict[str, float] = {}
    for code, pnl in per_trade:
        by_code[code] = by_code.get(code, 0.0) + pnl
    ranked = sorted(by_code.items(), key=lambda x: x[1], reverse=True)
    top5_codes = {c for c, _ in ranked[:5]}
    pnl_ex_top5 = sum(p for c, p in by_code.items() if c not in top5_codes)

    base.update({
        "layer": "1_thick",
        "exit_reason_pnl": {k: round(v, 2) for k, v in reason_pnl.items()},
        "exit_reason_count": reason_n,
        "holding_buckets": hold_buckets,
        "holding_bucket_pnl": {k: round(v, 2) for k, v in hold_pnl.items()},
        "pnl_excluding_top5_winners": round(pnl_ex_top5, 2),
        "still_profitable_ex_top5": pnl_ex_top5 > 0,
        "note": "Week3 加厚 Layer1；exit_reason 缺省 signal_exit",
    })
    return base


def _ols_beta(y: np.ndarray, x: np.ndarray) -> dict[str, float | None]:
    """y = a + b x；返回 alpha/beta/r2/t_beta。"""
    mask = np.isfinite(y) & np.isfinite(x)
    y, x = y[mask], x[mask]
    n = len(y)
    if n < 30:
        return {"alpha": None, "beta": None, "r2": None, "t_beta": None, "n": n, "warn": "sample<30"}
    x_des = np.column_stack([np.ones(n), x])
    beta_hat, *_ = np.linalg.lstsq(x_des, y, rcond=None)
    a, b = float(beta_hat[0]), float(beta_hat[1])
    yhat = x_des @ beta_hat
    resid = y - yhat
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    dof = max(n - 2, 1)
    s2 = ss_res / dof
    xtx_inv = np.linalg.inv(x_des.T @ x_des + np.eye(2) * 1e-12)
    se_b = float(np.sqrt(max(s2 * xtx_inv[1, 1], 0.0)))
    t_b = b / se_b if se_b > 0 else None
    out: dict[str, float | None] = {
        "alpha_daily": round(a, 8),
        "alpha_annualized": round((1 + a) ** 252 - 1, 6) if a > -1 else None,
        "beta": round(b, 4),
        "r2": round(r2, 4),
        "t_beta": round(t_b, 3) if t_b is not None else None,
        "n": n,
    }
    if t_b is not None and abs(t_b) < 2:
        out["warn"] = "|t_beta|<2：相对该基准的暴露不稳定或偏弱"
    return out


def layer2_beta_attribution(
    strategy_returns: pd.Series,
    benchmarks: dict[str, pd.Series],
) -> dict[str, Any]:
    """
    benchmarks: {"HS300": ret_series, "ZZ500": ret_series}
    strategy_returns / benchmark returns 均为日收益，index 为日期。
    """
    y = strategy_returns.dropna()
    results: dict[str, Any] = {"layer": "2_beta", "benchmarks": {}}
    for name, bx in benchmarks.items():
        aligned = pd.concat([y, bx.rename("b")], axis=1, join="inner").dropna()
        if aligned.empty:
            results["benchmarks"][name] = {"error": "no_overlap"}
            continue
        fit = _ols_beta(aligned.iloc[:, 0].values, aligned["b"].values)
        results["benchmarks"][name] = fit
    results["note"] = (
        "R_strategy = α + β×R_benchmark；α 年化为交易日复利近似。"
        "A股建议同时看 HS300 与 ZZ500。"
    )
    return results


def risk_exposure_snapshot(
    data: dict[str, pd.DataFrame],
    weights: dict[str, float],
    *,
    lookback: int = 40,
) -> dict[str, Any]:
    """用 Barra-lite risk_* 做组合暴露快照（Week4）。"""
    from market.barra_lite import estimate_factor_model, portfolio_risk

    if len(data) < 3 or sum(abs(w) for w in weights.values()) <= 0:
        return {"error": "insufficient_data_or_weights", "purpose": "risk"}
    try:
        model = estimate_factor_model(data, window=10, lookback=lookback)
        risk = portfolio_risk(weights, model)
        risk["purpose"] = "risk"
        risk["note"] = (
            "仅 risk_* 风格暴露；勿与 alpha_* 选股因子混用。"
            + " " + str(risk.get("note") or "")
        )
        return risk
    except Exception as exc:
        return {"error": str(exc), "purpose": "risk"}
