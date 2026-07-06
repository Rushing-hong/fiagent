"""IPO calendar tool: new stock subscription dates, listing dates, first-day returns."""

from __future__ import annotations

from typing import Any

from market.envelope import clamp_int, err, ok, to_float
from tools.base import BaseTool


class IPOCalendarTool(BaseTool):
    name = "get_ipo_calendar"
    summary = "新股日历（申购日/上市日/中签率/首日涨幅）"
    description = (
        "获取 A 股 IPO 新股日历。包含：申购代码、申购日期、发行价、发行市盈率、"
        "行业市盈率、网上中签率、上市日期、首日涨跌幅。\n"
        "A 股打新是散户核心策略——2025 年新股首日平均涨幅 368%。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "top_n": {"type": "integer", "default": 20, "description": "返回最近 N 只新股"},
        },
    }
    is_readonly = True

    def execute(self, args: dict[str, Any], ctx: Any) -> str:
        try:
            import akshare as ak
        except ImportError:
            return err("akshare 未安装。请执行: pip install akshare")

        top_n = clamp_int(args.get("top_n"), 20, 1, 50)
        try:
            df = ak.stock_ipo_benefit_em()
            if df is None or df.empty:
                return err("未获取到 IPO 数据")
        except Exception as e:
            return err(f"IPO 数据获取失败: {e}")

        records = []
        for _, row in df.head(top_n).iterrows():
            records.append({
                "code": str(row.get("股票代码", "")),
                "name": str(row.get("股票简称", "")),
                "ipo_price": to_float(row.get("发行价格")),
                "ipo_pe": to_float(row.get("发行市盈率")),
                "industry_pe": to_float(row.get("行业市盈率")),
                "ipo_date": str(row.get("申购日期", "")),
                "list_date": str(row.get("上市日期", "")),
                "lottery_rate": to_float(row.get("网上申购中签率")),
                "first_day_return": to_float(row.get("首日涨跌幅")),
            })

        return ok({"count": len(records), "ipos": records}, source="akshare", market="a_share")
