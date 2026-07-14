"""JSON envelope helpers for market tools.

Quality Flag（简洁版）：
  normal   — 主源、完整，可直接引用
  degraded — 降级源 / 抽样 / 有 caveat，引用时必须说明
  partial  — 部分成功，不得当全量结论
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any, Literal

Quality = Literal["normal", "degraded", "partial"]

_QUALITY_RANK = {"normal": 0, "degraded": 1, "partial": 2}
_TZ_SH = timezone(timedelta(hours=8))


def now_as_of() -> str:
    return datetime.now(_TZ_SH).isoformat(timespec="seconds")


def worse_quality(a: Quality, b: Quality) -> Quality:
    return a if _QUALITY_RANK[a] >= _QUALITY_RANK[b] else b


def ok(
    data: Any,
    *,
    quality: Quality = "normal",
    as_of: str | None = None,
    note: str | None = None,
    _meta: dict[str, Any] | None = None,
    **meta: Any,
) -> str:
    envelope: dict[str, Any] = {
        "ok": True,
        "quality": quality,
        "as_of": as_of or now_as_of(),
    }
    if note:
        envelope["note"] = note
    envelope.update(meta)
    if _meta is not None:
        envelope["_meta"] = normalize_meta(_meta)
    envelope["data"] = data
    return json.dumps(envelope, ensure_ascii=False, default=str)


def normalize_meta(raw: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    """Phase1 数值工具统一 _meta：source / fetch_time / stale / frequency / unit."""
    m = dict(raw or {})
    m.update(kwargs)
    out = {
        "source": str(m.get("source") or "unknown"),
        "fetch_time": str(m.get("fetch_time") or now_as_of()),
        "stale": bool(m.get("stale", False)),
        "frequency": str(m.get("frequency") or "none"),
        "unit": str(m.get("unit") or "none"),
    }
    for k, v in m.items():
        if k not in out:
            out[k] = v
    return out


def err(
    message: str,
    *,
    quality: Quality = "degraded",
    note: str | None = None,
) -> str:
    envelope: dict[str, Any] = {
        "ok": False,
        "quality": quality,
        "as_of": now_as_of(),
        "error": message,
    }
    if note:
        envelope["note"] = note
    return json.dumps(envelope, ensure_ascii=False)


def to_float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(n, hi))
