"""A股交易所交易日历（基建模块，供回测/因子/宏观对齐消费）。

数据源：akshare `tool_trade_date_hist_sina`，缓存进 research.db。
"""

from __future__ import annotations

import bisect
import logging
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Iterable

import pandas as pd

logger = logging.getLogger(__name__)


def _to_ymd(d: date | datetime | pd.Timestamp | str) -> str:
    if isinstance(d, str):
        return d[:10]
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%d")
    if isinstance(d, pd.Timestamp):
        return d.strftime("%Y-%m-%d")
    return d.isoformat()


def refresh_calendar_cache(*, force: bool = False) -> int:
    """拉取并写入 research.db，返回交易日条数。"""
    from market.research_store import get_store

    store = get_store()
    if not force:
        n = store.count_trade_days()
        if n > 1000:
            return n
    try:
        import akshare as ak

        df = ak.tool_trade_date_hist_sina()
    except Exception as exc:
        logger.warning("trade calendar fetch failed: %s", exc)
        return store.count_trade_days()
    if df is None or getattr(df, "empty", True):
        return store.count_trade_days()
    col = "trade_date" if "trade_date" in df.columns else df.columns[0]
    days = sorted({_to_ymd(x) for x in df[col].tolist() if x is not None})
    store.replace_trade_calendar(days)
    invalidate_calendar_cache()
    return len(days)


@lru_cache(maxsize=1)
def _cached_set() -> frozenset[str]:
    from market.research_store import get_store

    store = get_store()
    if store.count_trade_days() < 100:
        refresh_calendar_cache(force=True)
    return frozenset(store.load_trade_days())


@lru_cache(maxsize=1)
def _cached_days() -> tuple[str, ...]:
    return tuple(sorted(_cached_set()))


def invalidate_calendar_cache() -> None:
    _cached_set.cache_clear()
    _cached_days.cache_clear()


def is_trading_day(d: date | datetime | pd.Timestamp | str) -> bool:
    ymd = _to_ymd(d)
    try:
        cached = _cached_set()
        if cached:
            return ymd in cached
    except Exception as exc:
        logger.warning("trade calendar cache read failed: %s", exc)
    # 无可用交易所日历时不猜工作日（避免把法定节假日当交易日）
    logger.warning(
        "trade calendar empty/unavailable; treating %s as non-trading day",
        ymd,
    )
    return False


def trading_days(start: str, end: str) -> list[str]:
    """返回 [start, end] 内交易所交易日。日历不可用时返回空列表（不再 bdate_range 冒充）。"""
    s, e = _to_ymd(start), _to_ymd(end)
    try:
        ordered = _cached_days()
        left = bisect.bisect_left(ordered, s)
        right = bisect.bisect_right(ordered, e)
        days = list(ordered[left:right])
        if days:
            return days
    except Exception as exc:
        logger.warning("trade calendar range failed: %s", exc)
    logger.warning(
        "trade calendar empty for %s..%s; returning [] (no weekday fallback)",
        s,
        e,
    )
    return []


def trading_days_index(start: str, end: str) -> pd.DatetimeIndex:
    days = trading_days(start, end)
    return pd.DatetimeIndex(pd.to_datetime(days))


def align_dates_to_trading(
    dates: Iterable[pd.Timestamp | str],
    *,
    start: str | None = None,
    end: str | None = None,
) -> list[pd.Timestamp]:
    """Intersect given timestamps with exchange calendar (date-normalized)."""
    raw = [pd.Timestamp(d).normalize() for d in dates]
    if not raw:
        return []
    s = start or min(raw).strftime("%Y-%m-%d")
    e = end or max(raw).strftime("%Y-%m-%d")
    allowed = set(trading_days(s, e))
    out = [d for d in sorted(set(raw)) if d.strftime("%Y-%m-%d") in allowed]
    return out


def next_trading_day(d: str | date | datetime, *, n: int = 1) -> str | None:
    ymd = _to_ymd(d)
    days = _cached_days()
    index = bisect.bisect_right(days, ymd) + n - 1
    if n < 1 or index >= len(days):
        return None
    return days[index]


def prev_trading_day(d: str | date | datetime, *, n: int = 1) -> str | None:
    ymd = _to_ymd(d)
    days = _cached_days()
    index = bisect.bisect_left(days, ymd) - n
    if n < 1 or index < 0:
        return None
    return days[index]


def session_hint(d: str | date | datetime | None = None) -> dict[str, object]:
    """Rough session info for Agent (half-day not yet modeled from free source)."""
    ymd = _to_ymd(d or datetime.now())
    open_ = is_trading_day(ymd)
    return {
        "date": ymd,
        "is_trading_day": open_,
        "note": "半日市/临时休市需人工核对；本日历来自新浪交易日历史。",
    }
