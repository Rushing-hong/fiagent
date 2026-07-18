"""Unified akshare data wrapper.

akshare provides 1000+ functions covering Chinese futures, options, convertible
bonds, funds, macro, and more. This module wraps the most commonly needed ones
into a consistent JSON-returning interface for tool consumption.

Gracefully degrades when akshare is not installed (tools will report "not installed").
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# akshare availability check
# ---------------------------------------------------------------------------

_AK_AVAILABLE = False
try:
    import akshare as ak  # noqa: F401
    _AK_AVAILABLE = True
except ImportError:
    pass


def _require_ak() -> None:
    if not _AK_AVAILABLE:
        raise ImportError(
            "akshare 未安装。请执行: pip install akshare"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _df_to_records(df: pd.DataFrame, limit: int = 0) -> list[dict[str, Any]]:
    """Convert DataFrame to list-of-dicts, filling NaN with None."""
    if df is None or df.empty:
        return []
    df = df.where(pd.notna(df), None)
    records = df.to_dict(orient="records")
    if limit and limit < len(records):
        return records[:limit]
    return records


def _ok(data: Any, **meta: Any) -> str:
    from market.envelope import ok
    return ok(data, **meta)


def _err(message: str) -> str:
    from market.envelope import err
    return err(message)


# ===================================================================
# Futures 期货
# ===================================================================

# Exchange code to Chinese name
_EXCHANGE_MAP: dict[str, str] = {
    "CFFEX":   "中金所",
    "SHFE":    "上期所",
    "INE":     "上海国际能源交易中心",
    "DCE":     "大商所",
    "ZCE":     "郑商所",
    "GFEX":    "广期所",
}

# Exchange to akshare symbol suffix
_EXCHANGE_SUFFIX: dict[str, str] = {
    "CFFEX": "CFX",
    "SHFE":  "SHF",
    "INE":   "INE",
    "DCE":   "DCE",
    "ZCE":   "CZC",
    "GFEX":  "GFE",
}

# Hot contracts per exchange
_HOT_CONTRACTS: dict[str, list[str]] = {
    "CFFEX": ["IF", "IC", "IM", "IH", "T", "TF", "TS", "TL"],
    "SHFE":  ["AU", "AG", "CU", "AL", "ZN", "RB", "HC", "RU", "BU"],
    "INE":   ["SC", "LU", "BC"],
    "DCE":   ["I", "J", "JM", "M", "Y", "P", "LH", "EG", "PG", "PP"],
    "ZCE":   ["TA", "MA", "SA", "FG", "SR", "CF", "RM", "OI", "UR"],
    "GFEX":  ["LC", "SI", "PS"],
}


def get_futures_main_list(exchange: str | None = None) -> str:
    """List all futures main contracts, optionally filtered by exchange.

    Args:
        exchange: One of CFFEX/SHFE/INE/DCE/ZCE/GFEX, or None for all.
    """
    _require_ak()
    try:
        import akshare as ak
        df = ak.futures_main_sina()
        if df is None or df.empty:
            return _err("未获取到期货主力合约数据")
        records = _df_to_records(df)
        if exchange and exchange in _EXCHANGE_SUFFIX:
            records = [r for r in records if str(r.get("symbol", "")).endswith(_EXCHANGE_SUFFIX[exchange])]
        # pick key columns
        slim = []
        for r in records:
            slim.append({
                "symbol": r.get("symbol"),
                "name": r.get("name"),
                "exchange": r.get("exchange"),
                "trade_date": r.get("trade_date"),
                "open": _float(r.get("open")),
                "high": _float(r.get("high")),
                "low": _float(r.get("low")),
                "close": _float(r.get("close")),
                "volume": _float(r.get("volume")),
                "open_interest": _float(r.get("hold")),
            })
        return _ok({"count": len(slim), "contracts": slim}, source="akshare", market="futures")
    except Exception as e:
        return _err(f"期货行情获取失败: {e}")


def get_futures_daily(symbol: str, start_date: str, end_date: str) -> str:
    """Get daily OHLCV for a specific futures contract.

    Args:
        symbol: Contract code like 'RB2501', 'IF2503', 'LC2505'.
        start_date: YYYY-MM-DD.
        end_date: YYYY-MM-DD.
    """
    _require_ak()
    try:
        import akshare as ak
        sd = start_date.replace("-", "")
        ed = end_date.replace("-", "")
        # Try futures_zh_daily_sina (more stable), fallback to futures_main_sina
        func = getattr(ak, "futures_zh_daily_sina", None) or getattr(ak, "futures_main_sina")
        if func is None:
            return _err("akshare 版本不支持期货历史数据")
        df = func(symbol=symbol, start_date=sd, end_date=ed)
        if df is None or df.empty:
            return _err(f"未找到 {symbol} 的期货数据")
        records = _df_to_records(df)
        slim = []
        for r in records:
            slim.append({
                "trade_date": str(r.get("date", ""))[:10],
                "open": _float(r.get("open")),
                "high": _float(r.get("high")),
                "low": _float(r.get("low")),
                "close": _float(r.get("close")),
                "volume": _float(r.get("volume")),
                "open_interest": _float(r.get("hold")),
            })
        return _ok({"symbol": symbol, "count": len(slim), "records": slim}, source="akshare", market="futures")
    except Exception as e:
        return _err(f"期货历史数据获取失败: {e}")


def get_futures_position_ranking(symbol: str, indicator: str = "volume") -> str:
    """Get top members' long/short position ranking (CFFEX/DCE/SHFE daily report).

    Args:
        symbol: Exchange-specific contract code.
        indicator: 'volume' | 'long' | 'short'.
    """
    _require_ak()
    try:
        import akshare as ak
        func_map = {
            "volume": "futures_hold_volume_rank",
            "long":   "futures_hold_long_rank",
            "short":  "futures_hold_short_rank",
        }
        func_name = func_map.get(indicator)
        if func_name is None:
            return _err("indicator 必须是 volume/long/short")
        func = getattr(ak, func_name, None)
        if func is None:
            return _err(f"akshare 版本不支持 {func_name}，请升级: pip install akshare --upgrade")
        df = func(symbol=symbol)
        records = _df_to_records(df, limit=20)
        return _ok({"symbol": symbol, "indicator": indicator, "count": len(records), "ranking": records},
                   source="akshare", market="futures")
    except Exception as e:
        return _err(f"期货持仓排名获取失败: {e}")


def get_commodity_spot(symbol: str) -> str:
    """Get latest spot price for a commodity.

    Args:
        symbol: '螺纹钢' | '铁矿石' | '铜' | '原油' | '生猪' etc.
    """
    _require_ak()
    try:
        import akshare as ak
        func = getattr(ak, "futures_spot_price", None) or getattr(ak, "futures_spot", None)
        if func is None:
            return _err("akshare 版本不支持现货价格查询，请升级: pip install akshare --upgrade")
        df = func(symbol=symbol)
        records = _df_to_records(df, limit=50)
        return _ok({"symbol": symbol, "count": len(records), "records": records},
                   source="akshare", market="commodity_spot")
    except Exception as e:
        return _err(f"现货价格获取失败: {e}")


# ===================================================================
# Convertible Bonds 可转债
# ===================================================================

def get_cb_list() -> str:
    """Get full convertible bond market snapshot from Jisilu (集思录).

    Returns: symbol, name, bond_price, convert_price, premium_rt (溢价率),
             stock_price, convert_value, ytm_rt (到期收益率), rating,
             put_convert_price (回售触发价), force_redeem_price (强赎触发价),
             double_low (双低值 = 转债价 + 溢价率).
    """
    _require_ak()
    try:
        import akshare as ak
        df = ak.bond_cb_jsl(cookie="")
        if df is None or df.empty:
            return _err("未获取到可转债数据（集思录可能需要 cookie）")
        records = _df_to_records(df)
        slim = []
        for r in records:
            price = _float(r.get("price"))
            premium = _float(r.get("premium_rt"))
            double_low = round(price + premium, 2) if price is not None and premium is not None else None
            slim.append({
                "cb_code": r.get("bond_id"),
                "cb_name": r.get("bond_nm"),
                "stock_code": r.get("stock_id"),
                "stock_name": r.get("stock_nm"),
                "cb_price": price,
                "convert_price": _float(r.get("convert_price")),
                "premium_rt": premium,
                "convert_value": _float(r.get("convert_value")),
                "ytm_rt": _float(r.get("ytm_rt")),
                "rating": r.get("rating_cd"),
                "issue_year": r.get("year_left"),
                "double_low": double_low,
                "put_price": _float(r.get("put_convert_price")),
                "force_redeem_price": _float(r.get("force_redeem_price")),
            })
        return _ok({"count": len(slim), "bonds": slim}, source="akshare", market="convertible_bond")
    except Exception as e:
        return _err(f"可转债数据获取失败: {e}")


def screen_cb(
    max_price: float = 130.0,
    max_premium: float = 30.0,
    max_double_low: float | None = None,
    min_rating: str = "",
    sort_by: str = "double_low",
    top_n: int = 20,
) -> str:
    """Screen convertible bonds by price, premium, and rating.

    Classic strategies:
    - 双低策略: low price + low premium (e.g. price<120, premium<20)
    - 低价策略: price < 100 (near bond floor protection)
    - YTM策略: positive yield-to-maturity

    Args:
        max_price: Maximum bond price (default 130).
        max_premium: Maximum premium rate % (default 30).
        max_double_low: Max double-low value (price + premium). If set, overrides max_price/max_premium.
        min_rating: Minimum credit rating ('AA' / 'AA+' / 'AAA').
        sort_by: 'double_low' | 'ytm_rt' | 'premium_rt'.
        top_n: Max results.
    """
    _require_ak()
    try:
        import akshare as ak
        df = ak.bond_cb_jsl(cookie="")
        if df is None or df.empty:
            return _err("未获取到可转债数据")
        records = _df_to_records(df)

        filtered = []
        for r in records:
            price = _float(r.get("price"))
            premium = _float(r.get("premium_rt"))
            if price is None or premium is None:
                continue
            dl = price + premium
            rating = str(r.get("rating_cd", "")).strip().upper()

            if max_double_low is not None:
                if dl > max_double_low:
                    continue
            else:
                if price > max_price or premium > max_premium:
                    continue
            if min_rating and (not rating or rating < min_rating):
                continue

            filtered.append({
                "cb_code": r.get("bond_id"),
                "cb_name": r.get("bond_nm"),
                "cb_price": price,
                "convert_price": _float(r.get("convert_price")),
                "premium_rt": premium,
                "convert_value": _float(r.get("convert_value")),
                "ytm_rt": _float(r.get("ytm_rt")),
                "rating": rating,
                "double_low": round(dl, 2),
                "stock_code": r.get("stock_id"),
                "stock_name": r.get("stock_nm"),
            })

        key_map = {"double_low": "double_low", "ytm_rt": "ytm_rt", "premium_rt": "premium_rt"}
        sort_key = key_map.get(sort_by, "double_low")
        reverse = sort_by != "ytm_rt"  # YTM higher is better; lower double_low/premium is better
        filtered.sort(key=lambda x: x.get(sort_key, 999) or 999, reverse=reverse)
        top = filtered[:top_n]

        return _ok({
            "strategy": f"max_double_low={max_double_low}" if max_double_low else f"price<{max_price}, premium<{max_premium}",
            "count": len(top),
            "bonds": top,
        }, source="akshare", market="convertible_bond")
    except Exception as e:
        return _err(f"可转债筛选失败: {e}")


# ===================================================================
# Options 期权
# ===================================================================

_OPTION_UNDERLYINGS = {
    "50ETF":     "510050",
    "300ETF_SH": "510300",
    "300ETF_SZ": "159919",
    "500ETF":    "510500",
    "1000ETF":   "512100",
    "KCB50ETF":  "588000",
    "CYBETF":    "159915",
}


def get_option_chain(underlying: str = "50ETF") -> str:
    """Get T-quote for ETF options (calls + puts) for a given underlying.

    Args:
        underlying: '50ETF' | '300ETF_SH' | '300ETF_SZ' | '500ETF' | '1000ETF' | 'KCB50ETF' | 'CYBETF'.
    """
    _require_ak()
    code = _OPTION_UNDERLYINGS.get(underlying)
    if code is None:
        return _err(f"不支持的标的: {underlying}，可选: {list(_OPTION_UNDERLYINGS.keys())}")
    try:
        import akshare as ak
        df = ak.option_finance_board(symbol=code, end_month="")
        if df is None or df.empty:
            return _err(f"未获取到 {underlying} 期权数据")
        records = _df_to_records(df)
        calls = []
        puts = []
        for r in records:
            entry = {
                "code": r.get("code"),
                "name": r.get("name"),
                "strike": _float(r.get("exercise_price")),
                "expiry": str(r.get("expire_date", ""))[:10],
                "price": _float(r.get("price")),
                "volume": _float(r.get("volume")),
                "open_interest": _float(r.get("position")),
                "iv": _float(r.get("implied_volatility")),
                "delta": _float(r.get("delta")),
                "gamma": _float(r.get("gamma")),
                "theta": _float(r.get("theta")),
                "vega": _float(r.get("vega")),
                "rho": _float(r.get("rho")),
            }
            opt_type = str(r.get("option_type", "")).lower()
            if opt_type in ("c", "call", "认购"):
                calls.append(entry)
            else:
                puts.append(entry)

        # compute max pain and PCR
        total_call_oi = sum(c.get("open_interest") or 0 for c in calls)
        total_put_oi = sum(p.get("open_interest") or 0 for p in puts)
        pcr = round(total_put_oi / total_call_oi, 3) if total_call_oi else None

        return _ok({
            "underlying": underlying,
            "underlying_code": code,
            "calls": calls[:40],
            "puts": puts[:40],
            "pcr": pcr,
        }, source="akshare", market="options")
    except Exception as e:
        return _err(f"期权数据获取失败: {e}")


# ===================================================================
# Limit Board 涨停板
# ===================================================================

def get_limit_board(date: str = "") -> str:
    """Get daily limit-up / limit-down stock pool.

    Args:
        date: Trading date YYYY-MM-DD. Empty = latest trading day.
    
    Returns: limit-up stocks with lock time, order book ratio, consecutive days;
             limit-down stocks; broken-board (炸板) stocks.
    """
    _require_ak()
    try:
        import akshare as ak
        date_compact = date.replace("-", "") if date else ""
        if date_compact:
            df = ak.stock_zt_pool_em(date=date_compact)
        else:
            df = ak.stock_zt_pool_em()
        if df is None or df.empty:
            return _err("未获取到涨停板数据")
        records = _df_to_records(df)
        up_list = []
        down_list = []
        broken_list = []
        for r in records:
            entry = {
                "code": r.get("代码"),
                "name": r.get("名称"),
                "change_pct": _float(r.get("涨跌幅")),
                "price": _float(r.get("最新价")),
                "limit_up_time": r.get("涨停时间"),   # e.g. "093000"
                "open_count": _int(r.get("炸板次数")),
                "consecutive_days": _int(r.get("连板数")),
                "limit_order_ratio": _float(r.get("封单金额")),
                "turnover_rate": _float(r.get("换手率")),
                "sector": r.get("所属行业"),
            }
            pct = entry.get("change_pct") or 0
            open_cnt = entry.get("open_count") or 0
            if pct >= 9.5:
                if open_cnt > 0:
                    broken_list.append({**entry, "type": "limit_up_broken"})
                else:
                    up_list.append({**entry, "type": "limit_up"})
            elif pct <= -9.5:
                down_list.append({**entry, "type": "limit_down"})

        return _ok({
            "date": date or "latest",
            "limit_up_count": len(up_list),
            "limit_down_count": len(down_list),
            "broken_board_count": len(broken_list),
            "limit_up": up_list[:50],
            "broken_board": broken_list[:30],
            "limit_down": down_list[:50],
        }, source="akshare", market="a_share")
    except Exception as e:
        return _err(f"涨停板数据获取失败: {e}")


# ===================================================================
# Dividend Calendar 分红除权
# ===================================================================

def get_dividend_calendar(code: str = "", year: str = "") -> str:
    """Get dividend / ex-rights calendar for A-share stocks.

    Args:
        code: Stock code (e.g. '600519') or empty for all.
        year: Year (e.g. '2026') or empty for current.
    """
    _require_ak()
    try:
        import akshare as ak
        if not year:
            from datetime import date
            year = str(date.today().year)
        func = getattr(ak, "stock_dividend_cninfo", None) or getattr(ak, "stock_dividents_cninfo")
        if func is None:
            return _err("akshare 版本不支持分红数据，请升级: pip install akshare --upgrade")
        df = func(symbol=code if code else "")
        if df is None or df.empty:
            return _err("未获取到分红数据")
        records = _df_to_records(df)
        slim = []
        for r in records:
            plan_date = str(r.get("预案公告日", "") or "")
            ex_date = str(r.get("除权除息日", "") or "")
            if year and year not in plan_date and year not in ex_date:
                continue
            slim.append({
                "code": r.get("证券代码"),
                "name": r.get("证券简称"),
                "plan_announce_date": plan_date[:10] if plan_date else None,
                "ex_rights_date": ex_date[:10] if ex_date else None,
                "record_date": str(r.get("股权登记日", "") or "")[:10] or None,
                "dividend_per_share": _float(r.get("派息比例")),  # 元/股
                "bonus_share_ratio": _float(r.get("送股比例")),
                "transfer_share_ratio": _float(r.get("转增比例")),
                "total_ratio": _float(r.get("总比例")),
                "dividend_yield": _float(r.get("股息率")),
                "plan_preview": r.get("分红方案说明", ""),
            })
        return _ok({"year": year, "count": len(slim), "dividends": slim}, source="akshare", market="a_share")
    except Exception as e:
        return _err(f"分红数据获取失败: {e}")


# ===================================================================
# Helpers
# ===================================================================

def _float(val: Any) -> float | None:
    if val in (None, "", "-", "nan"):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _int(val: Any) -> int | None:
    if val in (None, "", "-", "nan"):
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None
