"""Fundamental stock screener: filter A-shares by PE/PB/ROE/market_cap.

Wraps existing screen_market (PE/PB/market_cap from push2) and
get_financial_statements (ROE from F10) into a unified screening tool.
"""

from __future__ import annotations

import json
from typing import Any

from market.eastmoney import get_json, push2_diff_rows
from market.envelope import clamp_int, err, ok, to_float
from tools.base import BaseTool

_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_FS_A = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
_FIELDS = "f2,f3,f4,f5,f6,f8,f9,f12,f14,f20,f23,f24,f37,f115"


class ScreenFundamentalTool(BaseTool):
    name = "screen_fundamental"
    summary = "基本面筛选：按 PE/PB/ROE/市值/股息率筛选 A 股"
    description = (
        "按基本面指标筛选 A 股股票，支持 PE/PB/ROE/市值/股息率等多维过滤。\n"
        "用于价值投资选股、成长股筛选、格雷厄姆式低估值策略。\n\n"
        "示例: {\"max_pe\": 15, \"min_roe\": 15, \"max_pb\": 2, \"min_market_cap\": 100}\n"
        "策略: {\"max_pe\": 10, \"min_dividend_yield\": 3}  → 低估值高股息\n"
        "策略: {\"max_pe\": 30, \"min_roe\": 20, \"max_pb\": 5} → 合理估值高成长\n\n"
        "数据源: 东财 push2 实时行情（PE/PB/市值/股息率来自行情数据，ROE 来自最近财报）"
    )
    parameters = {
        "type": "object",
        "properties": {
            "max_pe": {
                "type": "number",
                "description": "最高 PE(TTM)，排除负值。如 15 表示 PE≤15",
            },
            "max_pb": {
                "type": "number",
                "description": "最高 PB(MRQ)。如 2 表示 PB≤2",
            },
            "min_roe": {
                "type": "number",
                "description": "最低 ROE(%)。如 15 表示 ROE≥15%",
            },
            "min_market_cap": {
                "type": "number",
                "description": "最低总市值（亿元）。如 100 表示市值≥100亿",
            },
            "min_dividend_yield": {
                "type": "number",
                "description": "最低股息率(%)。如 3 表示股息率≥3%",
            },
            "exclude_st": {
                "type": "boolean",
                "default": True,
                "description": "是否排除 ST/*ST 股票",
            },
            "sort_by": {
                "type": "string",
                "enum": ["pe", "pb", "roe", "market_cap", "dividend_yield"],
                "default": "pe",
                "description": "排序字段",
            },
            "top_n": {
                "type": "integer",
                "default": 30,
                "description": "最多返回数量",
            },
        },
    }
    is_readonly = True

    def execute(self, args: dict[str, Any], ctx: Any) -> str:
        max_pe = args.get("max_pe")
        max_pb = args.get("max_pb")
        min_roe = args.get("min_roe")
        min_market_cap = args.get("min_market_cap")
        min_dividend_yield = args.get("min_dividend_yield")
        exclude_st = args.get("exclude_st", True)
        sort_by = str(args.get("sort_by", "pe"))
        top_n = clamp_int(args.get("top_n"), 30, 1, 100)

        # No filter specified at all = error
        if max_pe is None and max_pb is None and min_roe is None and min_market_cap is None and min_dividend_yield is None:
            return err("至少指定一个筛选条件: max_pe/max_pb/min_roe/min_market_cap/min_dividend_yield")

        # Fetch from push2 (max 500 stocks to filter)
        try:
            payload = get_json(
                _CLIST_URL,
                params={
                    "pn": "1",
                    "pz": "500",
                    "po": "1",
                    "fid": "f9",  # sort by PE ascending by default
                    "fs": _FS_A,
                    "fields": _FIELDS,
                },
            )
        except Exception as e:
            return err(f"数据获取失败: {e}")

        rows = push2_diff_rows(payload)
        if not rows:
            return err("未获取到行情数据")

        results = []
        for raw in rows:
            if not isinstance(raw, dict) or not raw.get("f12"):
                continue

            code = str(raw.get("f12"))
            name = str(raw.get("f14", ""))

            # ST filter
            if exclude_st and ("ST" in name or "*ST" in name):
                continue

            pe = to_float(raw.get("f9"))       # PE(TTM)
            pb = to_float(raw.get("f23"))      # PB(MRQ)
            roe = to_float(raw.get("f37"))     # ROE(%)
            mc = to_float(raw.get("f20"))      # 总市值(亿)
            dy = to_float(raw.get("f115"))     # 股息率(%)

            # Apply filters
            if max_pe is not None:
                if pe is None or pe <= 0 or pe > max_pe:
                    continue
            if max_pb is not None:
                if pb is None or pb <= 0 or pb > max_pb:
                    continue
            if min_roe is not None:
                if roe is None or roe < min_roe:
                    continue
            if min_market_cap is not None:
                if mc is None or mc < min_market_cap:
                    continue
            if min_dividend_yield is not None:
                if dy is None or dy < min_dividend_yield:
                    continue

            results.append({
                "code": code,
                "name": name,
                "price": to_float(raw.get("f2")),
                "change_pct": to_float(raw.get("f3")),
                "pe": pe,
                "pb": pb,
                "roe": roe,
                "market_cap": mc,
                "dividend_yield": dy,
                "volume": to_float(raw.get("f5")),
            })

        # Sort
        sort_key = {
            "pe": "pe", "pb": "pb", "roe": "roe",
            "market_cap": "market_cap", "dividend_yield": "dividend_yield",
        }.get(sort_by, "pe")
        reverse = sort_by in ("roe", "market_cap", "dividend_yield")  # higher is better for these
        results.sort(key=lambda r: r.get(sort_key) if r.get(sort_key) is not None else (9999 if reverse else -9999), reverse=reverse)

        top = results[:top_n]

        filters_applied = {}
        if max_pe is not None: filters_applied["max_pe"] = max_pe
        if max_pb is not None: filters_applied["max_pb"] = max_pb
        if min_roe is not None: filters_applied["min_roe"] = min_roe
        if min_market_cap is not None: filters_applied["min_market_cap"] = min_market_cap
        if min_dividend_yield is not None: filters_applied["min_dividend_yield"] = min_dividend_yield

        return ok({
            "filters": filters_applied,
            "exclude_st": exclude_st,
            "sort_by": sort_by,
            "count": len(top),
            "stocks": top,
        }, source="eastmoney", market="a_share")
