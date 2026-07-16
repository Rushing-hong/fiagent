"""组合风险：历史/参数 VaR 与 A 股情景压力。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# Preset stress windows (inclusive calendar dates); applied as equity path shocks
# via realized index-like drawdowns on portfolio returns.
ASHARE_STRESS_SCENARIOS: dict[str, dict[str, Any]] = {
    "2015_crash": {
        "label": "2015 股灾（流动性/跌停）",
        "start": "2015-06-15",
        "end": "2015-07-09",
        "shock": -0.35,
        "note": "单边下跌+涨跌停流动性冻结近似：组合一次性冲击 -35%",
    },
    "2018_trade_war": {
        "label": "2018 贸易摩擦",
        "start": "2018-01-29",
        "end": "2018-10-19",
        "shock": -0.25,
        "note": "中美贸易摩擦阶段近似冲击 -25%",
    },
    "2020_covid": {
        "label": "2020 疫情冲击",
        "start": "2020-01-23",
        "end": "2020-03-27",
        "shock": -0.18,
        "note": "疫情初期近似冲击 -18%",
    },
    "2022_lockdown": {
        "label": "2022 封控",
        "start": "2022-03-01",
        "end": "2022-05-31",
        "shock": -0.15,
        "note": "封控阶段近似冲击 -15%",
    },
}


def historical_var(
    returns: np.ndarray | list[float],
    *,
    alpha: float = 0.05,
) -> dict[str, float]:
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 10:
        raise ValueError("收益样本不足")
    q = float(np.quantile(r, alpha))
    cvar = float(r[r <= q].mean()) if np.any(r <= q) else q
    return {
        "var": -q,  # positive number = loss
        "cvar": -cvar,
        "alpha": alpha,
        "n": int(len(r)),
        "mean": float(r.mean()),
        "std": float(r.std()),
    }


def parametric_var(
    returns: np.ndarray | list[float],
    *,
    alpha: float = 0.05,
) -> dict[str, float]:
    try:
        from scipy.stats import norm  # type: ignore
        z = float(norm.ppf(alpha))
        pdf_z = float(norm.pdf(z))
    except Exception:
        z = -1.64485362695 if abs(alpha - 0.05) < 1e-9 else -2.32634787404
        pdf_z = 0.103952648
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 10:
        raise ValueError("收益样本不足")
    mu, sig = float(r.mean()), float(r.std())
    q = mu + z * sig
    cvar = -(mu - sig * pdf_z / alpha) if alpha > 0 else -q
    return {
        "var": -q,
        "cvar": float(cvar),
        "alpha": alpha,
        "n": int(len(r)),
        "mean": mu,
        "std": sig,
    }


def apply_stress_shocks(
    equity: float,
    *,
    scenarios: list[str] | None = None,
) -> list[dict[str, Any]]:
    names = scenarios or list(ASHARE_STRESS_SCENARIOS.keys())
    out = []
    for name in names:
        sc = ASHARE_STRESS_SCENARIOS.get(name)
        if not sc:
            continue
        shock = float(sc["shock"])
        after = equity * (1.0 + shock)
        out.append({
            "scenario": name,
            "label": sc["label"],
            "start": sc["start"],
            "end": sc["end"],
            "shock": shock,
            "equity_before": round(equity, 2),
            "equity_after": round(after, 2),
            "pnl": round(after - equity, 2),
            "pnl_pct": round(shock * 100, 2),
            "note": sc["note"],
        })
    return out


def returns_from_equity_curve(equity_curve: list[dict[str, Any]]) -> np.ndarray:
    eq = [float(x["equity"]) for x in equity_curve if x.get("equity") is not None]
    if len(eq) < 3:
        return np.array([])
    s = pd.Series(eq)
    return s.pct_change().dropna().values
