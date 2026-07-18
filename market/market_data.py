"""OHLCV fetch with Tencent + Eastmoney fallback for A-shares."""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any
from urllib.request import Request, urlopen

from market.eastmoney import KLT_BY_INTERVAL, fetch_kline, resolve_secid
from market.envelope import now_as_of, ok, worse_quality
from market.loaders import fetch_akshare, fetch_baostock, fetch_mootdx

logger = logging.getLogger(__name__)

DEFAULT_MAX_ROWS = 250
_TENCENT_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

_A_SHARE_RE = re.compile(r"^\d{6}\.(SZ|SH|BJ)$", re.I)
_A_SHARE_CHAIN = ("tencent", "mootdx", "eastmoney", "baostock", "akshare")
_FETCHERS = {
    "tencent": lambda c, s, e, iv: _fetch_tencent(c, s, e),
    "mootdx": lambda c, s, e, iv: fetch_mootdx(c, s, e),
    "eastmoney": lambda c, s, e, iv: _fetch_eastmoney(c, s, e, iv),
    "baostock": lambda c, s, e, iv: fetch_baostock(c, s, e),
    "akshare": lambda c, s, e, iv: fetch_akshare(c, s, e),
}


def is_a_share_symbol(code: str) -> bool:
    return bool(_A_SHARE_RE.match(str(code).strip().upper()))


def detect_source(code: str) -> str:
    if is_a_share_symbol(code):
        return "tencent"
    return "none"


def _preferred_source(code: str, source: str) -> str:
    if source == "auto":
        return _A_SHARE_CHAIN[0] if is_a_share_symbol(code) else "none"
    return source


def cap_rows(records: list, max_rows: int) -> list | dict[str, object]:
    n = len(records)
    if max_rows < 0:
        max_rows = DEFAULT_MAX_ROWS
    if max_rows == 0 or n <= max_rows:
        return records
    step = math.ceil(n / max_rows)
    sampled = records[::step]
    if sampled[-1] is not records[-1]:
        sampled = sampled + [records[-1]]
    return {
        "rows": n,
        "returned": len(sampled),
        "truncated": True,
        "policy": f"every-{step}th-row (even stride; last bar pinned)",
        "hint": "narrow the date range or set max_rows=0 for all rows",
        "data": sampled,
    }


def _date_compact(d: str) -> str:
    return d.replace("-", "")


def _fetch_tencent(code: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    parts = code.upper().split(".")
    symbol = parts[0]
    suffix = parts[1] if len(parts) > 1 else ""
    if suffix == "SH":
        tencent_code = f"sh{symbol}"
    elif suffix == "SZ":
        tencent_code = f"sz{symbol}"
    elif suffix == "BJ":
        tencent_code = f"bj{symbol}"
    else:
        return []

    url = (
        f"{_TENCENT_URL}?param={tencent_code},day,"
        f"{start_date},{end_date},500,qfq"
    )
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://web.ifzq.gtimg.cn/",
        },
    )
    with urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw).get("data", {})
    if not data:
        return []
    stock_key = next(iter(data), None)
    if not stock_key:
        return []
    klines = data[stock_key].get("qfqday") or data[stock_key].get("day") or []
    rows: list[dict[str, Any]] = []
    for k in klines:
        if len(k) >= 6:
            rows.append({
                "trade_date": k[0],
                "open": float(k[1]),
                "close": float(k[2]),
                "high": float(k[3]),
                "low": float(k[4]),
                "volume": float(k[5]),
            })
    return rows


def _fetch_eastmoney(
    code: str, start_date: str, end_date: str, interval: str
) -> list[dict[str, Any]]:
    secid = resolve_secid(code)
    if secid is None:
        return []
    klt = KLT_BY_INTERVAL.get(interval, 101)
    return fetch_kline(
        secid,
        klt=klt,
        beg=_date_compact(start_date),
        end=_date_compact(end_date),
    )


def fetch_one(
    code: str,
    start_date: str,
    end_date: str,
    *,
    source: str = "auto",
    interval: str = "1D",
) -> tuple[list[dict[str, Any]], str]:
    code = str(code).strip().upper()
    if not is_a_share_symbol(code):
        return [], "rejected_non_a_share"

    if source == "auto":
        chain = list(_A_SHARE_CHAIN)
    elif source in _FETCHERS:
        chain = [source]
    else:
        chain = list(_A_SHARE_CHAIN)

    tried: list[str] = []
    for s in chain:
        tried.append(s)
        try:
            fetcher = _FETCHERS.get(s)
            if fetcher is None:
                continue
            rows = fetcher(code, start_date, end_date, interval)
            if rows:
                return rows, s
        except Exception as exc:
            logger.warning("%s fetch failed for %s: %s", s, code, exc)
    return [], tried[-1] if tried else "none"


def fetch_market_data(
    *,
    codes: list[str],
    start_date: str,
    end_date: str,
    source: str = "auto",
    interval: str = "1D",
    max_rows: int = DEFAULT_MAX_ROWS,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for code in codes:
        code = code.strip().upper()
        preferred = _preferred_source(code, source)
        rows, used_source = fetch_one(
            code, start_date, end_date, source=source, interval=interval
        )
        if not rows:
            if used_source == "rejected_non_a_share":
                results[code] = {
                    "error": "A-share only (expect ######.SH|SZ|BJ)",
                    "source_tried": used_source,
                    "quality": "partial",
                }
            else:
                results[code] = {
                    "error": "no data",
                    "source_tried": used_source,
                    "quality": "partial",
                }
            continue

        quality = "normal"
        notes: list[str] = []
        if used_source != preferred:
            quality = "degraded"
            notes.append(f"fallback from {preferred} → {used_source}")

        capped = cap_rows(rows, max_rows)
        if isinstance(capped, dict):
            quality = worse_quality(quality, "degraded")  # type: ignore[arg-type]
            notes.append(f"truncated {capped['rows']}→{capped['returned']}")
            entry: dict[str, Any] = {
                "source": used_source,
                "quality": quality,
                **capped,
            }
        else:
            entry = {"source": used_source, "quality": quality, "data": capped}
        if notes:
            entry["note"] = "; ".join(notes)
        results[code] = entry
    return results


def fetch_market_data_json(**kwargs: Any) -> str:
    data = fetch_market_data(**kwargs)
    qualities = [
        v.get("quality", "normal")
        for v in data.values()
        if isinstance(v, dict)
    ]
    overall: str = "normal"
    notes: list[str] = []
    for q in qualities:
        overall = worse_quality(overall, q)  # type: ignore[arg-type]
    failed = sum(1 for v in data.values() if isinstance(v, dict) and "error" in v)
    truncated = sum(
        1 for v in data.values() if isinstance(v, dict) and v.get("truncated")
    )
    fallbacks = sum(
        1
        for v in data.values()
        if isinstance(v, dict) and "fallback" in str(v.get("note", ""))
    )
    if failed:
        notes.append(f"{failed} symbol(s) failed")
    if truncated:
        notes.append(f"{truncated} symbol(s) truncated")
    if fallbacks:
        notes.append(f"{fallbacks} symbol(s) used fallback source")
    return ok(
        data,
        quality=overall,  # type: ignore[arg-type]
        as_of=now_as_of(),
        note="; ".join(notes) if notes else None,
        market="stock",
        source="multi",
    )
