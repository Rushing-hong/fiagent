"""Low-latency symbol search fallbacks for A-shares."""

from __future__ import annotations

import json
import re

from market.http import resolve_min_interval, throttled_get

_TENCENT_SUGGEST_URL = "https://smartbox.gtimg.cn/s3/"
_ASSIGNMENT_RE = re.compile(r"^\s*v_hint\s*=\s*(\"(?:\\.|[^\"])*\")\s*;?\s*$", re.S)
_SUFFIX = {"sh": "SH", "sz": "SZ", "bj": "BJ"}


def parse_tencent_suggest(text: str, *, count: int = 25) -> list[dict[str, str]]:
    """Parse Tencent's ``v_hint`` JavaScript assignment into A-share rows."""
    match = _ASSIGNMENT_RE.match(text or "")
    if not match:
        return []
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in str(payload).split("^"):
        parts = raw.split("~")
        if len(parts) < 3:
            continue
        market, code, name = parts[:3]
        suffix = _SUFFIX.get(market.lower())
        if suffix is None or len(code) != 6 or not code.isdigit():
            continue
        symbol = f"{code}.{suffix}"
        if symbol in seen:
            continue
        seen.add(symbol)
        out.append({"symbol": symbol, "name": name, "source": "tencent"})
        if len(out) >= count:
            break
    return out


def search_tencent_suggest(query: str, *, count: int = 25) -> list[dict[str, str]]:
    response = throttled_get(
        _TENCENT_SUGGEST_URL,
        host_key="tencent-suggest",
        min_interval=resolve_min_interval("FIAGENT_TENCENT_MIN_INTERVAL", 0.3),
        params={"q": query, "t": "all"},
        headers={"Referer": "https://gu.qq.com/"},
        timeout=10.0,
    )
    return parse_tencent_suggest(response.text, count=count)
