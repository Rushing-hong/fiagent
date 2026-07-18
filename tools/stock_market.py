"""A-share market data tools: OHLCV, symbol search, screener."""

from __future__ import annotations

import logging
from typing import Any

from market.eastmoney import push2_diff_rows, search_suggest
from market.envelope import clamp_int, err, ok
from market.http import throttled_get_json
from market.market_data import DEFAULT_MAX_ROWS, fetch_market_data_json
from tools.base import BaseTool

logger = logging.getLogger(__name__)

# 东财 suggest：1=沪，0=深/北（北交所以代码 4/8 开头区分）
_SUGGEST_SUFFIX = {"1": "SH", "0": "SZ"}
_CLIST_URLS = (
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://push2delay.eastmoney.com/api/qt/clist/get",
)
_MARKET_FS = {
    "a": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
}
_SORT_FID = {"change_pct": "f3", "volume": "f5", "amount": "f6", "turnover": "f8",
              "pe": "f9", "market_cap": "f20", "pb": "f23"}
_FIELDS = "f2,f3,f4,f5,f6,f8,f9,f12,f14,f20,f23"
_AK_SORT_COL = {
    "change_pct": "涨跌幅",
    "volume": "成交量",
    "amount": "成交额",
    "turnover": "换手率",
    "pe": "市盈率-动态",
    "market_cap": "总市值",
    "pb": "市净率",
}


class MarketDataTool(BaseTool):
    name = "get_market_data"
    summary = "获取 A 股 OHLCV 行情（多源自动降级）"
    description = (
        "获取 A 股 OHLCV。source=auto 时按腾讯→mootdx→东财→baostock→akshare 降级；"
        "也可指定单一源。符号如 600519.SH、000001.SZ、830799.BJ。"
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
            bare = code.zfill(6) if code.isdigit() and len(code) <= 6 else code
            if not bare.isdigit() or len(bare) != 6:
                continue
            if suffix == "SZ" and bare[0] in ("4", "8"):
                suffix = "BJ"
            symbol = f"{bare}.{suffix}"
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
        "A 股排行：东财 push2→push2delay，失败降级 akshare。"
        "涨幅榜 ascending=false；跌幅榜 ascending=true。"
        '示例: {"market":"a","sort_by":"change_pct","top_n":10,"ascending":true}'
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
            "ascending": {
                "type": "boolean",
                "default": False,
                "description": "true=升序（跌幅榜）；false=降序（涨幅榜）",
            },
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
        ascending = _as_bool(args.get("ascending"), False)
        po = "0" if ascending else "1"
        clist_params = {
            "pn": "1",
            "pz": str(top_n),
            "po": po,
            "fid": _SORT_FID[sort_by],
            "fs": _MARKET_FS[market],
            "fields": _FIELDS,
            "fltt": "2",
        }

        errors: list[str] = []
        try:
            payload, host = _clist_get(clist_params)
            stocks = _rows_from_push2(payload, top_n)
            if not stocks:
                raise RuntimeError(f"{host} had diff but no usable rows")
            delayed = "push2delay" in host
            return ok(
                {
                    "market": market,
                    "sort_by": sort_by,
                    "ascending": ascending,
                    "count": len(stocks),
                    "stocks": stocks,
                },
                market="a_share",
                source="eastmoney",
                quality="degraded" if delayed else "normal",
                note=(f"fallback host {host}" if delayed else None),
            )
        except Exception as exc:
            errors.append(f"eastmoney: {exc}")

        try:
            stocks = _screen_via_akshare(
                sort_by=sort_by, ascending=ascending, top_n=top_n
            )
            if stocks:
                return ok(
                    {
                        "market": market,
                        "sort_by": sort_by,
                        "ascending": ascending,
                        "count": len(stocks),
                        "stocks": stocks,
                    },
                    market="a_share",
                    source="akshare",
                    quality="degraded",
                    note="fallback from eastmoney → akshare.stock_zh_a_spot_em",
                )
            errors.append("akshare returned empty")
        except Exception as exc:
            errors.append(f"akshare: {exc}")

        return err("筛选请求失败: " + " | ".join(errors))


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off", ""):
        return False
    return default


def _clist_get(params: dict[str, Any]) -> tuple[Any, str]:
    """Try push2 then push2delay; empty diff counts as failure so the next host is tried."""
    last: Exception | None = None
    for url in _CLIST_URLS:
        host = url.split("//", 1)[1].split("/", 1)[0]
        try:
            payload = throttled_get_json(
                url, host_key=host, min_interval=1.0, params=params, timeout=15.0
            )
            if not push2_diff_rows(payload):
                raise RuntimeError(f"{host} returned empty diff")
            return payload, host
        except Exception as exc:
            last = exc
            logger.warning("screen_market clist fail %s: %s", host, exc)
    raise RuntimeError(str(last) if last else "clist unavailable")


def _rows_from_push2(payload: Any, top_n: int) -> list[dict[str, Any]]:
    stocks: list[dict[str, Any]] = []
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
        if len(stocks) >= top_n:
            break
    return stocks


def _screen_via_akshare(
    *,
    sort_by: str,
    ascending: bool,
    top_n: int,
) -> list[dict[str, Any]]:
    import akshare as ak
    import pandas as pd

    df = ak.stock_zh_a_spot_em()
    col = _AK_SORT_COL.get(sort_by, "涨跌幅")
    if col not in df.columns:
        raise RuntimeError(f"akshare 缺列: {col}")
    df = df.copy()
    df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=[col]).sort_values(col, ascending=ascending).head(top_n)
    stocks: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        stocks.append({
            "code": str(row.get("代码", "")),
            "name": str(row.get("名称", "")),
            "price": _num(row.get("最新价")),
            "change_pct": _num(row.get("涨跌幅")),
            "volume": _num(row.get("成交量")),
            "amount": _num(row.get("成交额")),
            "turnover_rate": _num(row.get("换手率")),
            "pe": _num(row.get("市盈率-动态")),
            "pb": _num(row.get("市净率")),
            "market_cap": _num(row.get("总市值")),
        })
    return stocks


def _num(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
