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


# --- Phase1 L1 chain guards (unit / frequency) ---------------------------------

_UNIT_ALIASES: dict[str, str] = {
    "cny_yuan": "CNY_yuan",
    "yuan": "CNY_yuan",
    "cny_wan": "CNY_wan",
    "10k cny": "CNY_wan",
    "10k_cny": "CNY_wan",
    "wan": "CNY_wan",
    "cny_yi": "CNY_yi",
    "yi": "CNY_yi",
    "ratio": "ratio",
    "index_point": "index_point",
    "none": "none",
}


def normalize_unit(unit: str | None) -> str:
    u = str(unit or "none").strip()
    return _UNIT_ALIASES.get(u.lower(), u)


def assert_unit_compatible(
    producer_unit: str,
    consumer_unit: str,
    *,
    converted: bool = False,
) -> None:
    """金额/单位不一致且未换算 → fail（L1）。"""
    if converted:
        return
    a, b = normalize_unit(producer_unit), normalize_unit(consumer_unit)
    if a == b:
        return
    raise ValueError(
        f"unit mismatch: producer={a!r} consumer={b!r}（需显式换算或统一 unit）"
    )


def assert_frequency_compatible(
    producer_freq: str,
    consumer_mode: str,
) -> None:
    """
    频率消费规则（L1）：
    - monthly/quarterly 不可被「连续 N 个交易日」类日频 streak 逻辑静默消费
    - daily 可喂日频；event 可喂事件聚合，不可假装成月频序列
    """
    p = str(producer_freq or "none").lower()
    c = str(consumer_mode or "none").lower()
    if c in ("daily_streak", "consecutive_trading_days", "rolling_ndays"):
        if p in ("monthly", "quarterly"):
            raise ValueError(
                f"frequency mismatch: {p} 不可按 {c} 消费（勿把月/季频当连续交易日）"
            )
    if c == "monthly_series" and p == "daily":
        raise ValueError(
            f"frequency mismatch: daily 不可直接当 monthly_series（需先聚合）"
        )
    if c == "monthly_series" and p == "event":
        raise ValueError(
            f"frequency mismatch: event 不可直接当 monthly_series"
        )


def assert_meta_chain(
    producer_meta: dict[str, Any],
    *,
    expect_unit: str | None = None,
    expect_frequency_mode: str | None = None,
    converted: bool = False,
) -> None:
    """对工具信封 `_meta` 做链式断言。"""
    meta = normalize_meta(producer_meta)
    if expect_unit is not None:
        assert_unit_compatible(meta["unit"], expect_unit, converted=converted)
    if expect_frequency_mode is not None:
        assert_frequency_compatible(meta["frequency"], expect_frequency_mode)
