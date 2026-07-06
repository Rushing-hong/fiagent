"""Futures market tools: quote, OHLCV, position ranking, spot prices.

Covers: CFFEX (股指/国债), SHFE/INE (金属/能源), DCE (黑色/农产品),
ZCE (化工/农产品), GFEX (新能源).
"""

from __future__ import annotations

from typing import Any

from market.akshare_data import (
    _EXCHANGE_MAP,
    _HOT_CONTRACTS,
    get_commodity_spot,
    get_futures_daily,
    get_futures_main_list,
    get_futures_position_ranking,
)
from tools.base import BaseTool


class FuturesQuoteTool(BaseTool):
    name = "get_futures_quote"
    summary = "期货主力合约实时/历史行情（6大交易所热门品种）"
    description = (
        "获取期货主力合约行情，覆盖中金所/上期所/能源中心/大商所/郑商所/广期所。\n"
        f"交易所: {', '.join(_EXCHANGE_MAP.values())}。\n"
        "热门品种: IF/IC/IM/IH(股指), T/TF/TS(国债), AU/AG(贵金属), "
        "RB/I/JM(黑色), LC/SI(新能源), TA/MA/SA(化工), M/Y/P(农产品)。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["main_list", "daily", "position", "spot"],
                "default": "main_list",
                "description": "main_list=主力合约列表, daily=历史日线, position=持仓排名, spot=现货价格",
            },
            "symbol": {
                "type": "string",
                "description": "合约代码（daily/position/spot 模式必填），如 RB2501、IF2503",
            },
            "exchange": {
                "type": "string",
                "enum": ["", "CFFEX", "SHFE", "INE", "DCE", "ZCE", "GFEX"],
                "default": "",
                "description": "交易所筛选（main_list 模式可选）",
            },
            "start_date": {"type": "string", "description": "开始日期 YYYY-MM-DD（daily 模式）"},
            "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD（daily 模式）"},
            "indicator": {
                "type": "string",
                "enum": ["volume", "long", "short"],
                "default": "volume",
                "description": "持仓排名指标（position 模式）",
            },
        },
    }
    is_readonly = True

    def execute(self, args: dict[str, Any], ctx: Any) -> str:
        mode = args.get("mode", "main_list")

        if mode == "main_list":
            exchange = args.get("exchange") or None
            return get_futures_main_list(exchange=exchange)

        if mode == "daily":
            symbol = str(args.get("symbol", "")).strip().upper()
            if not symbol:
                return '{"ok": false, "error": "symbol 必填"}'
            start = str(args.get("start_date", ""))
            end = str(args.get("end_date", ""))
            if not start or not end:
                return '{"ok": false, "error": "start_date 和 end_date 必填"}'
            return get_futures_daily(symbol=symbol, start_date=start, end_date=end)

        if mode == "position":
            symbol = str(args.get("symbol", "")).strip().upper()
            if not symbol:
                return '{"ok": false, "error": "symbol 必填"}'
            return get_futures_position_ranking(symbol=symbol, indicator=args.get("indicator", "volume"))

        if mode == "spot":
            symbol = str(args.get("symbol", "")).strip()
            if not symbol:
                return '{"ok": false, "error": "symbol 必填（如 螺纹钢、铁矿石、铜）"}'
            return get_commodity_spot(symbol=symbol)

        return '{"ok": false, "error": "未知 mode"}'
