"""A-share sentiment / behavior tools: guba lexicon + overnight decomposition."""

from __future__ import annotations

import json
import logging
from typing import Any

from market.a_share_code import to_a_share_symbol
from market.envelope import clamp_int, err, ok
from market.market_data import fetch_one, is_a_share_symbol
from market.sentiment_data import (
    fetch_guba_posts,
    overnight_vs_intraday,
    score_guba_sentiment,
    summarize_overnight_intraday,
)
from tools.base import BaseTool

logger = logging.getLogger(__name__)


class GubaSentimentTool(BaseTool):
    name = "get_guba_sentiment"
    summary = "东方财富股吧帖子情绪（词典启发式）"
    description = (
        "抓取东财股吧列表页帖子，用中文看多/看空关键词对标题做启发式情绪打分。"
        'A 股专用。示例: {"code": "600519.SH", "pages": 2}'
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "A 股代码，如 600519 或 600519.SH"},
            "pages": {
                "type": "integer",
                "default": 1,
                "description": "抓取页数 1-3",
            },
        },
        "required": ["code"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        code = str(args.get("code") or "").strip()
        if not code:
            return err("code 不能为空")
        normalized = to_a_share_symbol(code)
        if not is_a_share_symbol(normalized):
            return err("仅支持 A 股代码")

        pages = clamp_int(args.get("pages"), 1, 1, 3)
        all_posts: list[dict[str, Any]] = []
        failed_pages: list[int] = []

        for page in range(1, pages + 1):
            try:
                posts = fetch_guba_posts(normalized, page=page)
                all_posts.extend(posts)
            except Exception as exc:
                logger.warning("get_guba_sentiment page %s failed: %s", page, exc)
                failed_pages.append(page)

        if not all_posts and failed_pages:
            return err(f"股吧抓取失败（pages={failed_pages}）")

        sentiment = score_guba_sentiment(all_posts)
        sample = all_posts[:5]
        quality = "partial" if failed_pages else "degraded"
        note = None
        if failed_pages:
            note = f"pages failed: {failed_pages}"
        elif not all_posts:
            note = "未解析到帖子标题"

        return ok(
            {
                "code": normalized,
                "pages_requested": pages,
                "pages_failed": failed_pages,
                "n_posts": len(all_posts),
                "sentiment": sentiment,
                "sample_posts": sample,
            },
            quality=quality,
            note=note,
            market="a_share",
            source="eastmoney_guba",
        )


class OvernightReturnsTool(BaseTool):
    name = "calc_overnight_returns"
    summary = "隔夜 vs 日内收益分解（A 股 OHLCV）"
    description = (
        "用日线 OHLCV 分解 overnight=open/prev_close-1 与 intraday=close/open-1，"
        "输出均值与近 N 日序列。A 股专用。"
        '示例: {"codes": ["600519.SH"], "start_date": "2024-01-01", "end_date": "2024-06-30", "last_n": 5}'
    )
    parameters = {
        "type": "object",
        "properties": {
            "codes": {"type": "array", "items": {"type": "string"}},
            "start_date": {"type": "string"},
            "end_date": {"type": "string"},
            "last_n": {"type": "integer", "default": 5},
        },
        "required": ["codes", "start_date", "end_date"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        codes = args.get("codes")
        if not isinstance(codes, list) or not codes:
            return err("codes 必须为非空数组")
        start = str(args.get("start_date") or "").strip()
        end = str(args.get("end_date") or "").strip()
        if not start or not end:
            return err("start_date 与 end_date 必填")
        last_n = clamp_int(args.get("last_n"), 5, 1, 60)

        results: dict[str, Any] = {}
        failed = 0
        for raw_code in codes:
            code = str(raw_code).strip().upper()
            if not is_a_share_symbol(code):
                results[code] = {"error": "A-share only (expect ######.SH|SZ|BJ)"}
                failed += 1
                continue
            rows, source = fetch_one(code, start, end)
            if not rows:
                results[code] = {"error": "no data", "source_tried": source}
                failed += 1
                continue
            series = overnight_vs_intraday(rows)
            summary = summarize_overnight_intraday(series, last_n=last_n)
            if "error" in summary:
                results[code] = {"error": summary["error"], "source": source}
                failed += 1
                continue
            results[code] = {
                "source": source,
                "summary": summary,
            }

        quality = "partial" if failed else "normal"
        note = f"{failed} symbol(s) failed" if failed else None
        return ok(
            {
                "start_date": start,
                "end_date": end,
                "last_n": last_n,
                "results": results,
            },
            quality=quality,
            note=note,
            market="a_share",
            source="market_data",
        )
