"""Barra-lite 多因子风险：CNE5 风格近似 risk_*（价量）。

输出因子协方差、特异波动、组合风险分解。非商业 Barra/CNE 模型。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from market.factor_zoo import RISK_FACTOR_IDS, compute_day_zscores, equal_weight_market_returns


def _aligned_returns(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    closes = {}
    for code, df in data.items():
        if "close" not in df.columns:
            continue
        closes[code] = df["close"].astype(float)
    if not closes:
        return pd.DataFrame()
    panel = pd.DataFrame(closes).sort_index().ffill()
    return panel.pct_change().dropna(how="all")


def build_factor_exposures(
    data: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    codes: list[str],
    *,
    window: int = 20,
    industry_map: dict[str, str] | None = None,
    factor_ids: list[str] | None = None,
) -> pd.DataFrame:
    """Rows=codes, cols=risk_* (+ optional industry dummies)."""
    fids = list(factor_ids or RISK_FACTOR_IDS)
    mkt = equal_weight_market_returns(data)
    zs = compute_day_zscores(data, date, codes, fids, market_rets=mkt)
    frame = pd.DataFrame(
        {fid: [zs[fid].get(c, 0.0) for c in codes] for fid in fids},
        index=codes,
    )
    if industry_map:
        inds = sorted({industry_map.get(c, "OTHER") for c in codes})
        for ind in inds[1:]:
            frame[f"ind_{ind}"] = [
                1.0 if industry_map.get(c, "OTHER") == ind else 0.0 for c in codes
            ]
    return frame.fillna(0.0)


def estimate_factor_model(
    data: dict[str, pd.DataFrame],
    *,
    window: int = 20,
    lookback: int = 60,
    industry_map: dict[str, str] | None = None,
    factor_ids: list[str] | None = None,
) -> dict[str, Any]:
    """
    Cross-sectional OLS each day: r = X f + e
    Then sample cov of factor returns; specific var = var(e).
    """
    codes = list(data.keys())
    rets = _aligned_returns(data)
    if rets.empty or len(rets) < max(window + 5, 20):
        raise ValueError("行情不足，无法估计风险模型")

    fids = list(factor_ids or RISK_FACTOR_IDS)
    # need n_obs > n_factors for stable cross-section
    max_f = max(3, len(codes) - 2)
    if len(fids) > max_f:
        fids = fids[:max_f]

    dates = list(rets.index)
    use_dates = dates[-(lookback + 1) :]
    factor_rets: list[np.ndarray] = []
    resid_list: dict[str, list[float]] = {c: [] for c in codes}
    factor_names: list[str] | None = None

    for i in range(1, len(use_dates)):
        d0, d1 = use_dates[i - 1], use_dates[i]
        try:
            X = build_factor_exposures(
                data, d0, codes, window=window, industry_map=industry_map, factor_ids=fids
            )
        except Exception:
            continue
        y = rets.loc[d1].reindex(codes).astype(float)
        mask = y.notna()
        if int(mask.sum()) < max(4, X.shape[1] + 1):
            continue
        Xm = X.loc[mask].values
        ym = y.loc[mask].values
        k = Xm.shape[1]
        beta, *_ = np.linalg.lstsq(
            Xm.T @ Xm + np.eye(k) * 1e-6, Xm.T @ ym, rcond=None
        )
        factor_names = list(X.columns)
        factor_rets.append(beta)
        resid = ym - Xm @ beta
        for j, c in enumerate(X.index[mask]):
            resid_list[c].append(float(resid[j]))

    if not factor_rets or factor_names is None:
        raise ValueError("有效截面日不足")

    F = np.vstack(factor_rets)
    factor_cov = np.cov(F, rowvar=False)
    if np.ndim(factor_cov) == 0:
        factor_cov = np.array([[float(factor_cov)]])
    factor_cov = np.atleast_2d(factor_cov)

    specific_var = {}
    for c, xs in resid_list.items():
        if len(xs) >= 5:
            specific_var[c] = float(np.var(xs))
        else:
            specific_var[c] = float(np.nanmean(list(specific_var.values())) if specific_var else 1e-4)

    last = use_dates[-1]
    X_last = build_factor_exposures(
        data, last, codes, window=window, industry_map=industry_map, factor_ids=fids
    )

    return {
        "factor_names": factor_names,
        "factor_cov": factor_cov,
        "factor_vol": np.sqrt(np.clip(np.diag(factor_cov), 0, None)),
        "specific_var": specific_var,
        "exposures": X_last,
        "n_days": len(factor_rets),
        "asof": str(last),
    }


def portfolio_risk(
    weights: dict[str, float],
    model: dict[str, Any],
) -> dict[str, Any]:
    codes = list(model["exposures"].index)
    w = np.array([float(weights.get(c, 0.0)) for c in codes], dtype=float)
    if w.sum() > 0:
        w = w / w.sum()
    X = model["exposures"].values
    Fcov = np.asarray(model["factor_cov"], dtype=float)
    d = np.array([float(model["specific_var"].get(c, 1e-4)) for c in codes])
    D = np.diag(d)

    # σ² = w' X F X' w + w' D w
    port_expo = X.T @ w
    sys_var = float(port_expo.T @ Fcov @ port_expo)
    spec_var = float(w.T @ D @ w)
    total = max(sys_var + spec_var, 0.0)
    # marginal factor risk contribution
    # RC_k ≈ (F X' w)_k * expo_k / σ
    Fx = Fcov @ port_expo
    factor_rc = {}
    for i, name in enumerate(model["factor_names"]):
        factor_rc[name] = float(Fx[i] * port_expo[i])

    return {
        "weights": {c: float(w[i]) for i, c in enumerate(codes) if abs(w[i]) > 1e-12},
        "factor_exposure": {
            model["factor_names"][i]: float(port_expo[i]) for i in range(len(port_expo))
        },
        "risk": {
            "total_var": total,
            "total_vol": float(np.sqrt(total)),
            "systematic_var": sys_var,
            "specific_var": spec_var,
            "systematic_share": float(sys_var / total) if total > 0 else 0.0,
        },
        "factor_risk_contribution_var": factor_rc,
        "asof": model.get("asof"),
        "n_model_days": model.get("n_days"),
        "note": "Barra-lite（mom/size/vol±行业）；非商业风险模型",
    }
