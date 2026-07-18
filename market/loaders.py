"""Optional A-share OHLCV loaders: mootdx, baostock, akshare."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _rows_from_df(df, date_col: str = "trade_date") -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    out: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        if date_col in df.columns:
            td = str(row[date_col])[:10]
        else:
            td = str(idx)[:10]
        out.append({
            "trade_date": td,
            "open": float(row.get("open", row.get("开盘", 0))),
            "close": float(row.get("close", row.get("收盘", 0))),
            "high": float(row.get("high", row.get("最高", 0))),
            "low": float(row.get("low", row.get("最低", 0))),
            "volume": float(row.get("volume", row.get("成交量", 0))),
        })
    return out


def fetch_mootdx(code: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    try:
        from mootdx.quotes import Quotes
    except ImportError:
        return []

    bare = code.split(".")[0]
    upper = code.upper()
    if (
        upper.endswith(".BJ")
        or (len(bare) == 6 and bare[0] in ("4", "8"))
        or bare.startswith("92")
    ):
        return []

    try:
        client = Quotes.factory(market="std")
        df = client.get_k_data(code=bare, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return []
        df = df.rename(columns={
            "date": "trade_date",
            "open": "open",
            "close": "close",
            "high": "high",
            "low": "low",
            "volume": "volume",
        })
        rows = _rows_from_df(df)
        return [r for r in rows if start_date <= r["trade_date"] <= end_date]
    except (ValueError, TypeError, KeyError, ConnectionError, OSError) as exc:
        logger.warning("mootdx failed for %s: %s", code, exc)
        return []


def fetch_baostock(code: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    try:
        import baostock as bs
    except ImportError:
        return []
    parts = code.upper().split(".")
    sym = parts[0]
    suffix = parts[1] if len(parts) > 1 else "SH"
    # baostock 无北交所；勿把 .BJ 误映射为 sz.
    if suffix == "BJ" or sym.startswith(("4", "8", "92")):
        return []
    bs_code = f"sh.{sym}" if suffix == "SH" else f"sz.{sym}"
    lg = bs.login()
    if lg.error_code != "0":
        return []
    try:
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="2",
        )
        rows: list[dict[str, Any]] = []
        while rs.error_code == "0" and rs.next():
            row = rs.get_row_data()
            if len(row) >= 6:
                rows.append({
                    "trade_date": row[0],
                    "open": float(row[1] or 0),
                    "high": float(row[2] or 0),
                    "low": float(row[3] or 0),
                    "close": float(row[4] or 0),
                    "volume": float(row[5] or 0),
                })
        return rows
    except (ValueError, TypeError, ConnectionError, OSError) as exc:
        logger.warning("baostock failed for %s: %s", code, exc)
        return []
    finally:
        bs.logout()


def fetch_akshare(code: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    try:
        import akshare as ak
    except ImportError:
        return []
    sym = code.split(".")[0]
    sd = start_date.replace("-", "")
    ed = end_date.replace("-", "")
    try:
        df = ak.stock_zh_a_hist(
            symbol=sym, period="daily", start_date=sd, end_date=ed, adjust="qfq",
        )
        if df is None or df.empty:
            return []
        mapped = []
        for _, row in df.iterrows():
            mapped.append({
                "trade_date": str(row.get("日期", ""))[:10],
                "open": float(row.get("开盘", 0)),
                "close": float(row.get("收盘", 0)),
                "high": float(row.get("最高", 0)),
                "low": float(row.get("最低", 0)),
                "volume": float(row.get("成交量", 0)),
            })
        return mapped
    except (ValueError, TypeError, KeyError, ConnectionError, OSError) as exc:
        logger.warning("akshare failed for %s: %s", code, exc)
        return []


def fetch_akshare_minute(
    code: str,
    *,
    period: str = "5",
    start_date: str | None = None,
    end_date: str | None = None,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """Recent minute bars via akshare; merges/persists to local research.db."""
    period = str(period)
    if period not in ("1", "5", "15", "30", "60"):
        period = "5"

    cached: list[dict[str, Any]] = []
    if use_cache:
        try:
            from market.research_store import get_store
            cached = get_store().load_bars(code, period, start_date, end_date)
        except Exception as exc:
            logger.warning("bar cache load failed: %s", exc)

    try:
        import akshare as ak
    except ImportError:
        return cached

    bare = code.split(".")[0]
    suffix = code.split(".")[-1].upper() if "." in code else (
        "SH" if bare.startswith(("5", "6", "9")) else "SZ"
    )
    symbol = f"{suffix.lower()}{bare}"
    try:
        df = ak.stock_zh_a_minute(symbol=symbol, period=period, adjust="qfq")
        if df is None or df.empty:
            return cached
        day_col = "day" if "day" in df.columns else df.columns[0]
        fresh: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            td = str(row[day_col])
            if start_date and td[:10] < start_date[:10]:
                continue
            if end_date and td[:10] > end_date[:10]:
                continue
            fresh.append({
                "trade_date": td,
                "open": float(row.get("open", 0) or 0),
                "high": float(row.get("high", 0) or 0),
                "low": float(row.get("low", 0) or 0),
                "close": float(row.get("close", 0) or 0),
                "volume": float(row.get("volume", 0) or 0),
            })
        if use_cache and fresh:
            try:
                from market.research_store import get_store
                # store full fresh pull (unfiltered by start) for accumulation
                all_rows = []
                for _, row in df.iterrows():
                    all_rows.append({
                        "trade_date": str(row[day_col]),
                        "open": float(row.get("open", 0) or 0),
                        "high": float(row.get("high", 0) or 0),
                        "low": float(row.get("low", 0) or 0),
                        "close": float(row.get("close", 0) or 0),
                        "volume": float(row.get("volume", 0) or 0),
                    })
                get_store().upsert_bars(code, period, all_rows)
            except Exception as exc:
                logger.warning("bar cache save failed: %s", exc)
        if not fresh and cached:
            return cached
        if not cached:
            return fresh
        # merge by trade_date
        by_td = {r["trade_date"]: r for r in cached}
        for r in fresh:
            by_td[r["trade_date"]] = r
        merged = [by_td[k] for k in sorted(by_td.keys())]
        if start_date:
            merged = [r for r in merged if r["trade_date"][:10] >= start_date[:10]]
        if end_date:
            merged = [r for r in merged if r["trade_date"][:10] <= end_date[:10]]
        return merged
    except (ValueError, TypeError, KeyError, ConnectionError, OSError) as exc:
        logger.warning("akshare minute failed for %s: %s", code, exc)
        return cached
