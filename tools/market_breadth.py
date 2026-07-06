"""Market breadth indicators: TMT concentration, sector turnover, market sentiment.

Monitors: AI/TMT sector trading volume concentration (the #1 2026 market signal),
limit-up/down ratio, sector rotation heatmap.
"""

from __future__ import annotations

import json
from typing import Any

from market.eastmoney import get_json, push2_diff_rows
from market.envelope import err, ok, to_float
from tools.base import BaseTool

_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"

# TMT sector codes (申万 2021)
_TMT_BOARDS = {
    "BK0447": "计算机",
    "BK0446": "电子",
    "BK0448": "通信",
    "BK0449": "传媒",
}

# Major sector groups for breadth analysis
_SECTOR_GROUPS: dict[str, dict[str, list[str]]] = {
    "TMT":     {"boards": list(_TMT_BOARDS.keys())},
    "金融":     {"boards": ["BK0445", "BK0450", "BK0451"]},  # 银行/非银/房地产
    "消费":     {"boards": ["BK0452", "BK0453", "BK0454", "BK0456"]},  # 食品饮料/家电/汽车/医药
    "周期":     {"boards": ["BK0440", "BK0441", "BK0442", "BK0443"]},  # 煤炭/有色/钢铁/化工
    "新能源":   {"boards": ["BK0457", "BK0458"]},  # 电力设备/公用事业
}


class MarketBreadthTool(BaseTool):
    name = "get_market_breadth"
    summary = "市场宽度指标（TMT成交占比/板块热度/情绪信号）"
    description = (
        "计算 A 股市场宽度指标，辅助判断市场风格和情绪。\n\n"
        "核心指标:\n"
        "- TMT成交占比: AI/算力产业链成交额占全市场比例。>35%=极度拥挤, <15%=冷清\n"
        "- 板块成交排名: 各行业成交额排行，识别资金主攻方向\n"
        "- 板块涨跌比: 上涨板块数/总板块数\n\n"
        "2026年TMT成交占比持续>35%，是当前最重要的市场情绪指标。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["full", "tmt_only", "sector_ranking"],
                "default": "full",
                "description": "full=全部指标, tmt_only=仅TMT集中度, sector_ranking=板块成交排行",
            },
        },
    }
    is_readonly = True

    def execute(self, args: dict[str, Any], ctx: Any) -> str:
        mode = str(args.get("mode", "full"))

        try:
            # Fetch all sector data (max 200 sectors)
            payload = get_json(
                _CLIST_URL,
                params={
                    "fs": "m:90+t:2",  # 行业板块
                    "fields": "f2,f3,f4,f12,f14,f20,f48,f104,f105",
                    "pn": "1",
                    "pz": "200",
                    "po": "1",
                    "fid": "f20",  # sort by turnover
                    "fltt": "2",
                },
            )
        except Exception as e:
            return err(f"板块数据获取失败: {e}")

        rows = push2_diff_rows(payload)
        if not rows:
            return err("未获取到板块数据")

        # Parse all sectors
        sectors: dict[str, dict[str, Any]] = {}
        total_turnover = 0.0
        up_count = 0
        down_count = 0

        for raw in rows:
            if not isinstance(raw, dict):
                continue
            code = str(raw.get("f12", ""))
            name = str(raw.get("f14", ""))
            turnover = to_float(raw.get("f20")) or 0.0
            change_pct = to_float(raw.get("f3")) or 0.0
            up = int(raw.get("f104") or 0)
            down = int(raw.get("f105") or 0)

            sectors[code] = {
                "code": code, "name": name,
                "turnover": turnover, "change_pct": change_pct,
                "up_count": up, "down_count": down,
            }
            total_turnover += turnover

            if change_pct > 0:
                up_count += 1
            elif change_pct < 0:
                down_count += 1

        total_board_count = len(sectors)
        if total_board_count == 0:
            return err("板块数据为空")

        result: dict[str, Any] = {}

        # TMT concentration
        if mode in ("full", "tmt_only"):
            tmt_turnover = sum(sectors.get(c, {}).get("turnover", 0) for c in _TMT_BOARDS)
            tmt_pct = round(tmt_turnover / total_turnover * 100, 2) if total_turnover > 0 else 0

            tmt_detail = {}
            for code, cname in _TMT_BOARDS.items():
                s = sectors.get(code, {})
                tmt_detail[cname] = {
                    "code": code,
                    "turnover": s.get("turnover"),
                    "change_pct": s.get("change_pct"),
                    "pct_of_total": round(s.get("turnover", 0) / total_turnover * 100, 2) if total_turnover > 0 else 0,
                }

            # Crowding assessment
            if tmt_pct > 40:
                tmt_crowding = "极度拥挤 — 市场极度集中于AI赛道，警惕风格切换"
            elif tmt_pct > 30:
                tmt_crowding = "高度拥挤 — AI仍是主线但分化加剧"
            elif tmt_pct > 20:
                tmt_crowding = "温和 — AI与其他板块均衡"
            elif tmt_pct > 10:
                tmt_crowding = "冷清 — AI非当前主线"
            else:
                tmt_crowding = "极冷 — AI赛道被市场忽视"

            result["tmt_concentration"] = {
                "tmt_turnover": tmt_turnover,
                "total_turnover": total_turnover,
                "tmt_pct": tmt_pct,
                "crowding_level": tmt_crowding,
                "detail": tmt_detail,
                "thresholds": {
                    "extreme": 40, "high": 30, "moderate": 20, "low": 10,
                    "description": "TMT成交占比阈值: >40%极度拥挤, >30%高度拥挤, >20%温和, >10%冷清",
                },
            }

        # Sector ranking
        if mode in ("full", "sector_ranking"):
            ranked = sorted(sectors.values(), key=lambda s: s["turnover"], reverse=True)

            # Top sectors by turnover
            result["top_sectors"] = ranked[:15]

            # Group-level analysis
            group_stats = {}
            for gname, gcfg in _SECTOR_GROUPS.items():
                g_turnover = sum(sectors.get(c, {}).get("turnover", 0) for c in gcfg["boards"])
                g_pct = round(g_turnover / total_turnover * 100, 2) if total_turnover > 0 else 0
                group_stats[gname] = {"turnover": g_turnover, "pct_of_total": g_pct}

            result["sector_groups"] = group_stats

        # Market breadth
        breadth_pct = round(up_count / total_board_count * 100, 2) if total_board_count > 0 else 0
        if breadth_pct > 70:
            breadth_signal = "普涨 — 市场情绪高涨"
        elif breadth_pct > 50:
            breadth_signal = "偏多 — 多数板块上涨"
        elif breadth_pct > 30:
            breadth_signal = "偏空 — 多数板块下跌"
        else:
            breadth_signal = "普跌 — 市场情绪低迷"

        result["market_breadth"] = {
            "total_boards": total_board_count,
            "up_boards": up_count,
            "down_boards": down_count,
            "breadth_pct": breadth_pct,
            "signal": breadth_signal,
        }

        result["data_time"] = "实时"

        return ok(result, source="eastmoney", market="a_share")
