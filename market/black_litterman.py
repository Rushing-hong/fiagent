"""Simplified Black-Litterman blender (numpy-only).

Views are absolute excess-return views on subsets of assets.
Prior equilibrium π = δ Σ w_mkt.
Posterior mean / unconstrained mean-variance weights for long-only projection.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def black_litterman_posterior(
    cov: np.ndarray,
    market_weights: np.ndarray,
    *,
    P: np.ndarray,
    Q: np.ndarray,
    omega: np.ndarray | None = None,
    delta: float = 2.5,
    tau: float = 0.05,
) -> dict[str, np.ndarray]:
    """
    Args:
        cov: (n,n) covariance of excess returns
        market_weights: (n,) prior market portfolio weights (sum≈1)
        P: (k,n) pick matrix
        Q: (k,) view returns
        omega: (k,k) view uncertainty; default diag(P (τΣ) P')
        delta: risk aversion
        tau: prior uncertainty scale
    """
    n = cov.shape[0]
    w = np.asarray(market_weights, dtype=float).reshape(n)
    w = np.clip(w, 0, None)
    if w.sum() <= 0:
        w = np.ones(n) / n
    else:
        w = w / w.sum()

    sigma = np.asarray(cov, dtype=float)
    # ridge for stability
    sigma = sigma + np.eye(n) * (1e-8 * np.trace(sigma) / n + 1e-12)
    pi = delta * sigma @ w

    P = np.asarray(P, dtype=float)
    Q = np.asarray(Q, dtype=float).reshape(-1)
    if omega is None:
        mid = P @ (tau * sigma) @ P.T
        omega = np.diag(np.maximum(np.diag(mid), 1e-12))
    else:
        omega = np.asarray(omega, dtype=float)

    tau_sig_inv = np.linalg.inv(tau * sigma)
    omega_inv = np.linalg.inv(omega)
    post_prec = tau_sig_inv + P.T @ omega_inv @ P
    post_cov = np.linalg.inv(post_prec)
    mu = post_cov @ (tau_sig_inv @ pi + P.T @ omega_inv @ Q)

    # Unconstrained MV: w ∝ Σ^{-1} μ
    inv_sig = np.linalg.inv(sigma)
    raw = inv_sig @ mu
    raw = np.clip(raw, 0, None)  # long-only projection
    if raw.sum() <= 0:
        raw = w.copy()
    weights = raw / raw.sum()
    return {"mu": mu, "pi": pi, "weights": weights, "post_cov": post_cov}


def views_from_absolute(
    n: int,
    view_specs: list[dict[str, Any]],
    codes: list[str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build P, Q, omega from absolute views.

    Each view_spec: {
      "assets": ["600519.SH", ...],  # equal weight within view
      "q": 0.02,                     # expected excess return
      "confidence": 0.5,             # 0..1 → scales omega inverse
    }
    """
    code_idx = {c: i for i, c in enumerate(codes)}
    rows: list[np.ndarray] = []
    qs: list[float] = []
    confs: list[float] = []
    for vs in view_specs:
        assets = vs.get("assets") or []
        idxs = [code_idx[a] for a in assets if a in code_idx]
        if not idxs:
            continue
        row = np.zeros(n)
        row[idxs] = 1.0 / len(idxs)
        rows.append(row)
        qs.append(float(vs.get("q", 0.0)))
        confs.append(float(np.clip(vs.get("confidence", 0.5), 0.05, 1.0)))
    if not rows:
        raise ValueError("无有效观点（assets 与 codes 无交集）")
    P = np.vstack(rows)
    Q = np.asarray(qs, dtype=float)
    # Higher confidence → smaller omega
    base = np.ones(len(qs)) * 0.05
    omega = np.diag(base / np.asarray(confs))
    return P, Q, omega


def cov_from_returns(returns: np.ndarray, floor: float = 1e-6) -> np.ndarray:
    """returns: (t, n)."""
    r = np.asarray(returns, dtype=float)
    if r.ndim != 2 or r.shape[0] < 2:
        n = r.shape[1] if r.ndim == 2 else 1
        return np.eye(n) * 0.04
    c = np.cov(r, rowvar=False)
    c = np.atleast_2d(c)
    c = c + np.eye(c.shape[0]) * floor
    return c
