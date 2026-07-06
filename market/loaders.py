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
    if code.upper().endswith(".BJ") or (len(bare) == 6 and bare[0] in ("4", "8")):
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
