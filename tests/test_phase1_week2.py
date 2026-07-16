"""Phase1 Week2: factor zoo, layer1 thin, barra risk_*."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from market.backtest_attr import thin_layer1_attribution
from market.backtest_engine import BacktestConfig, BacktestEngine
from market.barra_lite import build_factor_exposures
from market.factor_zoo import ALPHA_FACTOR_IDS, compute_day_zscores
from market.research_store import ResearchStore


def _ohlcv(n: int = 80, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-02", periods=n)
    px = 10 * np.cumprod(1 + rng.normal(0.001, 0.02, n))
    return pd.DataFrame({
        "open": px, "high": px * 1.01, "low": px * 0.99, "close": px,
        "volume": rng.integers(1e5, 1e6, n).astype(float),
    }, index=idx)


def test_factor_zoo_alpha_risk_namespaces():
    data = {f"S{i}": _ohlcv(80, seed=i) for i in range(8)}
    date = list(data["S0"].index)[-1]
    codes = list(data.keys())
    zs = compute_day_zscores(data, date, codes, list(ALPHA_FACTOR_IDS)[:3])
    assert "alpha_mom_1m" in zs
    assert all(c in zs["alpha_mom_1m"] for c in codes)

    X = build_factor_exposures(data, date, codes)
    for fid in ("risk_mom", "risk_size", "risk_vol", "risk_beta"):
        assert fid in X.columns


def test_thin_layer1_and_engine_metrics():
    from types import SimpleNamespace

    trades = [
        SimpleNamespace(code="A", pnl=200.0),
        SimpleNamespace(code="B", pnl=-200.0),
        SimpleNamespace(code="A", pnl=100.0),
    ]
    layer = thin_layer1_attribution(trades)
    assert layer["total_pnl"] == 100.0
    assert layer["top5_winners"][0]["code"] == "A"

    df = _ohlcv(40)
    data = {"600519.SH": df, "000858.SZ": df.copy()}
    sig = pd.DataFrame(0.5, index=df.index, columns=list(data.keys()))
    cfg = BacktestConfig(
        signal_lag=0, exec_price="close", use_impact_model=False,
        reject_limit_lock=False, max_positions=5,
    )
    r = BacktestEngine(cfg).run(data, signal=sig)
    assert r["ok"]
    assert "layer1_attribution" in r["metrics"]
    assert "total_pnl" in r["metrics"]["layer1_attribution"]


def test_factor_values_bulk_write(tmp_path: Path):
    store = ResearchStore(db_path=tmp_path / "r.db")
    rows = [
        ("2024-06-03", "600519.SH", "alpha_mom_1m", 0.5, "alpha"),
        ("2024-06-03", "000858.SZ", "alpha_mom_1m", -0.2, "alpha"),
        ("2024-06-03", "600519.SH", "risk_size", 1.0, "risk"),
    ]
    assert store.upsert_factor_values(rows) == 3
    cur = store._conn().execute(
        "SELECT COUNT(*) FROM factor_values WHERE purpose='alpha'"
    )
    assert int(cur.fetchone()[0]) == 2
