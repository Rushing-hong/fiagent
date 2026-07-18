"""可交易股票池：按 A 股制度约束筛动态 universe（供 run_backtest 的 codes）。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from market.eastmoney import push2_diff_rows
from market.envelope import clamp_int, err, ok, to_float
from market.http import throttled_get_json
from tools.base import BaseTool

logger = logging.getLogger(__name__)

_CLIST_URLS = (
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://push2delay.eastmoney.com/api/qt/clist/get",
)
_FS_A = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
_FIELDS = "f2,f3,f6,f12,f14,f20"


def _clist(params: dict[str, Any]) -> Any:
    last: Exception | None = None
    for url in _CLIST_URLS:
        host = url.split("//", 1)[1].split("/", 1)[0]
        try:
            return throttled_get_json(
                url, host_key=host, min_interval=1.0, params=params, timeout=15.0
            )
        except Exception as exc:
            last = exc
            logger.warning("universe clist fail %s: %s", host, exc)
    raise RuntimeError(str(last) if last else "clist unavailable")


def _suffix(bare: str) -> str:
    from market.a_share_code import to_a_share_symbol
    return to_a_share_symbol(str(bare).zfill(6))


def _is_st(name: str) -> bool:
    return "ST" in name or "*ST" in name


def _parse_list_date(val: Any) -> datetime | None:
    if val is None:
        return None
    s = str(val).strip().replace("-", "").replace("/", "")[:8]
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return datetime.strptime(s, "%Y%m%d")
    except ValueError:
        return None


def _listing_dates() -> dict[str, datetime]:
    """证券代码(无后缀) -> 上市日。"""
    out: dict[str, datetime] = {}
    try:
        import akshare as ak

        for fn_name in ("stock_info_sh_name_code", "stock_info_sz_name_code"):
            fn = getattr(ak, fn_name, None)
            if fn is None:
                continue
            try:
                df = fn(symbol="主板A股") if "sh" in fn_name else fn(symbol="A股列表")
            except TypeError:
                try:
                    df = fn()
                except Exception as exc:
                    logger.warning("%s fail: %s", fn_name, exc)
                    continue
            except Exception as exc:
                logger.warning("%s fail: %s", fn_name, exc)
                continue
            if df is None or getattr(df, "empty", True):
                continue
            code_col = next(
                (c for c in df.columns if "代码" in str(c) or str(c).lower() == "code"),
                None,
            )
            date_col = next(
                (c for c in df.columns if "上市" in str(c)),
                None,
            )
            if not code_col or not date_col:
                continue
            for _, row in df.iterrows():
                bare = str(row[code_col]).zfill(6)[-6:]
                dt = _parse_list_date(row[date_col])
                if bare.isdigit() and dt is not None:
                    out[bare] = dt
    except Exception as exc:
        logger.warning("listing dates unavailable: %s", exc)
    return out


class BuildTradableUniverseTool(BaseTool):
    name = "build_tradable_universe"
    summary = "动态可交易股票池（剔ST/低价/低流动性/次新）"
    description = (
        "从东财全 A 快照筛可交易池：默认排除 ST/*ST、股价<min_price、"
        "当日成交额<min_amount（元）、上市未满 min_list_days 日历日的次新股。"
        "输出 codes 可传给 run_backtest。结果为时点快照，非历史点位成分。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "min_price": {"type": "number", "default": 3.0, "description": "最低股价（元）"},
            "min_amount": {
                "type": "number",
                "default": 30_000_000,
                "description": "最低成交额（元），默认3000万",
            },
            "exclude_st": {"type": "boolean", "default": True},
            "min_list_days": {
                "type": "integer",
                "default": 120,
                "description": "上市满 N 个日历日（0=关闭）；用上交所/深交所列表上市日近似",
            },
            "max_names": {
                "type": "integer",
                "default": 500,
                "description": "返回上限（按成交额降序）",
            },
            "min_market_cap_yi": {
                "type": "number",
                "description": "可选，最低总市值（亿元）",
            },
            "save_snapshot": {
                "type": "boolean",
                "default": True,
                "description": "写入本地点位快照（供 load_pit_universe）",
            },
            "snapshot_name": {
                "type": "string",
                "default": "default",
                "description": "快照命名空间",
            },
        },
    }
    is_readonly = False  # 默认可 save_snapshot 写 universe

    def execute(self, args: dict, ctx) -> str:
        min_price = to_float(args.get("min_price"))
        if min_price is None:
            min_price = 3.0
        min_amount = to_float(args.get("min_amount"))
        if min_amount is None:
            min_amount = 30_000_000.0
        exclude_st = bool(args.get("exclude_st", True))
        max_names = clamp_int(args.get("max_names"), 500, 10, 2000)
        min_list_days = clamp_int(args.get("min_list_days"), 120, 0, 2000)
        min_mcap = to_float(args.get("min_market_cap_yi"))

        try:
            payload = _clist({
                "pn": "1",
                "pz": str(min(5000, max(max_names * 4, 1000))),
                "po": "1",
                "np": "1",
                "fltt": "2",
                "fid": "f6",
                "fs": _FS_A,
                "fields": _FIELDS,
            })
        except Exception as exc:
            return err(f"拉取股票池失败: {exc}")

        rows = push2_diff_rows(payload)
        list_map: dict[str, datetime] = {}
        if min_list_days > 0:
            list_map = _listing_dates()
        cutoff = datetime.now() - timedelta(days=min_list_days) if min_list_days > 0 else None

        passed: list[dict[str, Any]] = []
        stats = {
            "raw": len(rows),
            "st": 0,
            "low_price": 0,
            "low_amount": 0,
            "low_mcap": 0,
            "new_list": 0,
            "no_list_date": 0,
        }

        for r in rows:
            if not isinstance(r, dict) or not r.get("f12"):
                continue
            name = str(r.get("f14") or "")
            if exclude_st and _is_st(name):
                stats["st"] += 1
                continue
            price = to_float(r.get("f2"))
            amount = to_float(r.get("f6"))
            mcap_raw = to_float(r.get("f20"))
            mcap_yi = None
            if mcap_raw is not None:
                mcap_yi = mcap_raw / 1e8 if abs(mcap_raw) >= 1e6 else mcap_raw
            if price is None or price < min_price:
                stats["low_price"] += 1
                continue
            if amount is None or amount < min_amount:
                stats["low_amount"] += 1
                continue
            if min_mcap is not None and (mcap_yi is None or mcap_yi < min_mcap):
                stats["low_mcap"] += 1
                continue
            bare = str(r.get("f12")).zfill(6)
            if cutoff is not None:
                ld = list_map.get(bare)
                if ld is None:
                    stats["no_list_date"] += 1
                    # keep if listing map incomplete (BJ etc.) — do not hard-drop
                elif ld > cutoff:
                    stats["new_list"] += 1
                    continue
            passed.append({
                "code": _suffix(bare),
                "name": name,
                "price": price,
                "amount": amount,
                "market_cap_yi": round(mcap_yi, 2) if mcap_yi is not None else None,
                "list_date": list_map.get(bare).strftime("%Y-%m-%d") if bare in list_map else None,
            })

        passed.sort(key=lambda x: x.get("amount") or 0, reverse=True)
        top = passed[:max_names]
        codes = [x["code"] for x in top]
        snap_meta = None
        if bool(args.get("save_snapshot", True)):
            try:
                from market.research_store import get_store
                asof = get_store().save_universe(
                    codes,
                    name=str(args.get("snapshot_name") or "default"),
                    meta={
                        "min_price": min_price,
                        "min_amount": min_amount,
                        "min_list_days": min_list_days,
                        "count": len(codes),
                    },
                )
                snap_meta = {"saved_asof": asof, "name": str(args.get("snapshot_name") or "default")}
            except Exception as exc:
                snap_meta = {"error": str(exc)}
        note = (
            "时点快照。回测请另取历史区间 OHLCV，勿假设成分在历史全程不变。"
        )
        if min_list_days > 0:
            note += (
                f" 上市满{min_list_days}日历日过滤已启用（交易所列表）；"
                f"无上市日记录的票保留（no_list_date={stats['no_list_date']}）。"
            )
        return ok(
            {
                "count": len(top),
                "codes": codes,
                "sample": top[:20],
                "filters": {
                    "min_price": min_price,
                    "min_amount": min_amount,
                    "exclude_st": exclude_st,
                    "min_list_days": min_list_days,
                    "min_market_cap_yi": min_mcap,
                    "max_names": max_names,
                    "listing_map_size": len(list_map),
                },
                "reject_stats": stats,
                "snapshot": snap_meta,
                "note": note,
            },
            market="a_share",
            source="eastmoney+exchange_list",
            tool="build_tradable_universe",
            quality="ok" if (min_list_days == 0 or list_map) else "degraded",
        )
