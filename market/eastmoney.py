"""Eastmoney public API client: secid resolution, klines, datacenter."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from market.http import resolve_min_interval, throttled_get_json

logger = logging.getLogger(__name__)

_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_SEARCH_URL = "https://searchapi.eastmoney.com/api/suggest/get"
_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
F10_REPORT_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"

_HOST_KEY = "eastmoney"
_MIN_INTERVAL_ENV = "FIAGENT_EASTMONEY_MIN_INTERVAL"
_DEFAULT_MIN_INTERVAL = 1.0

KLT_BY_INTERVAL: dict[str, int] = {
    "1D": 101,
    "1W": 102,
    "1M": 103,
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1H": 60,
    "60m": 60,
}

_FIELDS1 = "f1,f2,f3,f4,f5,f6"
_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57"

_US_SECID_CACHE: dict[str, str | None] = {}
_JSONP_WRAPPER = re.compile(
    r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*\s*\((.*)\)\s*;?\s*$",
    re.DOTALL,
)

A_SHARE_SUFFIXES = ("SH", "SZ", "BJ")


def validate_a_share(code: str) -> str | None:
    """Validate and normalize an A-share code. Returns upper-cased code or None."""
    if not code or not code.strip():
        return None
    code = code.strip().upper()
    if code.rpartition(".")[2] not in A_SHARE_SUFFIXES:
        return None
    return code


def _min_interval() -> float:
    return resolve_min_interval(_MIN_INTERVAL_ENV, _DEFAULT_MIN_INTERVAL)


def get_json(url: str, *, params: dict[str, Any]) -> Any:
    return throttled_get_json(
        url,
        host_key=_HOST_KEY,
        min_interval=_min_interval(),
        params=params,
    )


def fetch_datacenter(
    report_name: str,
    *,
    columns: str = "ALL",
    filter_expr: str,
    sort_columns: str,
    sort_types: str = "-1",
    page_size: int = 500,
    source: str = "WEB",
    client: str = "WEB",
    url: str = _DATACENTER_URL,
) -> list[dict[str, Any]]:
    payload = get_json(
        url,
        params={
            "reportName": report_name,
            "columns": columns,
            "filter": filter_expr,
            "sortColumns": sort_columns,
            "sortTypes": sort_types,
            "pageNumber": "1",
            "pageSize": str(page_size),
            "source": source,
            "client": client,
        },
    )
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    data = result.get("data")
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _resolve_a_share_secid(code: str, suffix: str) -> str | None:
    if suffix == "SH":
        return f"1.{code}"
    if suffix in ("SZ", "BJ"):
        return f"0.{code}"
    return None


def _strip_jsonp(text: str) -> Any:
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except (ValueError, TypeError):
        pass
    match = _JSONP_WRAPPER.match(stripped)
    if match is None:
        return None
    try:
        return json.loads(match.group(1).strip())
    except (ValueError, TypeError):
        return None


def _parse_us_secid(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    table = payload.get("QuotationCodeTable")
    if not isinstance(table, dict):
        return None
    candidates = table.get("Data")
    if not isinstance(candidates, list):
        return None
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        quote_id = candidate.get("QuoteID")
        if isinstance(quote_id, str) and "." in quote_id:
            market = quote_id.split(".", 1)[0]
            if market in ("105", "106", "107"):
                return quote_id
    return None


def _resolve_us_secid(code: str) -> str | None:
    if code in _US_SECID_CACHE:
        return _US_SECID_CACHE[code]
    secid: str | None = None
    try:
        payload = get_json(
            _SEARCH_URL,
            params={"input": code, "type": "14", "count": "10"},
        )
        if isinstance(payload, str):
            payload = _strip_jsonp(payload)
        secid = _parse_us_secid(payload)
    except Exception as exc:
        logger.warning("eastmoney secid search failed for %s: %s", code, exc)
    _US_SECID_CACHE[code] = secid
    return secid


def resolve_secid(symbol: str) -> str | None:
    if not symbol or "." not in symbol:
        return None
    code, _, suffix = symbol.rpartition(".")
    code = code.strip().upper()
    suffix = suffix.strip().upper()
    if not code:
        return None
    if suffix in A_SHARE_SUFFIXES:
        return _resolve_a_share_secid(code, suffix)
    if suffix == "HK":
        return f"116.{code.zfill(5)}"
    if suffix == "US":
        return _resolve_us_secid(code)
    return None


def bare_a_share_code(symbol: str) -> str | None:
    token = symbol.strip().upper()
    if "." in token:
        token = token.rpartition(".")[0]
    for prefix in ("SH", "SZ", "BJ"):
        if token.startswith(prefix):
            token = token[len(prefix):]
    token = token.strip()
    if len(token) == 6 and token.isdigit():
        return token
    return None


def _parse_kline_row(raw: str) -> dict[str, Any] | None:
    parts = raw.split(",")
    if len(parts) < 7:
        return None
    try:
        return {
            "trade_date": parts[0],
            "open": float(parts[1]),
            "close": float(parts[2]),
            "high": float(parts[3]),
            "low": float(parts[4]),
            "volume": float(parts[5]),
            "amount": float(parts[6]),
        }
    except (ValueError, TypeError):
        return None


def fetch_kline(
    secid: str,
    *,
    klt: int,
    fqt: int = 1,
    beg: str = "0",
    end: str = "20500101",
) -> list[dict[str, Any]]:
    payload = get_json(
        _KLINE_URL,
        params={
            "secid": secid,
            "klt": str(klt),
            "fqt": str(fqt),
            "beg": beg,
            "end": end,
            "fields1": _FIELDS1,
            "fields2": _FIELDS2,
            "rev": "1",
            "lmt": "1000000",
        },
    )
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    klines = data.get("klines")
    if not isinstance(klines, list):
        return []
    rows: list[dict[str, Any]] = []
    for raw in klines:
        if isinstance(raw, str):
            parsed = _parse_kline_row(raw)
            if parsed is not None:
                rows.append(parsed)
    return rows


def push2_diff_rows(payload: Any) -> list:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    diff = data.get("diff")
    if isinstance(diff, dict):
        return list(diff.values())
    if isinstance(diff, list):
        return diff
    return []


def search_suggest(query: str, *, count: int = 25) -> list[dict[str, Any]]:
    payload = get_json(
        _SEARCH_URL,
        params={"input": query, "type": "14", "count": str(count)},
    )
    if not isinstance(payload, dict):
        return []
    table = payload.get("QuotationCodeTable")
    if not isinstance(table, dict):
        return []
    data = table.get("Data")
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]
