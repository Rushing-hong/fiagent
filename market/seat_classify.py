"""龙虎榜营业部席位启发式分类（v0，degraded）。

标签会变；本模块规则库可维护，准确率无金标 → quality=degraded。
"""

from __future__ import annotations

from typing import Any

# 知名游资席位关键词（非穷尽，随市场更新）
_HOT_MONEY_KEYS = (
    "拉萨", "深圳福田", "上海溧阳", "杭州五星", "成都温江", "东方财富拉萨",
    "华泰证券深圳益田", "国泰君安上海江苏路", "中信证券上海分公司",
    "财通证券杭州上塘路", "招商证券深圳蛇口", "光大证券宁波解放南路",
)

_INST_KEYS = (
    "机构专用", "社保", "保险", "基金", "QFII", "RQFII", "券商自营",
    "资产管理", "私募", "信托",
)

_QUANT_KEYS = (
    "量化", "对冲", "幻方", "九坤", "明汯", "灵均", "诚奇", "衍复", "金戈量锐",
)

_RETAIL_KEYS = (
    "普通", "个人",
)


def classify_seat(name: str | None) -> str:
    """Return hot_money | institution | quant | retail | unknown."""
    s = str(name or "").strip()
    if not s:
        return "unknown"
    for k in _QUANT_KEYS:
        if k in s:
            return "quant"
    for k in _INST_KEYS:
        if k in s:
            return "institution"
    for k in _HOT_MONEY_KEYS:
        if k in s:
            return "hot_money"
    for k in _RETAIL_KEYS:
        if k in s:
            return "retail"
    # 营业部默认偏游资通道（启发式）
    if "证券" in s and ("营业部" in s or "路" in s or "道" in s):
        return "hot_money"
    return "unknown"


def enrich_seats(seats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for s in seats:
        row = dict(s)
        row["seat_type"] = classify_seat(s.get("seat") or s.get("OPERATEDEPT_NAME"))
        out.append(row)
    return out


def aggregate_by_type(seats: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """seat_type -> {buy, sell, net, n}."""
    from market.envelope import to_float

    agg: dict[str, dict[str, float]] = {}
    for s in seats:
        t = s.get("seat_type") or classify_seat(s.get("seat"))
        bucket = agg.setdefault(t, {"buy": 0.0, "sell": 0.0, "net": 0.0, "n": 0.0})
        buy = to_float(s.get("buy")) or 0.0
        sell = to_float(s.get("sell")) or 0.0
        net = to_float(s.get("net"))
        if net is None:
            net = buy - sell
        bucket["buy"] += buy
        bucket["sell"] += sell
        bucket["net"] += float(net)
        bucket["n"] += 1
    return agg
