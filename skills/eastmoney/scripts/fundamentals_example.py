#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""研报舆情 + 财报基本面研究示例（A 股）。

在仓库根目录执行：`python skills/eastmoney/scripts/fundamentals_example.py`
无需 token；研报走东财 + 同花顺一致预期；财报走东财 F10。
"""

from __future__ import annotations

import json

from tools.stock_disclosure import ShareholderCountTool
from tools.stock_research import FinancialStatementsTool, ResearchReportsTool


def broker_consensus(code: str, limit: int = 10) -> None:
    """打印券商研报评级分布与一致预期 EPS。"""
    envelope = json.loads(
        ResearchReportsTool().execute({"code": code, "limit": limit}, None)
    )
    if not envelope.get("ok"):
        print(f"研报获取失败：{envelope.get('error')}")
        return
    data = envelope["data"]
    ratings = [r["rating"] for r in data["reports"] if r.get("rating")]
    print(f"{code} 近 {limit} 篇研报评级：{ratings}")
    print(f"  一致预期 EPS：{data.get('consensus_eps')}")


def a_share_indicators(code: str) -> None:
    """打印 A 股主要指标的最新报告期数。"""
    envelope = json.loads(
        FinancialStatementsTool().execute(
            {"code": code, "statement": "indicators", "period": "annual"}, None
        )
    )
    if not envelope.get("ok"):
        print(f"财报获取失败：{envelope.get('error')}")
        return
    periods = envelope["data"].get("periods", [])
    print(f"{code} 主要指标报告期数：{len(periods)}（来源 {envelope.get('source')}）")


def holder_trend(code: str) -> None:
    """打印 A 股股东户数环比趋势（最新两期）。"""
    envelope = json.loads(ShareholderCountTool().execute({"code": code}, None))
    if not envelope.get("ok"):
        print(f"股东户数获取失败：{envelope.get('error')}")
        return
    for period in envelope["data"]["periods"][:2]:
        print(
            f"  {period['end_date']} 户数={period['holder_count']} "
            f"环比={period['holder_count_change_pct']}%"
        )


def main() -> None:
    print("===== Eastmoney 基本面 + 研报研究（A 股）=====")
    code = "600519.SH"
    broker_consensus(code, limit=10)
    holder_trend(code)
    a_share_indicators(code)


if __name__ == "__main__":
    main()
