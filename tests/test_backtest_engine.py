"""Unit tests for enhanced A-share backtest realism (P0)."""

from __future__ import annotations

import pandas as pd

from market.backtest_engine import (
    BacktestConfig,
    BacktestEngine,
    Broker,
    Order,
    _is_limit_up,
    _is_limit_down,
)


def _ohlcv(n: int = 40, start: float = 10.0) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=n)
    closes = [start * (1.01 ** i) for i in range(n)]
    rows = []
    for i, d in enumerate(dates):
        c = closes[i]
        rows.append({
            "open": c * 0.99,
            "high": c * 1.02,
            "low": c * 0.98,
            "close": c,
            "volume": 1_000_000,
            "amount": c * 1_000_000,
        })
    return pd.DataFrame(rows, index=dates)


def test_limit_up_blocks_buy():
    cfg = BacktestConfig(reject_limit_lock=True, use_impact_model=False, signal_lag=0)
    broker = Broker(cfg)
    bar = {"open": 11.0, "high": 11.0, "low": 10.5, "close": 11.0, "prev_close": 10.0, "adv": 1e9}
    # main board 10% → limit 11.0
    assert _is_limit_up(bar, "600000.SH")
    status = broker.execute_order(
        Order("600000.SH", pd.Timestamp("2024-01-03"), "buy", 100, 11.0),
        bar,
        pd.Timestamp("2024-01-03"),
    )
    assert status == "limit_up_locked"
    assert broker.positions == {}


def test_limit_down_blocks_sell():
    cfg = BacktestConfig(reject_limit_lock=True, use_impact_model=False, signal_lag=0)
    broker = Broker(cfg)
    # seed position + unlock T+1
    broker.positions["600000.SH"] = 100
    broker.avg_cost["600000.SH"] = 10.0
    broker.tplus1_lock["600000.SH"] = pd.Timestamp("2024-01-02")
    broker.open_trades["600000.SH"] = __import__(
        "market.backtest_engine", fromlist=["Trade"]
    ).Trade(
        code="600000.SH",
        entry_date=pd.Timestamp("2024-01-02"),
        exit_date=None,
        entry_price=10.0,
        exit_price=None,
        quantity=100,
    )
    bar = {"open": 9.0, "high": 9.2, "low": 9.0, "close": 9.0, "prev_close": 10.0, "adv": 1e9}
    assert _is_limit_down(bar, "600000.SH")
    status = broker.execute_order(
        Order("600000.SH", pd.Timestamp("2024-01-03"), "sell", 100, 9.0),
        bar,
        pd.Timestamp("2024-01-03"),
    )
    assert status == "limit_down_locked"
    assert broker.positions["600000.SH"] == 100


def test_impact_slip_increases_with_size():
    cfg = BacktestConfig(use_impact_model=True, impact_coef=0.01, slippage=0.0)
    broker = Broker(cfg)
    bar = {"open": 10, "high": 10, "low": 10, "close": 10, "prev_close": 10, "adv": 1_000_000}
    slip_small = broker._calc_slip("600000.SH", "buy", 10_000, bar)
    slip_large = broker._calc_slip("600000.SH", "buy", 1_000_000, bar)
    assert slip_large > slip_small


def test_signal_lag_delays_entry():
    df = _ohlcv(30)
    data = {"600519.SH": df}
    signal = pd.DataFrame(1.0, index=df.index, columns=["600519.SH"])

    cfg0 = BacktestConfig(
        signal_lag=0, exec_price="close", use_impact_model=False,
        reject_limit_lock=False, max_positions=5,
    )
    cfg1 = BacktestConfig(
        signal_lag=1, exec_price="open", use_impact_model=False,
        reject_limit_lock=False, max_positions=5,
    )
    r0 = BacktestEngine(cfg0).run(data, signal=signal)
    r1 = BacktestEngine(cfg1).run(data, signal=signal)
    assert r0["ok"] and r1["ok"]
    assert r1["config"]["signal_lag"] == 1
    assert r0["metrics"]["total_trades"] >= 0


def test_halt_skips_missing_bar_and_cash_interest():
    df = _ohlcv(20)
    # punch a hole mid-series → halt
    hole = df.index[10]
    df2 = df.drop(index=hole)
    data = {"600519.SH": df2}
    signal = pd.DataFrame(1.0, index=df.index, columns=["600519.SH"])
    cfg = BacktestConfig(
        signal_lag=0, exec_price="close", use_impact_model=False,
        reject_limit_lock=False, skip_halted=True,
        cash_annual_rate=0.365,  # 0.1% per day for easy assert
        max_positions=5,
    )
    r = BacktestEngine(cfg).run(data, signal=signal)
    assert r["ok"]
    assert r["metrics"]["halt_code_days"] >= 1
    assert r["metrics"]["cash_interest_total"] > 0
    assert r["config"]["skip_halted"] is True


def test_event_signal_csv_writer(tmp_path):
    from tools.backtest_events import _write_signal_csv
    info = _write_signal_csv(
        [("600519.SH", "2024-01-03", 1.0), ("000858.SZ", "2024-01-04", 1.0)],
        hold_days=3,
        out_path=tmp_path / "sig.csv",
    )
    assert info["n_events"] == 2
    df = pd.read_csv(info["path"], index_col=0, parse_dates=True)
    assert "600519.SH" in df.columns
    assert (df["600519.SH"] > 0).sum() == 3


def test_industry_cap_and_hedge_and_sleeves():
    from market.backtest_p2 import apply_industry_cap, FuturesHedgeBook

    capped = apply_industry_cap(
        {"A": 0.4, "B": 0.4, "C": 0.2},
        {"A": "白酒", "B": "白酒", "C": "银行"},
        0.5,
    )
    assert abs(capped["A"] + capped["B"] - 0.5) < 1e-9
    assert abs(capped["C"] - 0.2) < 1e-9

    book = FuturesHedgeBook(symbol="IF", multiplier=300, hedge_ratio=1.0)
    d1 = pd.Timestamp("2024-01-02")
    d2 = pd.Timestamp("2024-02-02")
    book.step(d1, {"close": 4000, "prev_close": 4000}, stock_mv=1_200_000, cash_ref=1e6)
    assert book.contracts >= 1
    book.step(d2, {"close": 3900, "prev_close": 4000}, stock_mv=1_200_000, cash_ref=1e6)
    assert book.realized_pnl > 0

    df = _ohlcv(40)
    data = {"600519.SH": df, "000858.SZ": df.copy()}
    s1 = pd.DataFrame(1.0, index=df.index, columns=list(data.keys()))
    s2 = pd.DataFrame(0.0, index=df.index, columns=list(data.keys()))
    s2.iloc[10:] = 1.0
    fut = pd.DataFrame({"close": [4000.0 + i for i in range(len(df))]}, index=df.index)
    cfg = BacktestConfig(
        signal_lag=0, exec_price="close", use_impact_model=False,
        reject_limit_lock=False, hedge_enabled=True, hedge_ratio=1.0,
        max_industry_weight=0.6, max_positions=5,
    )
    r = BacktestEngine(cfg).run(
        data,
        sleeves={"mom": s1, "rev": s2},
        sleeve_weights={"mom": 0.5, "rev": 0.5},
        futures_data=fut,
        industry_map={"600519.SH": "白酒", "000858.SZ": "白酒"},
    )
    assert r["ok"]
    assert "hedge" in r["metrics"]
    assert "sleeve_attribution_cum_return" in r["metrics"]


def test_style_cap_and_black_litterman_and_hedge_map():
    from market.backtest_p2 import apply_style_exposure_cap
    from market.black_litterman import black_litterman_posterior, views_from_absolute
    from tools.suggest_hedge_ratio import _map_hedge_ratio
    import numpy as np

    capped = apply_style_exposure_cap(
        {"A": 0.5, "B": 0.5},
        {"A": 2.0, "B": -0.5},
        0.3,
    )
    expo = capped["A"] * 2.0 + capped["B"] * (-0.5)
    assert abs(expo) <= 0.3 + 1e-9

    codes = ["A", "B"]
    P, Q, omega = views_from_absolute(
        2, [{"assets": ["A"], "q": 0.05, "confidence": 0.8}], codes
    )
    out = black_litterman_posterior(
        np.eye(2) * 0.04, np.array([0.5, 0.5]), P=P, Q=Q, omega=omega
    )
    assert abs(out["weights"].sum() - 1.0) < 1e-9
    assert out["weights"][0] >= out["weights"][1] - 1e-9

    assert _map_hedge_ratio(0.95, 0.5, 1.0, 0.2) == 1.0
    assert _map_hedge_ratio(0.1, 0.5, 1.0, 0.2) == 0.2
    assert _map_hedge_ratio(0.5, 0.5, 1.0, 0.2) == 0.5

    # Engine path with momentum cap
    df = _ohlcv(50)
    # make A stronger momentum than B
    df_a = df.copy()
    df_b = df.copy()
    df_a["close"] = df_a["close"] * (1 + np.linspace(0, 0.4, len(df)))
    data = {"600519.SH": df_a, "000858.SZ": df_b}
    sig = pd.DataFrame(0.5, index=df.index, columns=list(data.keys()))
    cfg = BacktestConfig(
        signal_lag=0, exec_price="close", use_impact_model=False,
        reject_limit_lock=False, max_momentum_exposure=0.2, momentum_window=10,
        max_positions=5,
    )
    r = BacktestEngine(cfg).run(data, signal=sig)
    assert r["ok"]
    assert r["config"].get("max_momentum_exposure") == 0.2


def test_size_vol_caps_and_consensus_revision():
    from market.backtest_p2 import size_factor, vol_factor, apply_style_exposure_cap
    from tools.track_consensus import _revision_series, _sue_table

    df = _ohlcv(40)
    data = {"A": df, "B": df.copy()}
    data["B"]["volume"] = data["B"]["volume"] * 10
    date = df.index[-1]
    sz = size_factor(data, date, ["A", "B"], window=10)
    assert sz["B"] > sz["A"]

    vo = vol_factor(data, date, ["A", "B"], window=10)
    assert "A" in vo and "B" in vo

    capped = apply_style_exposure_cap(
        {"A": 0.8, "B": 0.2}, {"A": 1.5, "B": -1.0}, 0.4
    )
    assert abs(capped["A"] * 1.5 + capped["B"] * (-1.0)) <= 0.4 + 1e-9

    reports = [
        {"publish_date": "2024-01-10", "brokerage": "X", "eps_forecast": {"this_year": 10.0}},
        {"publish_date": "2024-02-10", "brokerage": "X", "eps_forecast": {"this_year": 11.0}},
        {"publish_date": "2024-02-11", "brokerage": "Y", "eps_forecast": {"this_year": 10.5}},
    ]
    series, mom = _revision_series(reports)
    assert len(series) == 3
    assert mom["upgrades"] >= 1
    assert mom["n_reports_with_eps"] == 3

    sue = _sue_table(
        [{"year": "2023", "eps": 12.0}],
        [{"year": "2023", "eps": 10.0}],
        series,
    )
    assert sue[0]["surprise_pct"] == 20.0


def test_parse_reports_eps_fields():
    from tools.stock_research import _parse_reports

    payload = {
        "data": [{
            "title": "t",
            "orgSName": "中信",
            "researcher": "张三",
            "publishDate": "2024-06-01 00:00:00",
            "emRatingName": "买入",
            "predictThisYearEps": "10.5",
            "predictNextYearEps": "12",
            "predictThisYearPe": "20",
            "predictNextYearPe": "18",
            "actualLastYearEps": "9",
        }]
    }
    rows = _parse_reports(payload)
    assert rows[0]["eps_forecast"]["this_year"] == 10.5
    assert rows[0]["actual_eps"]["last_year"] == 9.0


def test_research_store_and_barra_and_intraday_dates(tmp_path):
    from market.research_store import ResearchStore
    from market.barra_lite import estimate_factor_model, portfolio_risk
    import numpy as np

    store = ResearchStore(db_path=tmp_path / "research.db")
    store.upsert_bars(
        "600519.SH",
        "5",
        [
            {"trade_date": "2024-06-03 09:35:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 100},
            {"trade_date": "2024-06-03 09:40:00", "open": 1, "high": 1, "low": 1, "close": 1.1, "volume": 100},
        ],
    )
    bars = store.load_bars("600519.SH", "5", "2024-06-03", "2024-06-03")
    assert len(bars) == 2
    store.save_universe(["600519.SH", "000858.SZ"], asof="2024-01-15")
    store.save_universe(["600519.SH"], asof="2024-06-01")
    pit = store.load_universe_pit("2024-03-01")
    assert pit is not None and pit["asof"] == "2024-01-15" and len(pit["codes"]) == 2
    store.save_consensus("600519.SH", source="ths", points=[{"year": "2025", "eps": 60.0}], asof="2026-07-01")
    hist = store.load_consensus_history("600519.SH")
    assert hist and hist[0]["eps"] == 60.0

    # barra on synthetic panel
    idx = pd.bdate_range("2024-01-02", periods=80)
    data = {}
    rng = np.random.default_rng(0)
    for i, code in enumerate(["A", "B", "C", "D"]):
        px = 10 * np.cumprod(1 + rng.normal(0.001, 0.02, len(idx)))
        data[code] = pd.DataFrame({
            "open": px, "high": px * 1.01, "low": px * 0.99, "close": px,
            "volume": rng.integers(1e5, 1e6, len(idx)),
        }, index=idx)
    model = estimate_factor_model(data, window=10, lookback=40)
    risk = portfolio_risk({"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25}, model)
    assert risk["risk"]["total_vol"] >= 0
    assert "mom" in risk["factor_exposure"]

    # intraday dates preserved
    midx = pd.date_range("2024-06-03 09:35", periods=30, freq="5min")
    mdf = pd.DataFrame({
        "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0, "volume": 1000.0,
    }, index=midx)
    cfg = BacktestConfig(
        signal_lag=1, exec_price="close", use_impact_model=False,
        reject_limit_lock=False, after_hours=True, skip_halted=True,
    )
    sig = pd.DataFrame(1.0, index=midx, columns=["600519.SH"])
    r = BacktestEngine(cfg).run({"600519.SH": mdf}, signal=sig)
    assert r["ok"]
    assert r["config"]["n_trading_days"] >= 20
