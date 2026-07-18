"""A股宏观指标（akshare），统一 _meta 信封。"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Callable

import pandas as pd

from market.envelope import err, normalize_meta, now_as_of, ok, to_float
from tools.base import BaseTool

logger = logging.getLogger(__name__)

_CN_MONTH = re.compile(r"(\d{4})\D+(\d{1,2})")


def _parse_cn_month(val: Any) -> str | None:
    if val is None:
        return None
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m-%d")
    s = str(val).strip()
    m = _CN_MONTH.search(s)
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    return f"{y:04d}-{mo:02d}-01"


def _series_from_df(
    df: pd.DataFrame,
    *,
    date_col: str,
    value_col: str,
    indicator: str,
    unit: str,
    frequency: str = "monthly",
) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    if date_col not in df.columns or value_col not in df.columns:
        # fuzzy match
        date_col = next((c for c in df.columns if "月" in str(c) or "日期" in str(c)), date_col)
        value_col = next((c for c in df.columns if c == value_col), value_col)
        if date_col not in df.columns or value_col not in df.columns:
            return []
    out: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        asof = _parse_cn_month(row[date_col])
        val = to_float(row[value_col])
        if asof is None or val is None:
            continue
        out.append({
            "indicator": indicator,
            "asof": asof,
            "value": val,
            "unit": unit,
            "frequency": frequency,
        })
    out.sort(key=lambda x: x["asof"])
    return out


def _fetch_pmi() -> tuple[list[dict[str, Any]], str]:
    import akshare as ak
    df = ak.macro_china_pmi()
    rows = _series_from_df(
        df, date_col="月份", value_col="制造业-指数",
        indicator="pmi_mfg", unit="index_point",
    )
    rows2 = _series_from_df(
        df, date_col="月份", value_col="非制造业-指数",
        indicator="pmi_non_mfg", unit="index_point",
    )
    return rows + rows2, "akshare.macro_china_pmi"


def _fetch_cpi() -> tuple[list[dict[str, Any]], str]:
    import akshare as ak
    df = ak.macro_china_cpi()
    # 全国-同比增长 is YoY %
    rows = _series_from_df(
        df, date_col="月份", value_col="全国-同比增长",
        indicator="cpi_yoy", unit="ratio",
    )
    # store as percent number as published (e.g. 2.1 means 2.1%)
    return rows, "akshare.macro_china_cpi"


def _fetch_money() -> tuple[list[dict[str, Any]], str]:
    import akshare as ak
    df = ak.macro_china_money_supply()
    m2 = _series_from_df(
        df, date_col="月份", value_col="货币和准货币(M2)-同比增长",
        indicator="m2_yoy", unit="ratio",
    )
    m1 = _series_from_df(
        df, date_col="月份", value_col="货币(M1)-同比增长",
        indicator="m1_yoy", unit="ratio",
    )
    return m2 + m1, "akshare.macro_china_money_supply"


def _fetch_gdp() -> tuple[list[dict[str, Any]], str]:
    import akshare as ak
    df = ak.macro_china_gdp()
    # columns vary; try common
    date_col = next((c for c in df.columns if "季" in str(c) or "时间" in str(c) or "月份" in str(c)), df.columns[0])
    val_col = next(
        (c for c in df.columns if "同比" in str(c)),
        next((c for c in df.columns if "GDP" in str(c).upper()), df.columns[1] if len(df.columns) > 1 else df.columns[0]),
    )
    rows = _series_from_df(
        df, date_col=str(date_col), value_col=str(val_col),
        indicator="gdp_yoy", unit="ratio", frequency="quarterly",
    )
    return rows, "akshare.macro_china_gdp"


_FETCHERS: dict[str, Callable[[], tuple[list[dict[str, Any]], str]]] = {
    "pmi": _fetch_pmi,
    "pmi_mfg": _fetch_pmi,
    "cpi": _fetch_cpi,
    "cpi_yoy": _fetch_cpi,
    "m2": _fetch_money,
    "m2_yoy": _fetch_money,
    "money_supply": _fetch_money,
    "gdp": _fetch_gdp,
    "gdp_yoy": _fetch_gdp,
}

_ALIASES = {
    "pmi": ["pmi_mfg", "pmi_non_mfg"],
    "cpi": ["cpi_yoy"],
    "m2": ["m2_yoy", "m1_yoy"],
    "money_supply": ["m2_yoy", "m1_yoy"],
    "gdp": ["gdp_yoy"],
}


class GetMacroDataTool(BaseTool):
    name = "get_macro_data"
    summary = "中国宏观指标（PMI/CPI/M2/GDP）"
    description = (
        "查询中国宏观时间序列（默认 A 股语境）。indicator: pmi / cpi / m2 / gdp "
        "或细分 pmi_mfg / cpi_yoy / m2_yoy / gdp_yoy。"
        "返回带 _meta.frequency=monthly|quarterly；禁止把月频当成「连续N个交易日」。"
        "数据源 akshare（聚合公开页），quality 可能为 degraded。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "indicator": {
                "type": "string",
                "description": "pmi / cpi / m2 / gdp 或细分代码",
            },
            "start_date": {"type": "string", "description": "YYYY-MM-DD，按 asof 过滤"},
            "end_date": {"type": "string"},
            "limit": {"type": "integer", "default": 36, "description": "最近 N 个观测"},
            "persist": {"type": "boolean", "default": True, "description": "写入 research.db"},
        },
        "required": ["indicator"],
    }
    is_readonly = False  # 默认 persist 写 research.db，不可进并行只读池
    repeatable = True

    def execute(self, args: dict, ctx) -> str:
        key = str(args.get("indicator") or "").strip().lower()
        if not key:
            return err("需要 indicator，如 pmi / cpi / m2 / gdp")
        fetcher_key = key if key in _FETCHERS else None
        if fetcher_key is None:
            return err(f"不支持的 indicator: {key}；可选: {sorted(set(_FETCHERS))}")
        # resolve at call time so tests can patch _fetch_* 
        name = {
            "pmi": "_fetch_pmi", "pmi_mfg": "_fetch_pmi",
            "cpi": "_fetch_cpi", "cpi_yoy": "_fetch_cpi",
            "m2": "_fetch_money", "m2_yoy": "_fetch_money", "money_supply": "_fetch_money",
            "gdp": "_fetch_gdp", "gdp_yoy": "_fetch_gdp",
        }[fetcher_key]
        fetcher = globals()[name]

        try:
            rows, source = fetcher()
        except Exception as exc:
            return err(f"拉取宏观失败: {exc}")

        want = set(_ALIASES.get(key, [key]))
        # if key is already a concrete id, filter to it when present
        concrete = {r["indicator"] for r in rows}
        if key in concrete:
            want = {key}
        rows = [r for r in rows if r["indicator"] in want]

        start = str(args.get("start_date") or "")[:10] or None
        end = str(args.get("end_date") or "")[:10] or None
        if start:
            rows = [r for r in rows if r["asof"] >= start]
        if end:
            rows = [r for r in rows if r["asof"] <= end]

        limit = int(args.get("limit") or 36)
        if limit > 0 and len(rows) > limit:
            rows = rows[-limit:]

        fetch_time = now_as_of()
        stale = False
        if rows:
            last = rows[-1]["asof"]
            # monthly: stale if last asof older than ~100 days
            try:
                age = (datetime.now() - datetime.strptime(last[:7] + "-01", "%Y-%m-%d")).days
                stale = age > 100
            except ValueError:
                stale = False

        freq = rows[0]["frequency"] if rows else "monthly"
        unit = rows[0]["unit"] if rows else "none"

        if bool(args.get("persist", True)) and rows:
            try:
                from market.research_store import get_store
                get_store().upsert_macro_points([
                    {**r, "source": "akshare", "fetch_time": fetch_time} for r in rows
                ])
            except Exception as exc:
                logger.warning("macro persist failed: %s", exc)

        meta = normalize_meta(
            source=source,
            fetch_time=fetch_time,
            stale=stale,
            frequency=freq,
            unit=unit,
            indicator=key,
        )
        quality = "degraded"  # free aggregator
        note = (
            "来源 akshare 公开聚合；引用关键数字请核对官方发布。"
            f" frequency={freq}，勿按交易日连续计数。"
        )
        return ok(
            {
                "indicator": key,
                "series": rows,
                "latest": rows[-1] if rows else None,
                "count": len(rows),
            },
            quality=quality,
            note=note,
            market="a_share",
            tool="get_macro_data",
            _meta=meta,
        )
