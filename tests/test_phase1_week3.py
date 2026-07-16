"""Phase1 Week3: seats, northbound signal helpers, VaR/stress, Layer1 thick."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from market.backtest_attr import thick_layer1_attribution
from market.backtest_engine import BacktestConfig, BacktestEngine
from market.research_store import ResearchStore
from market.risk_metrics import apply_stress_shocks, historical_var, parametric_var
from market.seat_classify import aggregate_by_type, classify_seat
from tools.northbound_signal import _signalize


def test_seat_classify_heuristics():
    assert classify_seat("机构专用") == "institution"
    assert classify_seat("某某量化私募") == "quant"
    assert classify_seat("华泰证券深圳益田路营业部") == "hot_money"
    from market.seat_classify import enrich_seats
    seats = [
        {"seat": "机构专用", "buy": 1e6, "sell": 0, "net": 1e6},
        {"seat": "拉萨团结路", "buy": 2e6, "sell": 5e5, "net": 1.5e6},
    ]
    enriched = enrich_seats(seats)
    assert enriched[0]["seat_type"] == "institution"
    agg = aggregate_by_type(enriched)
    assert "institution" in agg and agg["institution"]["net"] == 1e6


def test_northbound_signalize():
    hist = [
        {"date": f"2024-01-{d:02d}", "total": 10.0 if d < 5 else -5.0}
        for d in range(1, 10)
    ]
    # rewrite with streak of inflows at end
    hist = [{"date": f"2024-02-{d:02d}", "total": float(d)} for d in range(1, 8)]
    sig = _signalize(hist, streak_n=3)
    assert sig["streak_signal"] == "inflow_streak"
    assert sig["unit"] == "CNY_wan"


def test_var_and_stress():
    rng = np.random.default_rng(0)
    rets = rng.normal(0.0005, 0.02, 120)
    h = historical_var(rets, alpha=0.05)
    p = parametric_var(rets, alpha=0.05)
    assert h["var"] > 0 and p["var"] > 0
    shocks = apply_stress_shocks(1_000_000, scenarios=["2015_crash", "2020_covid"])
    assert len(shocks) == 2
    assert shocks[0]["equity_after"] < 1_000_000


def test_thick_layer1_and_engine():
    trades = [
        SimpleNamespace(
            code="A", pnl=500.0, exit_reason="signal_exit",
            entry_date=pd.Timestamp("2024-01-02"), exit_date=pd.Timestamp("2024-01-03"),
        ),
        SimpleNamespace(
            code="B", pnl=-100.0, exit_reason="signal_exit",
            entry_date=pd.Timestamp("2024-01-02"), exit_date=pd.Timestamp("2024-02-01"),
        ),
        SimpleNamespace(
            code="C", pnl=50.0, exit_reason="signal_exit",
            entry_date=pd.Timestamp("2024-01-02"), exit_date=pd.Timestamp("2024-01-10"),
        ),
    ]
    layer = thick_layer1_attribution(trades)
    assert layer["layer"] == "1_thick"
    assert "holding_buckets" in layer
    assert "pnl_excluding_top5_winners" in layer
    assert layer["exit_reason_count"]["signal_exit"] == 3

    idx = pd.bdate_range("2024-01-02", periods=40)
    px = np.linspace(10, 12, 40)
    df = pd.DataFrame({
        "open": px, "high": px, "low": px, "close": px, "volume": 1e6,
    }, index=idx)
    data = {"600519.SH": df, "000858.SZ": df.copy()}
    sig = pd.DataFrame(0.5, index=idx, columns=list(data.keys()))
    r = BacktestEngine(BacktestConfig(
        signal_lag=0, exec_price="close", use_impact_model=False, reject_limit_lock=False,
    )).run(data, signal=sig)
    assert r["ok"]
    assert r["metrics"]["layer1_attribution"]["layer"] == "1_thick"


def test_micro_signal_store(tmp_path: Path):
    store = ResearchStore(db_path=tmp_path / "r.db")
    n = store.upsert_micro_signals([{
        "asof": "2024-06-03",
        "code": "_NORTHBOUND_",
        "signal_id": "northbound_total_wan",
        "value": 12.5,
        "unit": "CNY_wan",
        "meta_json": {"strength": "high"},
    }])
    assert n == 1
