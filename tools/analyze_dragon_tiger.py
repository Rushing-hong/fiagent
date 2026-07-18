"""龙虎榜席位画像 v0。"""

from __future__ import annotations

import json
from typing import Any

from market.eastmoney import bare_a_share_code, fetch_datacenter
from market.envelope import err, normalize_meta, now_as_of, ok
from market.seat_classify import aggregate_by_type, enrich_seats
from tools.base import BaseTool
from tools.stock_disclosure import _appearance_row, _compact_date, _seat_row


class AnalyzeDragonTigerTool(BaseTool):
    name = "analyze_dragon_tiger"
    summary = "龙虎榜席位画像（游资/机构/量化启发式）"
    description = (
        "在东财龙虎榜基础上对营业部做启发式分类：hot_money/institution/quant/retail/unknown。\n"
        "输出分类型净买入与席位明细；quality=degraded（席位标签无金标）。\n"
        "可选 persist 写入 research.db micro_signals。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "交易日 YYYY-MM-DD"},
            "code": {"type": "string", "description": "可选个股；缺省则汇总当日上榜"},
            "persist": {"type": "boolean", "default": True},
            "top_n": {"type": "integer", "default": 30},
        },
        "required": ["date"],
    }
    is_readonly = False
    repeatable = True

    def execute(self, args: dict, ctx) -> str:
        date_arg = str(args.get("date") or "").strip()
        if not date_arg:
            return err("date 必填")
        try:
            trade_date = _compact_date(date_arg)
        except ValueError as exc:
            return err(str(exc))

        code = bare_a_share_code(args["code"]) if args.get("code") else None
        try:
            appear_filter = f"(TRADE_DATE='{trade_date}')"
            if code:
                appear_filter += f'(SECURITY_CODE="{code}")'
            appearances_raw = fetch_datacenter(
                "RPT_DAILYBILLBOARD_DETAILS",
                filter_expr=appear_filter,
                sort_columns="BILLBOARD_NET_AMT",
            )
            appearances = [_appearance_row(r) for r in appearances_raw[:200]]

            seats: list[dict[str, Any]] = []
            if code:
                seats_raw = fetch_datacenter(
                    "RPT_BILLBOARD_TRADEDETAIL",
                    filter_expr=f"(TRADE_DATE='{trade_date}')(SECURITY_CODE=\"{code}\")",
                    sort_columns="NET",
                )
                seats = enrich_seats([_seat_row(r) for r in seats_raw[:50]])
            else:
                # sample top names' seats (cap API calls)
                for app in appearances[:5]:
                    bare = str(app.get("code") or "")
                    if not bare:
                        continue
                    seats_raw = fetch_datacenter(
                        "RPT_BILLBOARD_TRADEDETAIL",
                        filter_expr=f"(TRADE_DATE='{trade_date}')(SECURITY_CODE=\"{bare}\")",
                        sort_columns="NET",
                    )
                    for s in seats_raw[:10]:
                        row = _seat_row(s)
                        row["code"] = bare
                        seats.append(row)
                seats = enrich_seats(seats)

            by_type = aggregate_by_type(seats)
            # stock-level hot_money net signal for persist
            hot_net = float(by_type.get("hot_money", {}).get("net") or 0.0)
            inst_net = float(by_type.get("institution", {}).get("net") or 0.0)
            meta_note: str | None = None

            if bool(args.get("persist", True)):
                from market.a_share_code import to_a_share_symbol
                from market.research_store import get_store
                rows = []
                target_code = code or "_MARKET_"
                sym = to_a_share_symbol(code) if code else target_code
                rows.append({
                    "asof": trade_date,
                    "code": sym,
                    "signal_id": "dt_hot_money_net",
                    "value": hot_net,
                    "unit": "CNY_yuan",
                    "meta_json": {"seat_agg": by_type},
                })
                rows.append({
                    "asof": trade_date,
                    "code": sym,
                    "signal_id": "dt_institution_net",
                    "value": inst_net,
                    "unit": "CNY_yuan",
                    "meta_json": {"seat_agg": by_type},
                })
                try:
                    get_store().upsert_micro_signals(rows)
                except Exception as exc:
                    meta_note = f"persist_failed: {exc}"

            meta = normalize_meta(
                source="eastmoney+heuristic",
                fetch_time=now_as_of(),
                frequency="event",
                unit="CNY_yuan",
                stale=False,
            )
            note = "席位分类为启发式规则库 v0，非官方标签；quality=degraded"
            if meta_note:
                note = f"{note}；{meta_note}"
            return ok(
                {
                    "date": trade_date,
                    "code": code,
                    "appearances_count": len(appearances),
                    "appearances": appearances[: int(args.get("top_n") or 30)],
                    "seats": seats[:50],
                    "by_seat_type": by_type,
                    "signals": {
                        "hot_money_net": hot_net,
                        "institution_net": inst_net,
                    },
                    "note": note,
                },
                quality="degraded",
                market="a_share",
                tool="analyze_dragon_tiger",
                note=note,
                _meta=meta,
            )
        except Exception as exc:
            return err(f"龙虎榜画像失败: {exc}")
