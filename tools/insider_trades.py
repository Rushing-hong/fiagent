"""Insider trading tool: major shareholder / executive buy/sell records.

A-share insider trading data is one of the strongest signals — major shareholders
buying often indicates bottom, selling often indicates top.
"""

from __future__ import annotations

import json
from typing import Any

from market.envelope import clamp_int, err, ok, to_float
from tools.base import BaseTool


class InsiderTradesTool(BaseTool):
    name = "get_insider_trades"
    summary = "高管/大股东增减持记录"
    description = (
        "获取 A 股大股东/高管增减持记录。产业资本动向是 A 股最强信号之一。\n"
        "大股东增持 → 底部信号（对自己公司最有信心的是老板自己）\n"
        "大股东减持 → 顶部信号（套现动机远强于喊多）\n\n"
        "数据源: akshare（东财/巨潮资讯）"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "个股代码如 600519.SH。为空则全市场最近披露",
            },
            "days": {
                "type": "integer",
                "default": 30,
                "description": "最近 N 天的记录",
            },
            "holder_type": {
                "type": "string",
                "enum": ["all", "executive", "major_shareholder"],
                "default": "all",
                "description": "all=全部, executive=高管, major_shareholder=大股东(>=5%)",
            },
            "top_n": {
                "type": "integer",
                "default": 30,
            },
        },
        "required": [],
    }
    is_readonly = True

    def execute(self, args: dict[str, Any], ctx: Any) -> str:
        try:
            import akshare as ak
        except ImportError:
            return err("akshare 未安装。请执行: pip install akshare")

        code = str(args.get("code", "")).strip()
        days = clamp_int(args.get("days"), 30, 1, 365)
        holder_type = str(args.get("holder_type", "all"))
        top_n = clamp_int(args.get("top_n"), 30, 1, 100)

        try:
            if code:
                bare = code.split(".")[0].zfill(6) if "." in code else code.zfill(6)
                df = ak.stock_hold_management_detail(symbol=bare)
            else:
                # Market-wide latest
                df = ak.stock_hold_management_detail()
            if df is None or df.empty:
                return err(f"未获取到 {code or '全市场'} 的增减持数据")
        except Exception as e:
            return err(f"增减持数据获取失败: {e}")

        records = []
        for _, row in df.iterrows():
            name = str(row.get("股东名称", row.get("name", "")))
            side_raw = str(row.get("变动方向", row.get("direction", "")))
            if "增" in side_raw or "buy" in side_raw.lower():
                side = "增持"
                change_val = to_float(row.get("变动金额", row.get("amount", 0)))
            elif "减" in side_raw or "sell" in side_raw.lower():
                side = "减持"
                change_val = -abs(to_float(row.get("变动金额", row.get("amount", 0))) or 0)
            else:
                side = "未知"
                change_val = 0

            # Filter by holder type
            is_exec = any(kw in name for kw in ["董事", "监事", "高管", "总裁", "经理"])
            is_major = "持股5%" in str(row.get("变动原因", "")) or to_float(row.get("变动后持股比例", 0) or 0) >= 5
            if holder_type == "executive" and not is_exec:
                continue
            if holder_type == "major_shareholder" and not is_major:
                continue

            records.append({
                "date": str(row.get("变动日期", row.get("date", "")))[:10],
                "code": str(row.get("证券代码", row.get("code", ""))),
                "name": name,
                "position": str(row.get("职务", row.get("position", ""))),
                "side": side,
                "change_shares": to_float(row.get("变动数量", row.get("shares", 0))),
                "change_amount": change_val,
                "avg_price": to_float(row.get("成交均价", row.get("price", 0))),
                "after_holding_pct": to_float(row.get("变动后持股比例", 0)),
                "reason": str(row.get("变动原因", row.get("reason", ""))),
                "holder_type": "高管" if is_exec else "大股东" if is_major else "其他",
            })

        if not records:
            return err("筛选后无结果，尝试调整 holder_type 或扩大 days")

        # Sort by absolute change amount desc
        records.sort(key=lambda r: abs(r["change_amount"]), reverse=True)
        top = records[:top_n]

        # Summary
        buy_count = sum(1 for r in records if r["side"] == "增持")
        sell_count = sum(1 for r in records if r["side"] == "减持")
        total_buy = sum(r["change_amount"] for r in records if r["side"] == "增持")
        total_sell = sum(abs(r["change_amount"]) for r in records if r["side"] == "减持")

        if buy_count + sell_count > 0:
            if buy_count > sell_count * 2:
                signal = "产业资本净增持 — 偏多信号"
            elif sell_count > buy_count * 2:
                signal = "产业资本净减持 — 偏空信号"
            else:
                signal = "增减持平 — 中性"
        else:
            signal = "无显著增减"

        return ok({
            "filter": {"code": code or "全市场", "days": days, "holder_type": holder_type},
            "signal": signal,
            "summary": {
                "total_records": len(records),
                "buy_count": buy_count,
                "sell_count": sell_count,
                "total_buy_amount": total_buy,
                "total_sell_amount": total_sell,
            },
            "trades": top,
        }, source="akshare", market="a_share")
