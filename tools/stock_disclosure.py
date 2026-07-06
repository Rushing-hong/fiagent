"""A-share disclosure tools: 龙虎榜、融资融券、大宗交易、股东户数、解禁、板块."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

from market.eastmoney import (
    A_SHARE_SUFFIXES,
    bare_a_share_code,
    fetch_datacenter,
    get_json,
    push2_diff_rows,
    resolve_secid,
)
from market.envelope import clamp_int, err, ok, to_float
from tools.base import BaseTool

logger = logging.getLogger(__name__)

_MEMBERSHIP_URL = "https://push2.eastmoney.com/api/qt/slist/get"
_RANKING_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_RANKING_FS = "m:90+t:2"       # industry sectors (行业板块)
_RANKING_FS_CONCEPT = "m:90+t:3"  # concept sectors (概念板块)


class DragonTigerTool(BaseTool):
    name = "get_dragon_tiger"
    summary = "龙虎榜（异动席位）"
    description = "查询 A 股龙虎榜：某日上榜股票及营业部买卖席位（东财 datacenter）。"
    parameters = {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "交易日 YYYY-MM-DD"},
            "code": {"type": "string", "description": "可选，如 600519.SH"},
        },
        "required": ["date"],
    }
    is_readonly = True

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
            data: dict[str, Any] = {
                "date": trade_date,
                "count": len(appearances_raw),
                "appearances": [_appearance_row(r) for r in appearances_raw[:200]],
            }
            if code:
                data["code"] = code
                seats_raw = fetch_datacenter(
                    "RPT_BILLBOARD_TRADEDETAIL",
                    filter_expr=f"(TRADE_DATE='{trade_date}')(SECURITY_CODE=\"{code}\")",
                    sort_columns="NET",
                )
                data["seats"] = [_seat_row(r) for r in seats_raw[:30]]
            return ok(data, market="a_share", source="eastmoney")
        except Exception as exc:
            return err(f"龙虎榜查询失败: {exc}")


class MarginTradingTool(BaseTool):
    name = "get_margin_trading"
    summary = "融资融券余额"
    description = "获取 A 股个股融资融券日频数据：融资余额、融券余额等（东财 datacenter）。"
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "days": {"type": "integer", "default": 30},
        },
        "required": ["code"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        code = bare_a_share_code(str(args.get("code") or ""))
        if not code:
            return err("仅支持 A 股代码，如 600519.SH")
        days = clamp_int(args.get("days"), 30, 1, 250)
        try:
            rows = fetch_datacenter(
                "RPTA_WEB_RZRQ_GGMX",
                filter_expr=f'(SCODE="{code}")',
                sort_columns="DATE",
                page_size=days,
            )
        except Exception as exc:
            return err(str(exc))
        if not rows:
            return err(f"未找到 {code} 的融资融券数据")
        return ok(
            {"code": code, "rows": [_margin_row(r) for r in rows[:days]]},
            market="a_share",
            source="eastmoney",
        )


class BlockTradesTool(BaseTool):
    name = "get_block_trades"
    summary = "大宗交易"
    description = "查询 A 股大宗交易记录：成交价、溢价率、买卖营业部（东财 datacenter）。"
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "days": {"type": "integer", "default": 30},
        },
        "required": ["code"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        symbol = str(args.get("code") or "").strip().upper()
        if not symbol:
            return err("code 必填")
        secid = resolve_secid(symbol)
        if not secid or secid.split(".", 1)[0] not in ("0", "1"):
            return err(f"{symbol} 不是有效的 A 股代码")
        code = bare_a_share_code(symbol)
        days = clamp_int(args.get("days"), 30, 1, 365)
        end = datetime.now().date()
        start = end - timedelta(days=days - 1)
        filt = (
            f'(SECURITY_CODE="{code}")'
            f"(TRADE_DATE>='{start.isoformat()}')"
            f"(TRADE_DATE<='{end.isoformat()}')"
        )
        try:
            rows = fetch_datacenter(
                "RPT_DATA_BLOCKTRADE",
                columns=(
                    "TRADE_DATE,SECURITY_CODE,SECURITY_NAME_ABBR,CLOSE_PRICE,DEAL_PRICE,"
                    "PREMIUM_RATIO,DEAL_VOLUME,DEAL_AMT,BUYER_NAME,SELLER_NAME"
                ),
                filter_expr=filt,
                sort_columns="TRADE_DATE",
                page_size=200,
            )
        except Exception as exc:
            return err(str(exc))
        records = [_block_row(r) for r in rows[:200]]
        return ok(
            {"code": symbol, "days": days, "count": len(records), "records": records},
            market="china_a",
            source="eastmoney",
        )


class ShareholderCountTool(BaseTool):
    name = "get_shareholder_count"
    summary = "股东户数"
    description = "获取 A 股季度股东户数及环比变化（东财 datacenter）。"
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "max_periods": {"type": "integer", "default": 24},
        },
        "required": ["code"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        code = str(args.get("code") or "").strip().upper()
        suffix = code.rpartition(".")[2]
        if suffix not in A_SHARE_SUFFIXES:
            return err("仅支持 .SH/.SZ/.BJ A 股")
        if resolve_secid(code) is None:
            return err(f"无法解析代码 {code}")
        limit = clamp_int(args.get("max_periods"), 24, 1, 24)
        try:
            rows = fetch_datacenter(
                "RPT_HOLDERNUMLATEST",
                columns=(
                    "SECUCODE,SECURITY_CODE,END_DATE,HOLDER_NUM,HOLDER_NUM_CHANGE,"
                    "HOLDER_NUM_RATIO,AVG_HOLD_AMT,AVG_HOLD_NUM,TOTAL_MARKET_CAP"
                ),
                filter_expr=f'(SECUCODE="{code}")',
                sort_columns="END_DATE",
                page_size=limit,
            )
        except Exception as exc:
            return err(str(exc))
        periods = [_holder_row(r) for r in rows if _holder_row(r)][:limit]
        if not periods:
            return err(f"未找到 {code} 的股东户数数据")
        return ok({"code": code, "periods": periods}, market="CN", source="eastmoney")


class LockupExpiryTool(BaseTool):
    name = "get_lockup_expiry"
    summary = "限售解禁"
    description = (
        "查询 A 股限售解禁：指定股票历史解禁，或不传 code 查全市场近期解禁日历（东财）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "可选，如 600519.SH"},
            "horizon_days": {"type": "integer", "default": 90},
        },
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        bare = bare_a_share_code(str(args.get("code") or "")) if args.get("code") else None
        if args.get("code") and not bare:
            return err("无效的 A 股代码")
        horizon = clamp_int(args.get("horizon_days"), 90, 1, 365)
        today = date.today()
        if bare:
            filt = f'(SECURITY_CODE="{bare}")'
            sort_col, sort_type = "FREE_DATE", "-1"
        else:
            end = today + timedelta(days=horizon)
            filt = f"(FREE_DATE>='{today.isoformat()}')(FREE_DATE<='{end.isoformat()}')"
            sort_col, sort_type = "FREE_DATE", "1"
        try:
            rows = fetch_datacenter(
                "RPT_LIFT_STOCK",
                columns=(
                    "SECURITY_CODE,SECURITY_NAME_ABBR,FREE_DATE,FREE_SHARES_TYPE,"
                    "FREE_SHARES,ABLE_FREE_SHARES,LIFT_MARKET_CAP,FREE_RATIO,TOTAL_RATIO"
                ),
                filter_expr=filt,
                sort_columns=sort_col,
                sort_types=sort_type,
                page_size=200,
            )
        except Exception as exc:
            return err(str(exc))
        records = [_lockup_row(r) for r in rows if _lockup_row(r)][:200]
        data: dict[str, Any] = {
            "scope": "single_code" if bare else "market_calendar",
            "count": len(records),
            "records": records,
        }
        if bare:
            data["code"] = bare
        else:
            data["horizon_days"] = horizon
            data["as_of"] = today.isoformat()
        return ok(data, market="a_share", source="eastmoney")


class SectorInfoTool(BaseTool):
    name = "get_sector_info"
    summary = "板块归属 / 行业+概念涨跌排行"
    description = (
        "A 股板块信息：membership 模式查个股所属行业/概念板块；"
        "ranking 模式查行业或概念板块涨跌幅排行（东财 push2）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "mode": {"type": "string", "enum": ["membership", "ranking"], "default": "membership"},
            "sector_type": {
                "type": "string",
                "enum": ["industry", "concept"],
                "default": "industry",
                "description": "ranking 模式下的板块类型: industry=行业板块, concept=概念板块",
            },
            "limit": {"type": "integer", "default": 30},
        },
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        mode = args.get("mode", "membership")
        if mode == "ranking":
            limit = clamp_int(args.get("limit"), 30, 1, 100)
            sector_type = args.get("sector_type", "industry")
            fs = _RANKING_FS_CONCEPT if sector_type == "concept" else _RANKING_FS
            try:
                payload = get_json(
                    _RANKING_URL,
                    params={
                        "fs": fs,
                        "fields": "f12,f14,f3,f2,f104,f105,f128,f140",
                        "pn": "1",
                        "pz": str(limit),
                        "po": "1",
                        "fid": "f3",
                        "fltt": "2",
                    },
                )
            except Exception as exc:
                return err(str(exc))
            boards = []
            for raw in push2_diff_rows(payload):
                if not isinstance(raw, dict) or not raw.get("f12"):
                    continue
                boards.append({
                    "board_code": str(raw.get("f12")),
                    "board_name": str(raw.get("f14", "")),
                    "change_pct": to_float(raw.get("f3")),
                    "up_count": to_float(raw.get("f104")),
                    "down_count": to_float(raw.get("f105")),
                })
            return ok({"boards": boards[:limit]}, market="stock", source="eastmoney", mode="ranking", sector_type=sector_type)

        code = str(args.get("code") or "").strip()
        if not code:
            return err("membership 模式需要 code")
        secid = resolve_secid(code)
        if not secid:
            return err(f"无法解析 {code}")
        try:
            payload = get_json(
                _MEMBERSHIP_URL,
                params={
                    "secid": secid,
                    "spt": "3",
                    "pi": "0",
                    "pz": "100",
                    "fields": "f12,f13,f14,f3,f2",
                    "fltt": "2",
                    "po": "1",
                },
            )
        except Exception as exc:
            return err(str(exc))
        boards = []
        for raw in push2_diff_rows(payload):
            if isinstance(raw, dict) and raw.get("f12"):
                boards.append({
                    "board_code": str(raw.get("f12")),
                    "board_name": str(raw.get("f14", "")),
                    "change_pct": to_float(raw.get("f3")),
                })
        return ok(
            {"code": code, "secid": secid, "boards": boards},
            market="stock",
            source="eastmoney",
            mode="membership",
        )


def _compact_date(date_str: str) -> str:
    cleaned = date_str.strip()
    digits = cleaned.replace("-", "")
    if len(digits) != 8 or not digits.isdigit():
        raise ValueError(f"无效日期: {date_str}")
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"


def _appearance_row(raw: dict) -> dict:
    return {
        "code": raw.get("SECURITY_CODE"),
        "name": raw.get("SECURITY_NAME_ABBR"),
        "net_buy": raw.get("BILLBOARD_NET_AMT"),
        "buy_amount": raw.get("BILLBOARD_BUY_AMT"),
        "sell_amount": raw.get("BILLBOARD_SELL_AMT"),
        "reason": raw.get("EXPLANATION"),
    }


def _seat_row(raw: dict) -> dict:
    return {
        "seat": raw.get("OPERATEDEPT_NAME"),
        "side": raw.get("SIDE"),
        "buy": raw.get("BUY"),
        "sell": raw.get("SELL"),
        "net": raw.get("NET"),
        "rank": raw.get("RANK"),
    }


def _margin_row(raw: dict) -> dict:
    return {
        "trade_date": str(raw.get("DATE", ""))[:10],
        "financing_balance": to_float(raw.get("RZYE")),
        "financing_buy": to_float(raw.get("RZMRE")),
        "short_balance": to_float(raw.get("RQYE")),
        "margin_total_balance": to_float(raw.get("RZRQYE")),
    }


def _block_row(raw: dict) -> dict:
    return {
        "trade_date": raw.get("TRADE_DATE"),
        "name": raw.get("SECURITY_NAME_ABBR"),
        "deal_price": to_float(raw.get("DEAL_PRICE")),
        "premium_ratio": to_float(raw.get("PREMIUM_RATIO")),
        "deal_volume": to_float(raw.get("DEAL_VOLUME")),
        "deal_amount": to_float(raw.get("DEAL_AMT")),
        "buyer_seat": raw.get("BUYER_NAME"),
        "seller_seat": raw.get("SELLER_NAME"),
    }


def _holder_row(raw: dict) -> dict | None:
    end_date = str(raw.get("END_DATE") or "")[:10] or None
    holder = to_float(raw.get("HOLDER_NUM"))
    if not end_date and holder is None:
        return None
    return {
        "end_date": end_date,
        "holder_count": holder,
        "holder_count_change": to_float(raw.get("HOLDER_NUM_CHANGE")),
        "holder_count_change_pct": to_float(raw.get("HOLDER_NUM_RATIO")),
        "avg_hold_shares": to_float(raw.get("AVG_HOLD_NUM")),
        "avg_hold_amount": to_float(raw.get("AVG_HOLD_AMT")),
    }


def _lockup_row(raw: dict) -> dict | None:
    code = raw.get("SECURITY_CODE")
    free_date = raw.get("FREE_DATE")
    if not code or not free_date:
        return None
    return {
        "code": str(code),
        "name": raw.get("SECURITY_NAME_ABBR"),
        "free_date": str(free_date)[:10],
        "share_type": raw.get("FREE_SHARES_TYPE"),
        "free_shares": to_float(raw.get("FREE_SHARES")),
        "lift_market_cap": to_float(raw.get("LIFT_MARKET_CAP")),
        "free_ratio": to_float(raw.get("FREE_RATIO")),
    }
