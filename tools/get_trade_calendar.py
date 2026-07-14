"""交易日历查询工具（底层模块供回测自动消费）。"""

from __future__ import annotations

from market.envelope import err, normalize_meta, now_as_of, ok
from market.trade_calendar import (
    invalidate_calendar_cache,
    is_trading_day,
    refresh_calendar_cache,
    session_hint,
    trading_days,
)
from tools.base import BaseTool


class GetTradeCalendarTool(BaseTool):
    name = "get_trade_calendar"
    summary = "A股交易日历（是否开市/区间交易日）"
    description = (
        "查询上交所/深交所交易日（新浪历史列表缓存）。"
        "回测引擎会自动消费同一日历模块；本工具供显式查询。"
        "mode=check|range|refresh。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["check", "range", "refresh"],
                "default": "check",
            },
            "date": {"type": "string", "description": "check 用 YYYY-MM-DD"},
            "start_date": {"type": "string"},
            "end_date": {"type": "string"},
        },
    }
    is_readonly = True
    repeatable = True

    def execute(self, args: dict, ctx) -> str:
        mode = str(args.get("mode") or "check")
        meta = normalize_meta(
            source="akshare.tool_trade_date_hist_sina",
            fetch_time=now_as_of(),
            frequency="daily",
            unit="none",
        )
        if mode == "refresh":
            n = refresh_calendar_cache(force=True)
            invalidate_calendar_cache()
            return ok(
                {"refreshed": True, "n_days": n},
                market="a_share",
                tool="get_trade_calendar",
                _meta=meta,
            )
        if mode == "range":
            s = str(args.get("start_date") or "")
            e = str(args.get("end_date") or "")
            if not s or not e:
                return err("range 需要 start_date 与 end_date")
            days = trading_days(s, e)
            return ok(
                {"start_date": s[:10], "end_date": e[:10], "count": len(days), "days": days},
                market="a_share",
                tool="get_trade_calendar",
                _meta=meta,
            )
        # check
        d = str(args.get("date") or now_as_of()[:10])
        hint = session_hint(d)
        return ok(
            {"date": d[:10], "is_trading_day": is_trading_day(d), **hint},
            market="a_share",
            tool="get_trade_calendar",
            _meta=meta,
        )
