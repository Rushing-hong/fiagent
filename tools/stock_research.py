"""A-share research tools: 研报、新闻、财报."""

from __future__ import annotations

import json
import logging
from typing import Any

from market.eastmoney import (
    F10_REPORT_URL,
    bare_a_share_code,
    fetch_datacenter,
    get_json,
    resolve_secid,
    validate_a_share,
)
from market.envelope import clamp_int, err, ok, to_float
from market.http import resolve_min_interval, throttled_get
from tools.base import BaseTool

logger = logging.getLogger(__name__)

_REPORT_LIST_URL = "https://reportapi.eastmoney.com/report/list"
_EM_NEWS_URL = "https://search-api-web.eastmoney.com/search/jsonp"
_THS_URL = "https://basic.10jqka.com.cn/api/stock/profit_forecast/"

_EM_REPORTS = {
    "balance": "RPT_F10_FINANCE_GBALANCE",
    "income": "RPT_F10_FINANCE_GINCOME",
    "cashflow": "RPT_F10_FINANCE_GCASHFLOW",
    "indicators": "RPT_F10_FINANCE_MAINFINADATA",
}
_VALID_STATEMENTS = ("balance", "income", "cashflow", "indicators")
_VALID_PERIODS = ("annual", "quarter")
_SNIPPET_CHARS = 280


class ResearchReportsTool(BaseTool):
    name = "get_research_reports"
    summary = "卖方研报 + 一致预期 EPS"
    description = (
        "获取 A 股研报列表（东财 reportapi）及同花顺一致预期 EPS（THS，best-effort）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "limit": {"type": "integer", "default": 20},
        },
        "required": ["code"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        code = validate_a_share(str(args.get("code") or ""))
        if code is None:
            return err("需要有效的 A 股代码 .SH/.SZ/.BJ")
        limit = clamp_int(args.get("limit"), 20, 1, 50)
        try:
            payload = get_json(
                _REPORT_LIST_URL,
                params={
                    "code": bare_a_share_code(code),
                    "qType": "0",
                    "pageSize": str(limit),
                    "pageNo": "1",
                },
            )
        except Exception as exc:
            return err(str(exc))
        reports = _parse_reports(payload)[:limit]
        consensus = _fetch_ths_consensus(code)
        if not reports and not consensus:
            return err(f"未找到 {code} 的研报数据")
        return ok(
            {"code": code, "reports": reports, "consensus_eps": consensus},
            market="CN",
            source="eastmoney+ths",
        )


class StockNewsTool(BaseTool):
    name = "get_stock_news"
    summary = "财经新闻"
    description = (
        "获取 A 股个股或全市场财经新闻（东财 search-api）。"
        'scope=stock 需 code；scope=global 为宏观财经。'
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "scope": {"type": "string", "enum": ["stock", "global"], "default": "stock"},
            "limit": {"type": "integer", "default": 20},
        },
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        scope = args.get("scope", "stock")
        limit = clamp_int(args.get("limit"), 20, 1, 50)
        if scope == "global":
            query = "财经"
        else:
            code = str(args.get("code") or "").strip()
            if not code:
                return err("scope=stock 需要 code")
            query = code.split(".", 1)[0]
        try:
            articles = _fetch_em_news(query, limit)
        except Exception as exc:
            return err(str(exc))
        return ok(
            {"query": query, "scope": scope, "count": len(articles), "articles": articles},
            market="cn",
            source="eastmoney",
        )


class FinancialStatementsTool(BaseTool):
    name = "get_financial_statements"
    summary = "财务报表（三大表 + 主要指标）"
    description = (
        "获取 A 股财务报表：balance/income/cashflow/indicators（东财 F10 datacenter）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "statement": {
                "type": "string",
                "enum": list(_VALID_STATEMENTS),
                "default": "indicators",
            },
            "period": {"type": "string", "enum": list(_VALID_PERIODS), "default": "annual"},
        },
        "required": ["code"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        code = validate_a_share(str(args.get("code") or ""))
        if code is None:
            return err("需要有效的 A 股代码")
        statement = args.get("statement", "indicators")
        period = args.get("period", "annual")
        if statement not in _VALID_STATEMENTS:
            return err(f"statement 必须是 {_VALID_STATEMENTS}")
        if period not in _VALID_PERIODS:
            return err(f"period 必须是 {_VALID_PERIODS}")
        if resolve_secid(code) is None:
            return err(f"无法解析 {code}")
        try:
            rows = fetch_datacenter(
                _EM_REPORTS[statement],
                filter_expr=f'(SECUCODE="{code}")',
                sort_columns="REPORT_DATE",
                page_size=40,
                url=F10_REPORT_URL,
                source="F10",
                client="PC",
            )
        except Exception as exc:
            return err(str(exc))
        periods = _filter_periods(rows, period)[:40]
        if not periods:
            return err(f"未找到 {code} 的 {statement} 数据")
        return ok(
            {"code": code, "periods": periods},
            market="a_share",
            source="eastmoney",
            statement=statement,
            period=period,
        )


def _parse_reports(payload: Any) -> list[dict]:
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append({
            "title": row.get("title"),
            "brokerage": row.get("orgSName") or row.get("orgName"),
            "analyst": row.get("researcher"),
            "publish_date": str(row.get("publishDate") or "")[:10],
            "rating": row.get("emRatingName") or row.get("sRatingName"),
        })
    return out


def _fetch_ths_consensus(code: str) -> list[dict]:
    bare = bare_a_share_code(code)
    if not bare:
        return []
    try:
        interval = resolve_min_interval("FIAGENT_THS_MIN_INTERVAL", 1.0)
        resp = throttled_get(
            f"{_THS_URL}{bare}/",
            host_key="ths",
            min_interval=interval,
            headers={"Referer": f"https://basic.10jqka.com.cn/{bare}/"},
        )
        payload = resp.json()
    except Exception as exc:
        logger.warning("THS consensus failed for %s: %s", code, exc)
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    return [
        {"year": item.get("year"), "eps": to_float(item.get("eps"))}
        for item in data
        if isinstance(item, dict)
    ]


def _fetch_em_news(query: str, limit: int) -> list[dict]:
    param = json.dumps(
        {
            "uid": "",
            "keyword": query,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": limit,
                }
            },
        },
        ensure_ascii=False,
    )
    payload = get_json(_EM_NEWS_URL, params={"cb": "", "param": param, "_": "0"})
    if isinstance(payload, str):
        start = payload.find("(")
        end = payload.rfind(")")
        inner = payload[start + 1:end] if start != -1 and end > start else payload
        payload = json.loads(inner)
    result = payload.get("result") if isinstance(payload, dict) else None
    articles = result.get("cmsArticleWebOld") if isinstance(result, dict) else None
    if not isinstance(articles, list):
        return []
    out = []
    for a in articles[:limit]:
        if not isinstance(a, dict):
            continue
        body = a.get("content") or ""
        snippet = " ".join(str(body).split())[:_SNIPPET_CHARS]
        out.append({
            "title": a.get("title"),
            "url": a.get("url"),
            "source": a.get("mediaName"),
            "published": a.get("date"),
            "snippet": snippet,
        })
    return out


def _filter_periods(rows: list[dict], period: str) -> list[dict]:
    if period != "annual":
        return rows
    annual = [
        r for r in rows
        if str(r.get("REPORT_DATE", ""))[:10].endswith("-12-31")
    ]
    return annual or rows
