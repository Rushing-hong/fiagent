#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代码搜索 + 全市场选股研究示例（A 股）。

在仓库根目录执行：`python skills/eastmoney/scripts/screen_search_example.py`
无需 token；请求经东方财富共享 IP 限速层节流。
"""

from __future__ import annotations

import json

from tools.stock_market import ScreenMarketTool, SearchSymbolTool


def resolve_symbol(query: str, limit: int = 5) -> str | None:
    """把名称/片段解析为最佳候选 symbol。"""
    envelope = json.loads(
        SearchSymbolTool().execute({"query": query, "limit": limit}, None)
    )
    candidates = envelope.get("data", {}).get("candidates", [])
    print(f"'{query}' 候选：{[c['symbol'] for c in candidates]}")
    return candidates[0]["symbol"] if candidates else None


def top_movers(*, ascending: bool = False, top_n: int = 10) -> None:
    """打印 A 股今日涨幅榜或跌幅榜前 N。"""
    envelope = json.loads(
        ScreenMarketTool().execute(
            {
                "market": "a",
                "sort_by": "change_pct",
                "top_n": top_n,
                "ascending": ascending,
            },
            None,
        )
    )
    if not envelope.get("ok"):
        print(f"选股失败：{envelope.get('error')}")
        return
    label = "跌幅榜" if ascending else "涨幅榜"
    stocks = envelope["data"].get("stocks", [])
    print(f"A 股今日{label}：")
    for row in stocks[:5]:
        print(f"  {row['code']} {row['name']} {row['change_pct']}%")


def main() -> None:
    print("===== Eastmoney 代码搜索 + 全市场选股（A 股）=====")
    symbol = resolve_symbol("贵州茅台")
    print(f"解析得到：{symbol}")
    top_movers(ascending=False, top_n=10)
    top_movers(ascending=True, top_n=10)


if __name__ == "__main__":
    main()
