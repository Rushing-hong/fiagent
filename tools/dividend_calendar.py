"""Dividend calendar tool: ex-rights dates, payout ratios, dividend yield.

A-share dividend data from CNINFO (巨潮资讯), covering plan announcement dates,
ex-rights dates, record dates, cash dividend per share, bonus share ratios,
and dividend yield.
"""

from __future__ import annotations

from typing import Any

from market.akshare_data import get_dividend_calendar
from tools.base import BaseTool


class DividendCalendarTool(BaseTool):
    name = "get_dividend_calendar"
    summary = "A股分红除权日历（含每股分红、送转股、股息率）"
    description = (
        "获取 A 股分红除权除息日历数据。包含：预案公告日、除权除息日、"
        "股权登记日、每股派息金额、送股/转增比例、股息率。\n"
        "支持按年份筛选。A 股股息红利税：持股≤1月 20%、1月-1年 10%、>1年 0%。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "default": "",
                "description": "个股代码（如 600519），为空则全市场",
            },
            "year": {
                "type": "string",
                "default": "",
                "description": "年份（如 2026），为空则当前年份",
            },
        },
    }
    is_readonly = True

    def execute(self, args: dict[str, Any], ctx: Any) -> str:
        return get_dividend_calendar(
            code=str(args.get("code", "")),
            year=str(args.get("year", "")),
        )
