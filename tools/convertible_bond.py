"""Convertible bond tools: full market list + screening.

A-share convertible bonds (可转债) are hybrid instruments with bond-floor
protection and equity optionality. Core strategies: double-low (双低) screening,
deep-discount bargain hunting, and clause gaming (下修/强赎/回售).
"""

from __future__ import annotations

from typing import Any

from market.akshare_data import get_cb_list, screen_cb
from tools.base import BaseTool


class CBListTool(BaseTool):
    name = "get_cb_list"
    summary = "全市场可转债列表（含双低值、溢价率、到期收益率）"
    description = (
        "获取全市场可转债快照数据，包含：转债价格、转股价、转股溢价率、"
        "纯债价值、到期收益率(YTM)、信用评级、回售触发价、强赎触发价、"
        "双低值（=转债价+溢价率）。数据来源：集思录。"
    )
    parameters = {
        "type": "object",
        "properties": {},
    }
    is_readonly = True

    def execute(self, args: dict[str, Any], ctx: Any) -> str:
        return get_cb_list()


class ScreenCBTool(BaseTool):
    name = "screen_cb"
    summary = "可转债筛选（双低/低价/YTM策略）"
    description = (
        "按条件筛选可转债，支持三大经典策略：\n"
        "1. 双低策略: 转债价+溢价率 < 阈值（如<120）\n"
        "2. 低价策略: 转债价接近债底（如<100元）\n"
        "3. YTM策略: 到期收益率为正\n"
        "可叠加信用评级过滤。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "max_price": {
                "type": "number",
                "default": 130,
                "description": "最高转债价格",
            },
            "max_premium": {
                "type": "number",
                "default": 30,
                "description": "最高转股溢价率(%)",
            },
            "max_double_low": {
                "type": "number",
                "description": "最高双低值（设此值则忽略 max_price/max_premium）",
            },
            "min_rating": {
                "type": "string",
                "default": "",
                "enum": ["", "AA", "AA+", "AAA"],
                "description": "最低信用评级",
            },
            "sort_by": {
                "type": "string",
                "enum": ["double_low", "ytm_rt", "premium_rt"],
                "default": "double_low",
            },
            "top_n": {
                "type": "integer",
                "default": 20,
                "description": "返回数量",
            },
        },
    }
    is_readonly = True

    def execute(self, args: dict[str, Any], ctx: Any) -> str:
        return screen_cb(
            max_price=float(args.get("max_price", 130)),
            max_premium=float(args.get("max_premium", 30)),
            max_double_low=args.get("max_double_low") and float(args["max_double_low"]),
            min_rating=str(args.get("min_rating", "")),
            sort_by=str(args.get("sort_by", "double_low")),
            top_n=int(args.get("top_n", 20)),
        )
