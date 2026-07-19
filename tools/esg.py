"""A-share ESG tools: carbon prices and sustainability disclosures."""

from __future__ import annotations

import logging
from typing import Any

from market.envelope import clamp_int, err, ok
from market.esg_data import (
    EsgDataError,
    fetch_carbon_prices,
    filter_carbon_prices,
    search_cninfo_esg,
)
from tools.base import BaseTool

logger = logging.getLogger(__name__)

_CNINFO_SEARCH_HINT = (
    "巨潮资讯网公告检索：http://www.cninfo.com.cn/new/fulltextSearch/full"
    "?searchkey={keyword}&sortName=time&sortType=desc"
)


class GetCarbonPricesTool(BaseTool):
    name = "get_carbon_prices"
    summary = "中国碳市场成交价（分交易所）"
    description = (
        "获取中国试点/全国碳市场及欧盟碳价日频数据（akshare energy_carbon_*）。"
        "可选 exchange 过滤：北京/上海/广州/湖北/深圳/重庆/EU 等。"
        "单位：元/吨；引用时注明 quality。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "exchange": {
                "type": "string",
                "description": "可选，交易所：bj/sh/gz/hb/sz/重庆/EU 或中文名",
            },
            "limit": {
                "type": "integer",
                "default": 60,
                "description": "每个交易所返回最近 N 条",
            },
        },
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        exchange = str(args.get("exchange") or "").strip() or None
        limit = clamp_int(args.get("limit"), 60, 1, 500)
        try:
            rows = fetch_carbon_prices()
        except EsgDataError as exc:
            return err(str(exc))
        except Exception as exc:
            return err(f"碳价拉取失败: {exc}")

        filtered = filter_carbon_prices(rows, exchange)
        if exchange and not filtered:
            return err(f"未找到交易所 {exchange} 的碳价数据")

        by_exchange: dict[str, list[dict[str, Any]]] = {}
        for row in filtered:
            key = str(row.get("exchange") or row.get("market") or "unknown")
            by_exchange.setdefault(key, []).append(row)

        series: list[dict[str, Any]] = []
        latest: list[dict[str, Any]] = []
        for ex, items in sorted(by_exchange.items()):
            items.sort(key=lambda x: x.get("trade_date") or "")
            trimmed = items[-limit:]
            series.append({"exchange": ex, "rows": trimmed, "count": len(trimmed)})
            if trimmed:
                latest.append({**trimmed[-1], "exchange": ex})

        quality = "degraded"
        note = "来源 akshare 公开聚合（tanjiaoyi 等），非交易所官方 API；EU 源可能缺失。"
        return ok(
            {
                "unit": "CNY_per_ton",
                "exchange_filter": exchange,
                "series": series,
                "latest": latest,
                "total_rows": len(filtered),
            },
            quality=quality,
            note=note,
            market="a_share",
            source="akshare.energy_carbon",
        )


class SearchEsgReportsTool(BaseTool):
    name = "search_esg_reports"
    summary = "检索 A 股 ESG/可持续发展公告"
    description = (
        "在巨潮资讯检索 ESG、可持续发展、社会责任等关键词公告。"
        "返回 title/date/url/code。优先 CNINFO HTTP，失败时降级 akshare。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "default": "ESG",
                "description": "检索关键词，如 ESG / 可持续发展 / 社会责任",
            },
            "code": {
                "type": "string",
                "description": "可选 A 股代码，如 600519.SH",
            },
            "limit": {"type": "integer", "default": 20},
        },
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        keyword = str(args.get("keyword") or "ESG").strip() or "ESG"
        code = str(args.get("code") or "").strip() or None
        limit = clamp_int(args.get("limit"), 20, 1, 30)
        try:
            reports, source, quality = search_cninfo_esg(
                keyword,
                limit,
                code=code,
            )
        except EsgDataError as exc:
            return err(str(exc))
        except Exception as exc:
            return err(f"ESG 公告检索失败: {exc}")

        if not reports:
            return ok(
                {
                    "keyword": keyword,
                    "code": code,
                    "count": 0,
                    "reports": [],
                    "cninfo_hint": _CNINFO_SEARCH_HINT.format(keyword=keyword),
                },
                quality="degraded",
                note="未命中公告，可调整关键词或上巨潮人工检索",
                market="a_share",
                source=source,
            )

        note = None
        if quality == "degraded":
            note = "CNINFO 主接口不可用，已降级 akshare 披露检索"
        return ok(
            {
                "keyword": keyword,
                "code": code,
                "count": len(reports),
                "reports": reports,
            },
            quality=quality,
            note=note,
            market="a_share",
            source=source,
        )


class GetEsgOverviewTool(BaseTool):
    name = "get_esg_overview"
    summary = "ESG 研究入口（碳价 + 公告检索指引）"
    description = (
        "A 股 ESG 概览：最新碳价快照、巨潮公告检索方式；"
        "若提供 code 则附带近期 ESG 相关公告命中。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "可选 A 股代码，如 600519.SH",
            },
            "keyword": {
                "type": "string",
                "default": "ESG",
            },
            "report_limit": {"type": "integer", "default": 5},
        },
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        code = str(args.get("code") or "").strip() or None
        keyword = str(args.get("keyword") or "ESG").strip() or "ESG"
        report_limit = clamp_int(args.get("report_limit"), 5, 1, 10)

        carbon_block: dict[str, Any] = {"latest": [], "note": None, "error": None}
        try:
            rows = fetch_carbon_prices()
            by_ex: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                key = str(row.get("exchange") or row.get("market") or "unknown")
                by_ex.setdefault(key, []).append(row)
            latest = []
            for ex, items in by_ex.items():
                items.sort(key=lambda x: x.get("trade_date") or "")
                if items:
                    latest.append({**items[-1], "exchange": ex})
            carbon_block["latest"] = latest
            carbon_block["note"] = (
                "国内碳价为试点/全国市场聚合，元/吨；与个股 ESG 评级无直接换算关系。"
            )
        except EsgDataError as exc:
            carbon_block["error"] = str(exc)
        except Exception as exc:
            carbon_block["error"] = f"碳价拉取失败: {exc}"

        reports: list[dict[str, Any]] = []
        report_source = "cninfo.hisAnnouncement"
        report_quality = "normal"
        report_error = None
        try:
            reports, report_source, report_quality = search_cninfo_esg(
                keyword,
                report_limit,
                code=code,
            )
        except EsgDataError as exc:
            report_error = str(exc)
        except Exception as exc:
            report_error = f"公告检索失败: {exc}"

        quality = report_quality
        if carbon_block.get("error"):
            quality = "degraded"
        note_parts = [
            "A 股 ESG 以交易所/证监会披露为准；第三方评级需交叉验证。",
            _CNINFO_SEARCH_HINT.format(keyword=keyword),
        ]
        if report_error:
            note_parts.append(f"公告检索: {report_error}")

        data = {
            "code": code,
            "keyword": keyword,
            "carbon": carbon_block,
            "cninfo_usage": {
                "portal": "http://www.cninfo.com.cn/new/disclosure/stock",
                "search": _CNINFO_SEARCH_HINT.format(keyword=keyword),
                "tips": "关键词可试：ESG、可持续发展、社会责任、环境信息",
            },
            "recent_reports": reports,
            "report_source": report_source,
        }
        if code and not reports and not report_error:
            data["report_note"] = f"未找到 {code} 近期「{keyword}」相关公告"

        return ok(
            data,
            quality=quality,
            note="；".join(note_parts),
            market="a_share",
            source="esg_overview",
        )
