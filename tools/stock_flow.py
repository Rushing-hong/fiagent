"""A-share capital flow tools."""

from __future__ import annotations

import logging
from typing import Any

from market.eastmoney import get_json, resolve_secid
from market.envelope import clamp_int, err, ok
from tools.base import BaseTool

logger = logging.getLogger(__name__)

_DAILY_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_MINUTE_URL = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
_DAILY_FIELDS = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
_MINUTE_FIELDS = "f51,f52,f53,f54,f55,f56"
_BUCKETS = ("main", "small", "medium", "large", "super_large")

_REALTIME_URL = "https://push2.eastmoney.com/api/qt/kamt/get"
_HISTORY_URL = "https://push2his.eastmoney.com/api/qt/kamt.kline/get"
_REALTIME_FIELDS = "f1,f2,f3,f4,f51,f52,f54,f56"
_HISTORY_FIELDS1 = "f1,f3"
_HISTORY_FIELDS2 = "f51,f52,f54"


class FundFlowTool(BaseTool):
    name = "get_fund_flow"
    summary = "个股资金流向（主力/大单/中单/小单）"
    description = (
        "获取个股资金流向：主力、超大单、大单、中单、小单净额（东财 fflow 接口）。"
        '支持 daily（日频）或 min（当日分时）。示例: {"codes": ["600519.SH"], "period": "daily", "days": 30}'
    )
    parameters = {
        "type": "object",
        "properties": {
            "codes": {"type": "array", "items": {"type": "string"}},
            "period": {"type": "string", "enum": ["min", "daily"], "default": "daily"},
            "days": {"type": "integer", "default": 30},
        },
        "required": ["codes"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        codes = args.get("codes")
        if not isinstance(codes, list) or not codes:
            return err("codes 必须为非空数组")
        period = args.get("period", "daily")
        if period not in ("min", "daily"):
            return err("period 必须是 min 或 daily")
        days = clamp_int(args.get("days"), 30, 1, 250)
        results = {}
        failed = 0
        for symbol in (c.strip() for c in codes):
            entry = _fetch_symbol_flow(symbol, period=period, days=days)
            if "error" in entry:
                failed += 1
            results[symbol] = entry
        quality = "partial" if failed else "normal"
        note = f"{failed} symbol(s) failed" if failed else None
        return ok(
            results,
            quality=quality,
            note=note,
            market="stock",
            source="eastmoney",
            period=period,
            buckets=list(_BUCKETS),
        )


class NorthboundFlowTool(BaseTool):
    name = "get_northbound_flow"
    summary = "北向资金（沪股通+深股通）净流入"
    description = (
        "获取北向资金（沪深港通）市场级净流入：沪股通、深股通及合计，"
        "含实时快照与近期日频历史（东财 kamt 接口，单位：万元）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "lookback_days": {"type": "integer", "default": 30},
        },
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        lookback = clamp_int(args.get("lookback_days"), 30, 1, 250)
        try:
            realtime_payload = get_json(_REALTIME_URL, params={"fields": _REALTIME_FIELDS})
            history_payload = get_json(
                _HISTORY_URL,
                params={
                    "fields1": _HISTORY_FIELDS1,
                    "fields2": _HISTORY_FIELDS2,
                    "klt": "101",
                    "lmt": "250",
                },
            )
        except Exception as exc:
            return err(str(exc))
        return ok(
            {
                "unit": "10k CNY",
                "lookback_days": lookback,
                "realtime": _parse_realtime(realtime_payload),
                "history": _parse_history(history_payload, lookback),
            },
            market="China A",
            source="eastmoney",
        )


def _fetch_symbol_flow(symbol: str, *, period: str, days: int) -> dict[str, Any]:
    secid = resolve_secid(symbol)
    if secid is None:
        return {"symbol": symbol, "error": "无法解析代码"}
    is_daily = period == "daily"
    url = _DAILY_URL if is_daily else _MINUTE_URL
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f7",
        "fields2": _DAILY_FIELDS if is_daily else _MINUTE_FIELDS,
        "klt": "101" if is_daily else "1",
        "lmt": "0",
    }
    try:
        payload = get_json(url, params=params)
    except Exception as exc:
        return {"symbol": symbol, "error": str(exc)}
    data = payload.get("data") if isinstance(payload, dict) else None
    klines = data.get("klines") if isinstance(data, dict) else None
    if not isinstance(klines, list):
        return {"symbol": symbol, "secid": secid, "rows": []}
    rows = []
    for raw in klines:
        if not isinstance(raw, str):
            continue
        parsed = _parse_flow_row(raw)
        if parsed:
            rows.append(parsed)
    if is_daily and days < len(rows):
        rows = rows[-days:]
    return {"symbol": symbol, "secid": secid, "rows": rows[-250:]}


def _parse_flow_row(raw: str) -> dict[str, Any] | None:
    parts = raw.split(",")
    if len(parts) < 1 + len(_BUCKETS):
        return None
    try:
        values = [float(parts[i + 1]) for i in range(len(_BUCKETS))]
    except (ValueError, TypeError):
        return None
    row = {"timestamp": parts[0]}
    row.update(dict(zip(_BUCKETS, values)))
    return row


def _coerce_float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_realtime(payload: Any) -> dict[str, float | None]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return {"shanghai_connect": None, "shenzhen_connect": None, "total": None}
    sh = data.get("hk2sh") if isinstance(data.get("hk2sh"), dict) else {}
    sz = data.get("hk2sz") if isinstance(data.get("hk2sz"), dict) else {}
    shanghai = _coerce_float(sh.get("netBuyAmt"))
    shenzhen = _coerce_float(sz.get("netBuyAmt"))
    if shanghai is None and shenzhen is None:
        total = None
    else:
        total = (shanghai or 0.0) + (shenzhen or 0.0)
    return {"shanghai_connect": shanghai, "shenzhen_connect": shenzhen, "total": total}


def _parse_history(payload: Any, lookback: int) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return []
    klines = data.get("klines")
    if not isinstance(klines, list):
        return []
    rows = []
    for raw in klines:
        if not isinstance(raw, str):
            continue
        parts = raw.split(",")
        if len(parts) < 3:
            continue
        sh = _coerce_float(parts[1])
        sz = _coerce_float(parts[2])
        total = None if sh is None and sz is None else (sh or 0.0) + (sz or 0.0)
        rows.append({
            "trade_date": parts[0],
            "shanghai_connect": sh,
            "shenzhen_connect": sz,
            "total": total,
        })
    return rows[-lookback:]
