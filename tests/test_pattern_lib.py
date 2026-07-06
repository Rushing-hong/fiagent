"""Tests for pattern detection library (_pattern_lib.py).

Run: python -m pytest tools/test_pattern_lib.py -v
"""

import numpy as np
import pandas as pd
import pytest

from tools._pattern_lib import (
    PatternConfig,
    candlestick_patterns,
    double_top_bottom,
    find_peaks_valleys,
    head_and_shoulders,
    support_resistance,
    trend_line_slope,
)


@pytest.fixture
def uptrend_ohlc():
    """40 days of clean uptrend OHLCV."""
    n = 40
    base = np.linspace(10, 20, n)
    noise = np.random.RandomState(42).normal(0, 0.3, n)
    close = base + noise
    return pd.DataFrame({
        "open": close - 0.2,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": np.full(n, 1e6),
    })


@pytest.fixture
def double_top_ohlc():
    """Generates a price series with a clear double top pattern."""
    prices = [10, 11, 12, 13, 14, 15, 14, 13, 15, 14, 13, 12, 11, 10, 9, 8, 9, 10, 11, 12]
    return pd.DataFrame({
        "open": [p - 0.2 for p in prices],
        "high": [p + 0.5 for p in prices],
        "low": [p - 0.5 for p in prices],
        "close": prices,
        "volume": [1e6] * len(prices),
    })


class TestPeaksValleys:
    def test_finds_peaks_in_uptrend(self, uptrend_ohlc):
        pv = find_peaks_valleys(uptrend_ohlc["close"], window=3)
        # In a pure uptrend, there should be valleys near the start
        assert len(pv["valleys"]) > 0

    def test_empty_for_short_series(self):
        s = pd.Series([10, 11, 12])
        pv = find_peaks_valleys(s, window=5)
        assert pv["peaks"] == []
        assert pv["valleys"] == []


class TestCandlestick:
    def test_returns_all_zeros_for_flat_data(self):
        n = 10
        df = pd.DataFrame({
            "open": np.full(n, 10.0),
            "high": np.full(n, 10.0),
            "low": np.full(n, 10.0),
            "close": np.full(n, 10.0),
        })
        result = candlestick_patterns(df["open"], df["high"], df["low"], df["close"])
        assert (result == 0).all()

    def test_doji_threshold_configurable(self):
        n = 5
        df = pd.DataFrame({
            "open": [10.0, 10.01, 10.02, 10.01, 10.0],
            "high": [11.0, 11.01, 11.02, 11.01, 11.0],
            "low": [9.0, 9.01, 9.02, 9.01, 9.0],
            "close": [10.01, 10.02, 10.03, 10.0, 10.01],
        })
        # With very loose threshold, everything is a doji → nothing triggers hammer/engulfing
        cfg = PatternConfig(doji_body_ratio=0.99)
        result = candlestick_patterns(df["open"], df["high"], df["low"], df["close"], cfg=cfg)
        assert len(result) == n


class TestSupportResistance:
    def test_levels_in_range(self, uptrend_ohlc):
        sr = support_resistance(uptrend_ohlc["close"], window=5, num_levels=2)
        assert "support" in sr
        assert "resistance" in sr
        # Support levels should be below current price
        if sr["support"]:
            assert all(s <= uptrend_ohlc["close"].iloc[-1] for s in sr["support"])
        # Resistance levels should be above or near
        if sr["resistance"]:
            assert all(r >= uptrend_ohlc["close"].iloc[0] for r in sr["resistance"])


class TestTrendSlope:
    def test_positive_slope_in_uptrend(self, uptrend_ohlc):
        slopes = trend_line_slope(uptrend_ohlc["close"], window=10)
        assert slopes.dropna().mean() > 0


class TestHeadAndShoulders:
    def test_no_pattern_in_uptrend(self, uptrend_ohlc):
        hs = head_and_shoulders(uptrend_ohlc["close"], window=5)
        assert hs.sum() == 0  # No H&S in clean uptrend

    def test_config_symmetry(self):
        """Looser symmetry should find more patterns (or at least not crash)."""
        s = pd.Series(np.sin(np.linspace(0, 8 * np.pi, 100)) * 5 + 50)
        hs_strict = head_and_shoulders(s, window=5, cfg=PatternConfig(hs_shoulder_symmetry=0.01))
        hs_loose = head_and_shoulders(s, window=5, cfg=PatternConfig(hs_shoulder_symmetry=0.3))
        assert hs_loose.sum() >= hs_strict.sum()


class TestDoubleTopBottom:
    def test_detects_double_top(self, double_top_ohlc):
        dtb = double_top_bottom(double_top_ohlc["close"], window=3)
        double_tops = (dtb == 1).sum()
        # Should find at least one double top in the artificial pattern
        assert double_tops >= 1

    def test_tolerance_config(self, double_top_ohlc):
        strict = double_top_bottom(double_top_ohlc["close"], window=3, cfg=PatternConfig(dtb_tolerance=0.01))
        loose = double_top_bottom(double_top_ohlc["close"], window=3, cfg=PatternConfig(dtb_tolerance=0.10))
        assert loose.sum() >= strict.sum()


class TestPatternConfig:
    def test_from_dict_merges_defaults(self):
        cfg = PatternConfig.from_dict({"doji_body_ratio": 0.05})
        assert cfg.doji_body_ratio == 0.05
        assert cfg.dtb_tolerance == 0.03  # default preserved

    def test_unknown_keys_ignored(self):
        cfg = PatternConfig.from_dict({"doji_body_ratio": 0.15, "garbage": 999})
        assert cfg.doji_body_ratio == 0.15
        # should not raise
