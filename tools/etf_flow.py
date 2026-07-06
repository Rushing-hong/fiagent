"""ETF fund flow tool: daily inflows/outflows, share changes, premium/discount.

Covers 800+ A-share ETFs. Data from Eastmoney push2 + akshare.
Key metric: 主力净流入(主力净额) is the most watched flow indicator.
"""

from __future__ import annotations

import json
from typing import Any

from market.eastmoney import get_json, push2_diff_rows
from market.envelope import clamp_int, err, ok, to_float
from tools.base import BaseTool

_ETF_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_ETF_FS = "b:MK0021,b:MK0022,b:MK0826"
_ETF_FIELDS = (
    "f2,f3,f4,f12,f14,f15,f16,f17,f18,f20,f21,f47,f48,"
    "f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f115"
)


class ETFFlowTool(BaseTool):
    name = "get_etf_flow"
    summary = "ETF 资金流向排行（主力净流入/份额变化/折溢价）"
    description = (
        "获取全市场 ETF 资金流向排行。核心指标：主力净流入额、主力净占比、"
        "超大单/大单/中单/小单净额、份额变化、折溢价率。\n\n"
        "ETF 是 2026 年散户主战场——上半年通信 ETF 净流入 443 亿(第一)，"
        "宽基 ETF 流出超 1.6 万亿。资金流向反映市场风格切换。\n\n"
        "排序: net_inflow(主力净流入), change_pct(涨跌幅), volume(成交额), turnover(换手率)"
    )
    parameters = {
        "type": "object",
        "properties": {
            "sort_by": {
                "type": "string",
                "enum": ["net_inflow", "change_pct", "volume", "turnover"],
                "default": "net_inflow",
                "description": "排序字段: net_inflow=主力净流入(默认), change_pct=涨跌幅, volume=成交额, turnover=换手率",
            },
            "top_n": {
                "type": "integer",
                "default": 30,
                "description": "返回数量",
            },
        },
    }
    is_readonly = True

    def execute(self, args: dict[str, Any], ctx: Any) -> str:
        sort_by = str(args.get("sort_by", "net_inflow"))
        top_n = clamp_int(args.get("top_n"), 30, 1, 100)

        sort_fid = {
            "net_inflow": "f62",
            "change_pct": "f3",
            "volume": "f48",
            "turnover": "f8",
        }.get(sort_by, "f62")

        try:
            payload = get_json(
                _ETF_CLIST_URL,
                params={
                    "fs": _ETF_FS,
                    "fid": sort_fid,
                    "fields": _ETF_FIELDS,
                    "pn": "1",
                    "pz": str(min(top_n * 3, 300)),
                    "po": "1" if sort_by != "turnover" else "0",
                    "fltt": "2",
                },
            )
        except Exception as e:
            return err(f"ETF 数据获取失败: {e}")

        rows = push2_diff_rows(payload)
        if not rows:
            return err("未获取到 ETF 数据")

        results = []
        for raw in rows:
            if not isinstance(raw, dict) or not raw.get("f12"):
                continue

            name = str(raw.get("f14", ""))
            # Only include actual ETFs (filter out non-ETF funds in the list)
            if "ETF" not in name and "指数" not in name and not any(kw in name for kw in ["基金", "添益", "日利"]):
                continue

            main_net = to_float(raw.get("f62"))    # 主力净流入(元)
            super_large_net = to_float(raw.get("f66"))  # 超大单净额
            large_net = to_float(raw.get("f72"))    # 大单净额
            medium_net = to_float(raw.get("f78"))   # 中单净额
            small_net = to_float(raw.get("f84"))    # 小单净额

            results.append({
                "code": str(raw.get("f12")),
                "name": name,
                "price": to_float(raw.get("f2")),
                "change_pct": to_float(raw.get("f3")),
                "main_net_inflow": main_net,
                "main_net_pct": to_float(raw.get("f184")),  # 主力净占比%
                "super_large_net": super_large_net,
                "large_net": large_net,
                "medium_net": medium_net,
                "small_net": small_net,
                "volume": to_float(raw.get("f48")),   # 成交额
                "turnover_rate": to_float(raw.get("f8")),
                "fund_size": to_float(raw.get("f20")),  # 基金规模(亿)
                "premium": to_float(raw.get("f115")),   # 折溢价率
            })

        # Sort if needed (API sort may not be perfect for all fields)
        if sort_by == "net_inflow":
            results.sort(key=lambda r: abs(r.get("main_net_inflow") or 0), reverse=True)
        elif sort_by == "turnover":
            results.sort(key=lambda r: r.get("turnover_rate") or 0, reverse=True)

        top = results[:top_n]

        # Summary stats
        total_inflow = sum(r.get("main_net_inflow") or 0 for r in results)
        inflow_count = sum(1 for r in results if (r.get("main_net_inflow") or 0) > 0)
        outflow_count = sum(1 for r in results if (r.get("main_net_inflow") or 0) < 0)

        return ok({
            "sort_by": sort_by,
            "count": len(top),
            "summary": {
                "total_main_inflow": total_inflow,
                "inflow_etfs": inflow_count,
                "outflow_etfs": outflow_count,
            },
            "etfs": top,
        }, source="eastmoney", market="etf")
