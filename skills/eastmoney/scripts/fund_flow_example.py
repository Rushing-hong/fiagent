#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""资金流向 + 板块联动研究示例（A 股）。

在仓库根目录执行：`python skills/eastmoney/scripts/fund_flow_example.py`
无需 token；请求经东方财富共享 IP 限速层节流。
"""

from __future__ import annotations

import json

from tools.stock_disclosure import SectorInfoTool
from tools.stock_flow import FundFlowTool


def study_main_force(code: str, days: int = 30) -> dict | None:
    """读取一只股票近 N 日的主力净流入序列。"""
    envelope = json.loads(
        FundFlowTool().execute({"codes": [code], "period": "daily", "days": days}, None)
    )
    if not envelope.get("ok"):
        print(f"资金流向获取失败：{envelope.get('error')}")
        return None
    result = envelope["data"].get(code)
    rows = result.get("rows", []) if result else []
    print(f"{code} 近 {len(rows)} 日资金流向（最后一行）：{rows[-1] if rows else '无'}")
    return result


def study_sectors(code: str) -> None:
    """列出该股票所属行业/概念板块，并打印今日行业涨幅榜前 5。"""
    membership = json.loads(SectorInfoTool().execute({"code": code}, None))
    if membership.get("ok"):
        boards = membership["data"].get("boards", [])
        print(f"{code} 所属板块：{[b['board_name'] for b in boards]}")

    ranking = json.loads(SectorInfoTool().execute({"mode": "ranking", "limit": 5}, None))
    if ranking.get("ok"):
        for board in ranking["data"]["boards"]:
            print(f"  {board['board_name']}: {board['change_pct']}%")


def main() -> None:
    print("===== Eastmoney 资金流向 + 板块研究（A 股）=====")
    code = "600519.SH"
    study_main_force(code, days=30)
    study_sectors(code)


if __name__ == "__main__":
    main()
