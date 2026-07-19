"""A-share ESG helpers: carbon prices (akshare) and CNINFO disclosure search."""

from __future__ import annotations

import importlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import pandas as pd

from market.a_share_code import to_a_share_symbol
from market.envelope import to_float

logger = logging.getLogger(__name__)

_TZ_SH = timezone(timedelta(hours=8))

_CNINFO_QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
_CNINFO_PDF_BASE = "http://static.cninfo.com.cn/"
_CNINFO_COLUMN = "szse"

ak: Any | None = None
_AK_AVAILABLE: bool | None = None

_CARBON_SOURCES: dict[str, str] = {
    "domestic": "energy_carbon_domestic",
    "bj": "energy_carbon_bj",
    "sh": "energy_carbon_sh",
    "gz": "energy_carbon_gz",
    "hb": "energy_carbon_hb",
    "sz": "energy_carbon_sz",
    "eu": "energy_carbon_eu",
}

_EXCHANGE_ALIASES: dict[str, str] = {
    "bj": "北京",
    "beijing": "北京",
    "北京": "北京",
    "sh": "上海",
    "shanghai": "上海",
    "上海": "上海",
    "gz": "广州",
    "guangzhou": "广州",
    "广州": "广州",
    "hb": "湖北",
    "hubei": "湖北",
    "湖北": "湖北",
    "sz": "深圳",
    "shenzhen": "深圳",
    "深圳": "深圳",
    "cq": "重庆",
    "chongqing": "重庆",
    "重庆": "重庆",
    "domestic": "全国",
    "全国": "全国",
    "eu": "EU",
    "欧洲": "EU",
}


class EsgDataError(Exception):
    """Raised when ESG data fetch fails."""


def _require_ak() -> None:
    """Delay akshare's expensive import until ESG data is requested."""
    global ak, _AK_AVAILABLE
    if _AK_AVAILABLE is False:
        raise EsgDataError("akshare 未安装，无法拉取碳价")
    if ak is not None:
        _AK_AVAILABLE = True
        return
    try:
        ak = importlib.import_module("akshare")
        _AK_AVAILABLE = True
    except ImportError:
        _AK_AVAILABLE = False
        raise EsgDataError("akshare 未安装，无法拉取碳价")


def _carbon_df_to_rows(df: pd.DataFrame, *, market: str, source: str) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    cols = list(df.columns)
    if len(cols) < 5:
        return []
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        trade_date = row.iloc[0]
        if hasattr(trade_date, "isoformat"):
            trade_date = trade_date.isoformat()
        else:
            trade_date = str(trade_date)[:10]
        exchange = str(row.iloc[4] or market).strip() or market
        rows.append({
            "market": market,
            "exchange": exchange,
            "trade_date": trade_date,
            "price": to_float(row.iloc[1]),
            "volume": to_float(row.iloc[2]),
            "amount": to_float(row.iloc[3]),
            "unit": "CNY_per_ton",
            "source": source,
        })
    return rows


def fetch_carbon_prices() -> list[dict[str, Any]]:
    """Fetch China regional + EU carbon market daily prices via akshare."""
    _require_ak()
    assert ak is not None

    rows: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        domestic_df = ak.energy_carbon_domestic()
        rows.extend(_carbon_df_to_rows(
            domestic_df,
            market="CN",
            source="akshare.energy_carbon_domestic",
        ))
    except Exception as exc:
        errors.append(f"domestic: {exc}")
        logger.warning("carbon domestic fetch failed: %s", exc)

    for key in ("eu",):
        fn_name = _CARBON_SOURCES[key]
        try:
            fn = getattr(ak, fn_name)
            part = _carbon_df_to_rows(fn(), market="EU", source=f"akshare.{fn_name}")
            if part:
                for item in part:
                    item["exchange"] = item.get("exchange") or "EU"
                rows.extend(part)
        except Exception as exc:
            errors.append(f"{key}: {exc}")
            logger.warning("carbon %s fetch failed: %s", key, exc)

    if not rows:
        msg = "碳价数据为空"
        if errors:
            msg += f"（{'; '.join(errors[:3])}）"
        raise EsgDataError(msg)
    rows.sort(key=lambda x: (x.get("exchange") or "", x.get("trade_date") or ""))
    return rows


def filter_carbon_prices(
    rows: list[dict[str, Any]],
    exchange: str | None,
) -> list[dict[str, Any]]:
    if not exchange:
        return rows
    key = str(exchange).strip().lower()
    target = _EXCHANGE_ALIASES.get(key, exchange.strip())
    if target.upper() == "EU":
        return [r for r in rows if str(r.get("market") or "").upper() == "EU"]
    if target in ("全国", "domestic"):
        return [r for r in rows if str(r.get("market") or "").upper() == "CN"]
    return [
        r for r in rows
        if target in str(r.get("exchange") or "")
        or str(r.get("exchange") or "") in target
    ]


def _ms_to_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        ms = int(value)
    except (TypeError, ValueError):
        s = str(value).strip()
        return s[:10] if s else None
    return datetime.fromtimestamp(ms / 1000, tz=_TZ_SH).strftime("%Y-%m-%d")


def _cninfo_pdf_url(adjunct_url: str | None) -> str | None:
    if not adjunct_url:
        return None
    raw = str(adjunct_url).strip()
    if raw.startswith("http"):
        return raw
    return f"{_CNINFO_PDF_BASE}{raw.lstrip('/')}"


def _parse_cninfo_announcements(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("secCode") or "").strip()
        symbol = to_a_share_symbol(code) if code else None
        out.append({
            "title": str(item.get("announcementTitle") or "").strip(),
            "date": _ms_to_date(item.get("announcementTime")),
            "url": _cninfo_pdf_url(item.get("adjunctUrl")),
            "code": symbol or code or None,
            "name": str(item.get("secName") or "").strip() or None,
            "announcement_id": item.get("announcementId"),
        })
    return out


def _search_cninfo_http(
    keyword: str,
    *,
    page_size: int,
    code: str | None = None,
) -> list[dict[str, Any]]:
    import requests

    payload: dict[str, Any] = {
        "pageNum": 1,
        "pageSize": max(1, min(page_size, 30)),
        "column": _CNINFO_COLUMN,
        "tabName": "fulltext",
        "searchkey": keyword.strip(),
        "seDate": "",
        "sortName": "time",
        "sortType": "desc",
        "isHLtitle": "true",
    }
    if code:
        bare = to_a_share_symbol(code).split(".")[0]
        payload["searchkey"] = f"{bare} {keyword}".strip()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    resp = requests.post(_CNINFO_QUERY_URL, data=payload, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("announcements") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    rows = _parse_cninfo_announcements(items)
    if code:
        bare = to_a_share_symbol(code).split(".")[0]
        rows = [r for r in rows if not r.get("code") or str(r["code"]).startswith(bare)]
    return rows[:page_size]


def _search_cninfo_akshare_fallback(
    keyword: str,
    *,
    page_size: int,
    code: str | None,
) -> list[dict[str, Any]]:
    if not code:
        return []
    _require_ak()
    import akshare as ak

    bare = to_a_share_symbol(code).split(".")[0]
    end = datetime.now(_TZ_SH).strftime("%Y%m%d")
    start = (datetime.now(_TZ_SH) - timedelta(days=365 * 3)).strftime("%Y%m%d")
    fn = getattr(ak, "stock_zh_a_disclosure_report_cninfo", None)
    if fn is None:
        return []
    df = fn(symbol=bare, keyword=keyword, start_date=start, end_date=end)
    if df is None or df.empty:
        return []
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        title = str(row.get("公告标题") or (row.iloc[0] if len(row) else "")).strip()
        pub = row.get("公告时间") if "公告时间" in df.columns else row.iloc[1] if len(row) > 1 else None
        url = row.get("公告链接") if "公告链接" in df.columns else None
        if hasattr(pub, "strftime"):
            pub = pub.strftime("%Y-%m-%d")
        else:
            pub = str(pub)[:10] if pub else None
        rows.append({
            "title": title,
            "date": pub,
            "url": str(url).strip() if url else None,
            "code": to_a_share_symbol(bare),
            "name": None,
            "announcement_id": None,
        })
        if len(rows) >= page_size:
            break
    return rows


def search_cninfo_esg(
    keyword: str,
    page_size: int = 20,
    *,
    code: str | None = None,
) -> tuple[list[dict[str, Any]], str, str]:
    """
    Search CNINFO for ESG / sustainability disclosures.

    Returns (reports, source, quality).
    """
    if not str(keyword or "").strip() and not code:
        raise EsgDataError("keyword 或 code 至少填一项")

    query = str(keyword or "ESG").strip() or "ESG"
    limit = max(1, min(int(page_size or 20), 30))

    try:
        rows = _search_cninfo_http(query, page_size=limit, code=code)
        if rows:
            return rows, "cninfo.hisAnnouncement", "normal"
    except Exception as exc:
        logger.warning("CNINFO search failed: %s", exc)
        try:
            rows = _search_cninfo_akshare_fallback(query, page_size=limit, code=code)
            if rows:
                return rows, "akshare.stock_zh_a_disclosure_report_cninfo", "degraded"
        except Exception as fb_exc:
            logger.warning("CNINFO akshare fallback failed: %s", fb_exc)
        raise EsgDataError(f"巨潮 ESG 公告检索失败: {exc}") from exc

    if code:
        try:
            rows = _search_cninfo_akshare_fallback(query, page_size=limit, code=code)
            if rows:
                return rows, "akshare.stock_zh_a_disclosure_report_cninfo", "degraded"
        except Exception as exc:
            logger.warning("CNINFO empty + akshare fallback failed: %s", exc)

    return [], "cninfo.hisAnnouncement", "normal"
