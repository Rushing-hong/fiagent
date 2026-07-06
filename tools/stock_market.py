"""A-share market data tools: OHLCV, symbol search, screener."""

from __future__ import annotations

import json
import logging
from typing import Any

from market.eastmoney import get_json, push2_diff_rows, search_suggest
from market.envelope import clamp_int, err, ok
from market.market_data import DEFAULT_MAX_ROWS, fetch_market_data_json
from tools.base import BaseTool

logger = logging.getLogger(__name__)

_SUGGEST_SUFFIX = {"1": "SH", "0": "SZ", "116": "HK", "105": "US", "106": "US", "107": "US"}
_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_MARKET_FS = {
    "a": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
}
_SORT_FID = {"change_pct": "f3", "volume": "f5", "amount": "f6", "turnover": "f8",
              "pe": "f9", "market_cap": "f20", "pb": "f23"}
_FIELDS = "f2,f3,f4,f5,f6,f8,f9,f12,f14,f20,f23"


class MarketDataTool(BaseTool):
    name = "get_market_data"
    summary = "获取 A 股 OHLCV 行情（腾讯/东财）"
    description = (
        "获取 A 股 OHLCV 行情数据。数据源：腾讯财经（首选）或东方财富（备用），"
        "均为公开 HTTP 接口，无需 API Key。符号格式如 600519.SH、000001.SZ。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "codes": {
                "type": "array",
                "items": {"type": "string"},
                "description": '股票代码列表，如 ["600519.SH", "000001.SZ"]',
            },
            "start_date": {"type": "string", "description": "开始日期 YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
            "source": {
                "type": "string",
                "enum": ["auto", "tencent", "mootdx", "eastmoney", "baostock", "akshare"],
                "default": "auto",
            },
            "interval": {"type": "string", "default": "1D"},
            "max_rows": {"type": "integer", "default": DEFAULT_MAX_ROWS},
        },
        "required": ["codes", "start_date", "end_date"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        codes = args.get("codes")
        if not isinstance(codes, list) or not codes:
            return err("codes 必须为非空数组")
        return fetch_market_data_json(
            codes=codes,
            start_date=args["start_date"],
            end_date=args["end_date"],
            source=args.get("source", "auto"),
            interval=args.get("interval", "1D"),
            max_rows=args.get("max_rows", DEFAULT_MAX_ROWS),
        )


class SearchSymbolTool(BaseTool):
    name = "search_symbol"
    summary = "按名称/代码搜索 A 股标的"
    description = (
        "将公司名或代码片段解析为候选股票代码（东财 suggest 接口，免费无需 Key）。"
        "支持中文名如「茅台」、代码如 600519。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "公司名或代码片段"},
            "limit": {"type": "integer", "default": 10},
        },
        "required": ["query"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            return err("query 不能为空")
        limit = clamp_int(args.get("limit"), 10, 1, 25)
        try:
            rows = search_suggest(query, count=25)
        except Exception as exc:
            return err(f"东财搜索失败: {exc}")

        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            quote_id = row.get("QuoteID")
            market = ""
            code = str(row.get("Code") or "").strip()
            if isinstance(quote_id, str) and "." in quote_id:
                market, _, qid_code = quote_id.partition(".")
                code = code or qid_code.strip()
            else:
                market = str(row.get("MktNum") or "").strip()
            suffix = _SUGGEST_SUFFIX.get(market)
            if not suffix or not code:
                continue
            symbol = f"{code.zfill(5)}.{suffix}" if suffix == "HK" else f"{code}.{suffix}"
            if symbol in seen:
                continue
            seen.add(symbol)
            candidates.append({
                "symbol": symbol,
                "name": row.get("Name"),
                "source": "eastmoney",
            })
            if len(candidates) >= limit:
                break
        return ok(
            {"query": query, "count": len(candidates), "candidates": candidates},
            market="cn",
            source="eastmoney",
        )


class ScreenMarketTool(BaseTool):
    name = "screen_market"
    summary = "A 股全市场排行筛选"
    description = (
        "按涨跌幅、成交量、成交额或换手率对 A 股全市场排行（东财 push2 接口）。"
        '示例: {"market": "a", "sort_by": "change_pct", "top_n": 20}'
    )
    parameters = {
        "type": "object",
        "properties": {
            "market": {"type": "string", "enum": ["a"], "description": "A 股市场"},
            "sort_by": {
                "type": "string",
                "enum": ["change_pct", "volume", "amount", "turnover", "pe", "market_cap", "pb"],
                "default": "change_pct",
            },
            "top_n": {"type": "integer", "default": 30},
        },
        "required": ["market"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        market = args.get("market", "a")
        if market not in _MARKET_FS:
            return err("market 仅支持 a（A 股）")
        sort_by = args.get("sort_by", "change_pct")
        if sort_by not in _SORT_FID:
            return err(f"sort_by 必须是 {list(_SORT_FID)} 之一")
        top_n = clamp_int(args.get("top_n"), 30, 1, 100)
        try:
            payload = get_json(
                _CLIST_URL,
                params={
                    "pn": "1",
                    "pz": str(top_n),
                    "po": "1",
                    "fid": _SORT_FID[sort_by],
                    "fs": _MARKET_FS[market],
                    "fields": _FIELDS,
                },
            )
        except Exception as exc:
            return err(f"筛选请求失败: {exc}")

        stocks = []
        for raw in push2_diff_rows(payload):
            if not isinstance(raw, dict) or not raw.get("f12"):
                continue
            stocks.append({
                "code": str(raw.get("f12")),
                "name": str(raw.get("f14", "")),
                "price": _num(raw.get("f2")),
                "change_pct": _num(raw.get("f3")),
                "volume": _num(raw.get("f5")),
                "amount": _num(raw.get("f6")),
                "turnover_rate": _num(raw.get("f8")),
                "pe": _num(raw.get("f9")),
                "pb": _num(raw.get("f23")),
                "market_cap": _num(raw.get("f20")),
            })
        return ok(
            {"market": market, "sort_by": sort_by, "count": len(stocks), "stocks": stocks[:top_n]},
            market="a_share",
            source="eastmoney",
        )


def _num(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
