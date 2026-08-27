"""Market tape snippets for the Web UI ticker."""

from __future__ import annotations

import logging
import time
from typing import Any

from market.http import throttled_get_json

logger = logging.getLogger(__name__)

_ULIST = "https://push2.eastmoney.com/api/qt/ulist.np/get"
# 上证 / 深成 / 沪深300 / 中证500 / 创业板
_SECIDS = "1.000001,0.399001,1.000300,1.000905,0.399006"
_CACHE_TTL = 12.0
_cache: dict[str, Any] = {"ts": 0.0, "items": []}


def fetch_ticker(*, force: bool = False) -> dict[str, Any]:
    now = time.monotonic()
    if not force and _cache["items"] and now - float(_cache["ts"]) < _CACHE_TTL:
        return {"ok": True, "items": _cache["items"], "cached": True}

    try:
        data = throttled_get_json(
            _ULIST,
            host_key="push2.eastmoney.com",
            min_interval=1.2,
            params={
                "fltt": "2",
                "invt": "2",
                "fields": "f2,f3,f4,f12,f14",
                "secids": _SECIDS,
            },
            timeout=8,
        )
        rows = (((data or {}).get("data") or {}).get("diff")) or []
        items: list[dict[str, Any]] = []
        for row in rows:
            name = str(row.get("f14") or row.get("f12") or "")
            price = row.get("f2")
            pct = row.get("f3")
            try:
                pct_f = float(pct) if pct is not None and pct != "-" else None
            except (TypeError, ValueError):
                pct_f = None
            try:
                price_f = float(price) if price is not None and price != "-" else None
            except (TypeError, ValueError):
                price_f = None
            direction = "flat"
            if pct_f is not None:
                if pct_f > 0:
                    direction = "up"
                elif pct_f < 0:
                    direction = "down"
            items.append(
                {
                    "name": name,
                    "code": str(row.get("f12") or ""),
                    "price": price_f,
                    "pct": pct_f,
                    "direction": direction,
                }
            )
        if items:
            _cache["items"] = items
            _cache["ts"] = now
        return {"ok": True, "items": items or _cache["items"], "cached": False}
    except Exception as exc:
        logger.debug("ticker fetch failed: %s", exc)
        return {"ok": False, "items": _cache["items"], "error": str(exc), "cached": True}
