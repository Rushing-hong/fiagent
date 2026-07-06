"""JSON envelope helpers for market tools."""

from __future__ import annotations

import json
from typing import Any


def ok(data: Any, **meta: Any) -> str:
    envelope = {"ok": True, **meta, "data": data}
    return json.dumps(envelope, ensure_ascii=False)


def err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


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
