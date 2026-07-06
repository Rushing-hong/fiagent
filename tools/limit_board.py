"""Limit board tool: daily limit-up / limit-down pool analysis.

Essential A-share daily data: which stocks hit the limit, when they locked,
how many times the board broke, consecutive limit days, and sector attribution.
"""

from __future__ import annotations

from typing import Any

from market.akshare_data import get_limit_board
from tools.base import BaseTool


class LimitBoardTool(BaseTool):
    name = "get_limit_board"
    summary = "涨停板复盘（涨停/跌停/炸板 含封板时间+连板数+板块）"
    description = (
        "获取 A 股每日涨停板、跌停板、炸板股票池。含封板时间、炸板次数、"
        "连板天数、封单金额、换手率、所属行业板块。\n"
        "A 股散户每日必看数据，也是打板/龙头战法策略的核心输入。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "default": "",
                "description": "交易日 YYYY-MM-DD，为空则最新交易日",
            },
        },
    }
    is_readonly = True

    def execute(self, args: dict[str, Any], ctx: Any) -> str:
        return get_limit_board(date=str(args.get("date", "")))
