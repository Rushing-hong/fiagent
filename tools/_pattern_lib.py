"""Chart pattern detection functions (ported from Vibe-Trading).

All thresholds are configurable via PatternConfig. Defaults are calibrated
for daily A-share data. Adjust for weekly, intraday, or non-Chinese markets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Configurable thresholds
# ---------------------------------------------------------------------------

@dataclass
class PatternConfig:
    """Tunable parameters for all pattern detection functions.

    All ratio values are in [0, 1] unless noted otherwise.
    """

    # --- candlestick_patterns ---
    doji_body_ratio: float = 0.10       # body / range < this → doji
    hammer_shadow_ratio: float = 2.0     # lower_shadow / body > this → hammer
    hammer_upper_max: float = 1.0        # upper_shadow < body * this for hammer

    # --- support_resistance ---
    sr_cluster_pct: float = 0.05         # price range fraction for peak/valley clustering

    # --- head_and_shoulders ---
    hs_shoulder_symmetry: float = 0.05   # |left_shoulder - right_shoulder| / avg < this

    # --- double_top_bottom ---
    dtb_tolerance: float = 0.03          # |peak1 - peak2| / avg < this for double top

    # --- triangle ---
    triangle_flat_ratio: float = 0.02    # slope < range * this → "flat"

    @classmethod
    def from_dict(cls, d: dict[str, float]) -> PatternConfig:
        """Create config from a dict, filling missing keys with defaults."""
        defaults = {f.name: f.default for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        merged = {**defaults, **{k: v for k, v in d.items() if k in defaults}}
        return cls(**merged)


# ---------------------------------------------------------------------------
# Pattern detection functions
# ---------------------------------------------------------------------------

def find_peaks_valleys(close: pd.Series, window: int = 5) -> dict[str, list[int]]:
    """Detect peaks and valleys in a price series.

    Args:
        close: Closing price series.
        window: Half-window size; effective window is 2*window+1.

    Returns:
        Dict with keys "peaks" and "valleys", each a list of integer indices.
    """
    n = len(close)
    if n < 2 * window + 1:
        return {"peaks": [], "valleys": []}

    values = close.values.astype(float)
    peaks: list[int] = []
    valleys: list[int] = []

    for i in range(window, n - window):
        seg = values[i - window : i + window + 1]
        if np.isnan(values[i]):
            continue
        seg = seg[~np.isnan(seg)]
        if len(seg) == 0:
            continue
        if values[i] == np.max(seg):
            peaks.append(i)
        if values[i] == np.min(seg):
            valleys.append(i)

    return {"peaks": peaks, "valleys": valleys}


def candlestick_patterns(
    open_: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    cfg: PatternConfig | None = None,
) -> pd.Series:
    """Detect candlestick patterns: doji, hammer, and engulfing.

    Args:
        open_: Open price series.
        high: High price series.
        low: Low price series.
        close: Close price series.
        cfg: Optional threshold configuration.

    Returns:
        Series with values -1 (bearish), 0 (neutral), 1 (bullish).
    """
    if cfg is None:
        cfg = PatternConfig()

    body = (close - open_).abs()
    total_range = high - low
    upper_shadow = high - pd.concat([open_, close], axis=1).max(axis=1)
    lower_shadow = pd.concat([open_, close], axis=1).min(axis=1) - low

    result = pd.Series(0, index=close.index, dtype=int)

    safe_range = total_range.replace(0, np.nan)
    is_doji = body / safe_range < cfg.doji_body_ratio

    is_hammer = (
        (lower_shadow > cfg.hammer_shadow_ratio * body)
        & (upper_shadow < cfg.hammer_upper_max * body)
        & ~is_doji
    )
    result = result.where(~is_hammer, 1)

    prev_bearish = close.shift(1) < open_.shift(1)
    curr_bullish = close > open_
    engulf_bull = (
        prev_bearish
        & curr_bullish
        & (open_ <= close.shift(1))
        & (close >= open_.shift(1))
        & (body > body.shift(1))
    )
    result = result.where(~engulf_bull, 1)

    prev_bullish = close.shift(1) > open_.shift(1)
    curr_bearish = close < open_
    engulf_bear = (
        prev_bullish
        & curr_bearish
        & (open_ >= close.shift(1))
        & (close <= open_.shift(1))
        & (body > body.shift(1))
    )
    result = result.where(~engulf_bear, -1)

    return result


def support_resistance(
    close: pd.Series,
    window: int = 20,
    num_levels: int = 3,
    cfg: PatternConfig | None = None,
) -> dict[str, list[float]]:
    """Compute support and resistance levels via peak/valley clustering.

    Args:
        close: Closing price series.
        window: Peak/valley detection window.
        num_levels: Maximum number of levels to return.
        cfg: Optional threshold configuration.

    Returns:
        Dict with keys "support" and "resistance", each a list of price levels.
    """
    if cfg is None:
        cfg = PatternConfig()

    pv = find_peaks_valleys(close, window=window)
    values = close.values.astype(float)

    peak_prices = [float(values[i]) for i in pv["peaks"] if not np.isnan(values[i])]
    valley_prices = [float(values[i]) for i in pv["valleys"] if not np.isnan(values[i])]

    def cluster(prices: list[float], n: int) -> list[float]:
        if not prices:
            return []
        sp = sorted(prices)
        if len(sp) <= n:
            return sp
        clusters: list[list[float]] = [[sp[0]]]
        rng = sp[-1] - sp[0]
        thr = rng * cfg.sr_cluster_pct if rng > 0 else 1.0
        for p in sp[1:]:
            if abs(p - float(np.mean(clusters[-1]))) <= thr:
                clusters[-1].append(p)
            else:
                clusters.append([p])
        centers = [(len(c), float(np.mean(c))) for c in clusters]
        centers.sort(reverse=True)
        return [c for _, c in centers[:n]]

    return {"support": cluster(valley_prices, num_levels), "resistance": cluster(peak_prices, num_levels)}


def trend_line_slope(close: pd.Series, window: int = 20) -> pd.Series:
    """Compute rolling linear-fit slope.

    Args:
        close: Closing price series.
        window: Fitting window size.

    Returns:
        Series of slope values; first window-1 entries are NaN.
    """
    n = len(close)
    slopes = np.full(n, np.nan)
    values = close.values.astype(float)
    x = np.arange(window, dtype=float)

    for i in range(window - 1, n):
        seg = values[i - window + 1 : i + 1]
        if np.any(np.isnan(seg)):
            continue
        slopes[i] = np.polyfit(x, seg, 1)[0]

    return pd.Series(slopes, index=close.index)


def head_and_shoulders(
    close: pd.Series,
    window: int = 10,
    cfg: PatternConfig | None = None,
) -> pd.Series:
    """Detect head-and-shoulders top pattern.

    Args:
        close: Closing price series.
        window: Peak/valley detection window.
        cfg: Optional threshold configuration.

    Returns:
        Series with 1 where pattern is detected, 0 otherwise.
    """
    if cfg is None:
        cfg = PatternConfig()

    result = pd.Series(0, index=close.index, dtype=int)
    pv = find_peaks_valleys(close, window=window)
    peaks = pv["peaks"]
    values = close.values.astype(float)

    if len(peaks) < 3:
        return result

    for i in range(len(peaks) - 2):
        lv, hv, rv = values[peaks[i]], values[peaks[i + 1]], values[peaks[i + 2]]
        if any(np.isnan(x) for x in (lv, hv, rv)):
            continue
        if hv <= lv or hv <= rv:
            continue
        avg = (lv + rv) / 2
        if avg == 0 or abs(lv - rv) / avg > cfg.hs_shoulder_symmetry:
            continue
        result.iloc[peaks[i + 1]] = 1

    return result


def double_top_bottom(
    close: pd.Series,
    window: int = 10,
    cfg: PatternConfig | None = None,
) -> pd.Series:
    """Detect double-top and double-bottom patterns.

    Args:
        close: Closing price series.
        window: Peak/valley detection window.
        cfg: Optional threshold configuration.

    Returns:
        Series with 1 (double top), -1 (double bottom), or 0 (none).
    """
    if cfg is None:
        cfg = PatternConfig()

    result = pd.Series(0, index=close.index, dtype=int)
    pv = find_peaks_valleys(close, window=window)
    values = close.values.astype(float)

    tol = cfg.dtb_tolerance

    for i in range(len(pv["peaks"]) - 1):
        v1, v2 = values[pv["peaks"][i]], values[pv["peaks"][i + 1]]
        if np.isnan(v1) or np.isnan(v2):
            continue
        avg = (v1 + v2) / 2
        if avg != 0 and abs(v1 - v2) / avg < tol:
            result.iloc[pv["peaks"][i + 1]] = 1

    for i in range(len(pv["valleys"]) - 1):
        v1, v2 = values[pv["valleys"][i]], values[pv["valleys"][i + 1]]
        if np.isnan(v1) or np.isnan(v2):
            continue
        avg = (v1 + v2) / 2
        if avg != 0 and abs(v1 - v2) / abs(avg) < tol:
            if result.iloc[pv["valleys"][i + 1]] == 0:
                result.iloc[pv["valleys"][i + 1]] = -1

    return result


def triangle(
    close: pd.Series,
    window: int = 20,
    cfg: PatternConfig | None = None,
) -> pd.Series:
    """Detect triangle patterns.

    Args:
        close: Closing price series.
        window: Detection window size.
        cfg: Optional threshold configuration.

    Returns:
        Series with 1 (ascending triangle), -1 (descending triangle), or 0 (none).
    """
    if cfg is None:
        cfg = PatternConfig()

    n = len(close)
    result = pd.Series(0, index=close.index, dtype=int)
    values = close.values.astype(float)

    for i in range(window, n):
        seg = pd.Series(values[i - window : i + 1])
        pv = find_peaks_valleys(seg, window=max(2, window // 5))
        if len(pv["peaks"]) < 2 or len(pv["valleys"]) < 2:
            continue
        pvals = [float(seg.iloc[p]) for p in pv["peaks"]]
        vvals = [float(seg.iloc[v]) for v in pv["valleys"]]
        ps = np.polyfit(np.arange(len(pvals), dtype=float), pvals, 1)[0] if len(pvals) >= 2 else 0.0
        vs = np.polyfit(np.arange(len(vvals), dtype=float), vvals, 1)[0] if len(vvals) >= 2 else 0.0
        rng = max(pvals) - min(vvals)
        if rng == 0:
            continue
        flat = rng * cfg.triangle_flat_ratio
        if vs > flat and abs(ps) < flat:
            result.iloc[i] = 1
        elif ps < -flat and abs(vs) < flat:
            result.iloc[i] = -1

    return result


def broadening(close: pd.Series, window: int = 20) -> pd.Series:
    """Detect broadening (megaphone) patterns.

    Args:
        close: Closing price series.
        window: Detection window size.

    Returns:
        Series with 1 where broadening pattern is detected, 0 otherwise.
    """
    n = len(close)
    result = pd.Series(0, index=close.index, dtype=int)
    values = close.values.astype(float)

    for i in range(window, n):
        seg = pd.Series(values[i - window : i + 1])
        pv = find_peaks_valleys(seg, window=max(2, window // 5))
        if len(pv["peaks"]) < 2 or len(pv["valleys"]) < 2:
            continue
        pvals = [float(seg.iloc[p]) for p in pv["peaks"]]
        vvals = [float(seg.iloc[v]) for v in pv["valleys"]]
        peaks_rising = all(pvals[j + 1] > pvals[j] for j in range(len(pvals) - 1))
        valleys_falling = all(vvals[j + 1] < vvals[j] for j in range(len(vvals) - 1))
        if peaks_rising and valleys_falling:
            result.iloc[i] = 1

    return result


# ---------------------------------------------------------------------------
# Available pattern registry  (backward-compatible lambda-based interface
# uses default PatternConfig; the PatternTool passes window only)
# ---------------------------------------------------------------------------

_PATTERN_FUNCS = {
    "peaks_valleys": lambda df, w: find_peaks_valleys(df["close"], window=w),
    "candlestick": lambda df, w: candlestick_patterns(
        df["open"], df["high"], df["low"], df["close"]
    ).value_counts().to_dict(),
    "support_resistance": lambda df, w: support_resistance(df["close"], window=w),
    "trend_slope": lambda df, w: (
        {"mean_slope": float(trend_line_slope(df["close"], window=w).dropna().mean())}
        if len(df) > w else {"mean_slope": 0}
    ),
    "head_and_shoulders": lambda df, w: {
        "count": int(head_and_shoulders(df["close"], window=w).sum())
    },
    "double_top_bottom": lambda df, w: {
        "double_top": int((double_top_bottom(df["close"], window=w) == 1).sum()),
        "double_bottom": int((double_top_bottom(df["close"], window=w) == -1).sum()),
    },
    "triangle": lambda df, w: {
        "ascending": int((triangle(df["close"], window=w) == 1).sum()),
        "descending": int((triangle(df["close"], window=w) == -1).sum()),
    },
    "broadening": lambda df, w: {
        "count": int(broadening(df["close"], window=w).sum())
    },
}
