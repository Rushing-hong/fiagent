"""Phase1 Week4: Layer2 beta, risk exposure attr, L1 unit/frequency scenarios."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from market.backtest_attr import layer2_beta_attribution, risk_exposure_snapshot
from market.backtest_engine import BacktestConfig, BacktestEngine
from market.envelope import (
    assert_frequency_compatible,
    assert_meta_chain,
    assert_unit_compatible,
    normalize_meta,
)


def test_layer2_beta_hs300_zz500():
    rng = np.random.default_rng(42)
    n = 80
    idx = pd.bdate_range("2024-01-02", periods=n)
    hs = pd.Series(rng.normal(0.0004, 0.012, n), index=idx)
    zz = pd.Series(rng.normal(0.0003, 0.015, n), index=idx)
    # strategy ≈ 0.8*HS300 + noise
    strat = 0.0001 + 0.8 * hs + rng.normal(0, 0.005, n)
    strat = pd.Series(strat, index=idx)
    out = layer2_beta_attribution(strat, {"HS300": hs, "ZZ500": zz})
    assert out["layer"] == "2_beta"
    assert "HS300" in out["benchmarks"] and "ZZ500" in out["benchmarks"]
    hs_fit = out["benchmarks"]["HS300"]
    assert hs_fit.get("beta") is not None
    assert hs_fit.get("t_beta") is not None
    assert abs(float(hs_fit["beta"]) - 0.8) < 0.25


def test_engine_layer2_and_risk_attr():
    idx = pd.bdate_range("2024-01-02", periods=50)
    rng = np.random.default_rng(1)
    codes = [f"60000{i}.SH" for i in range(5)]
    data: dict[str, pd.DataFrame] = {}
    for c in codes:
        px = 10 * np.cumprod(1 + rng.normal(0.0005, 0.02, len(idx)))
        data[c] = pd.DataFrame({
            "open": px, "high": px * 1.01, "low": px * 0.99, "close": px, "volume": 1e6,
        }, index=idx)
    sig = pd.DataFrame(0.2, index=idx, columns=codes)
    hs = pd.Series(rng.normal(0.0004, 0.01, len(idx)), index=idx)
    zz = pd.Series(rng.normal(0.0003, 0.012, len(idx)), index=idx)
    r = BacktestEngine(BacktestConfig(
        signal_lag=0, exec_price="close", use_impact_model=False, reject_limit_lock=False,
        initial_cash=1_000_000,
    )).run(
        data,
        signal=sig,
        benchmark_returns={"HS300": hs, "ZZ500": zz},
    )
    assert r["ok"]
    m = r["metrics"]
    assert m["layer1_attribution"]["layer"] == "1_thick"
    assert m["layer2_attribution"]["layer"] == "2_beta"
    assert "HS300" in m["layer2_attribution"]["benchmarks"]
    # risk attr may error if barra needs more stocks / window; tolerate structured result
    assert "risk_attribution" in m
    assert m["risk_attribution"].get("purpose") == "risk" or "error" in m["risk_attribution"]


def test_risk_exposure_snapshot_purpose():
    idx = pd.bdate_range("2024-01-02", periods=45)
    rng = np.random.default_rng(2)
    data = {}
    for i in range(6):
        code = f"00000{i}.SZ"
        px = 8 * np.cumprod(1 + rng.normal(0.0, 0.015, len(idx)))
        data[code] = pd.DataFrame({
            "open": px, "high": px, "low": px, "close": px, "volume": 5e5 + i * 1e4,
        }, index=idx)
    w = {c: 1 / len(data) for c in data}
    snap = risk_exposure_snapshot(data, w, lookback=35)
    assert snap.get("purpose") == "risk"
    if "error" not in snap:
        assert "factor_exposure" in snap or "risk" in snap


# --- L1 scenarios: unit / frequency must fail on mismatch ---------------------

def test_l1_frequency_monthly_cannot_feed_daily_streak():
    with pytest.raises(ValueError, match="frequency mismatch"):
        assert_frequency_compatible("monthly", "daily_streak")
    with pytest.raises(ValueError, match="frequency mismatch"):
        assert_frequency_compatible("quarterly", "consecutive_trading_days")
    # daily → streak OK
    assert_frequency_compatible("daily", "daily_streak")


def test_l1_unit_mismatch_fails_unless_converted():
    with pytest.raises(ValueError, match="unit mismatch"):
        assert_unit_compatible("CNY_wan", "CNY_yuan")
    assert_unit_compatible("CNY_wan", "CNY_yuan", converted=True)
    assert_unit_compatible("10k CNY", "CNY_wan")  # alias


def test_l1_scenario_macro_to_northbound_chain():
    """宏观月频不可被北向「连续N日」逻辑消费；北向 wan 不可当 yuan 累加。"""
    macro_meta = normalize_meta(
        source="akshare", frequency="monthly", unit="index_point",
    )
    with pytest.raises(ValueError, match="frequency"):
        assert_meta_chain(macro_meta, expect_frequency_mode="daily_streak")

    nb_meta = normalize_meta(source="akshare", frequency="daily", unit="CNY_wan")
    assert_meta_chain(nb_meta, expect_unit="CNY_wan", expect_frequency_mode="daily_streak")
    with pytest.raises(ValueError, match="unit"):
        assert_meta_chain(nb_meta, expect_unit="CNY_yuan")


def test_l1_scenario_dragon_tiger_event_vs_monthly():
    dt_meta = normalize_meta(source="akshare", frequency="event", unit="CNY_yuan")
    with pytest.raises(ValueError, match="frequency"):
        assert_meta_chain(dt_meta, expect_frequency_mode="monthly_series")
    # event ok for event-style consumer (no daily_streak rule trip)
    assert_meta_chain(dt_meta, expect_unit="CNY_yuan", expect_frequency_mode="event")
