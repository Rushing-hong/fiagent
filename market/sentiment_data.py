"""Eastmoney guba sentiment + overnight/intraday return decomposition (A-share)."""

from __future__ import annotations

import json
import logging
from typing import Any

from market.a_share_code import to_a_share_symbol
from market.http import DEFAULT_USER_AGENT, throttled_get

logger = logging.getLogger(__name__)

_GUBA_HOST = "guba"
_BULL_KEYWORDS = (
    "看涨",
    "上涨",
    "买入",
    "加仓",
    "利好",
    "突破",
    "牛市",
    "反弹",
    "抄底",
    "多头",
    "强势",
    "看好",
    "机会",
    "低估",
    "涨停",
    "大涨",
    "起飞",
)
_BEAR_KEYWORDS = (
    "看跌",
    "下跌",
    "卖出",
    "减仓",
    "利空",
    "跌破",
    "熊市",
    "跳水",
    "逃顶",
    "空头",
    "弱势",
    "看空",
    "风险",
    "高估",
    "崩盘",
    "跌停",
    "大跌",
    "割肉",
)


def _bare_code(code: str) -> str:
    normalized = to_a_share_symbol(str(code).strip())
    bare, _, suffix = normalized.rpartition(".")
    if suffix not in ("SH", "SZ", "BJ") or len(bare) != 6 or not bare.isdigit():
        raise ValueError(f"invalid A-share code: {code!r}")
    return bare


def _coerce_int(value: Any) -> int | None:
    if value in (None, "", "-"):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _extract_json_after_marker(text: str, marker: str) -> str | None:
    idx = text.find(marker)
    if idx < 0:
        return None
    start = idx + len(marker)
    while start < len(text) and text[start] in " \t\n\r=":
        start += 1
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _guba_list_url(bare: str, page: int) -> str:
    if page <= 1:
        return f"https://guba.eastmoney.com/list,{bare}.html"
    return f"https://guba.eastmoney.com/list,{bare},{page}.html"


def _parse_post(raw: dict[str, Any]) -> dict[str, Any]:
    user = raw.get("post_user") if isinstance(raw.get("post_user"), dict) else {}
    return {
        "post_id": raw.get("post_id"),
        "title": str(raw.get("post_title") or "").strip(),
        "author": str(user.get("user_nickname") or user.get("user_name") or "").strip(),
        "time": str(
            raw.get("post_publish_time")
            or raw.get("post_display_time")
            or raw.get("post_last_time")
            or ""
        ).strip(),
        "read_count": _coerce_int(raw.get("post_click_count")),
        "comment_count": _coerce_int(raw.get("post_comment_count")),
    }


def fetch_guba_posts(code: str, page: int = 1) -> list[dict[str, Any]]:
    """Fetch Eastmoney guba list posts for an A-share code (bare 6-digit or ######.SH)."""
    bare = _bare_code(code)
    page = max(1, int(page))
    url = _guba_list_url(bare, page)
    try:
        resp = throttled_get(
            url,
            host_key=_GUBA_HOST,
            min_interval=0.5,
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Referer": "https://guba.eastmoney.com/",
            },
            timeout=15.0,
        )
        html = resp.text
    except Exception as exc:
        logger.warning("guba fetch failed for %s page %s: %s", bare, page, exc)
        raise

    raw_json = _extract_json_after_marker(html, "article_list")
    if not raw_json:
        logger.warning("guba article_list not found for %s page %s", bare, page)
        return []

    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        logger.warning("guba JSON parse failed for %s page %s: %s", bare, page, exc)
        return []

    items = payload.get("re") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []

    posts: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            parsed = _parse_post(item)
            if parsed.get("title"):
                posts.append(parsed)
    return posts


def score_guba_sentiment(posts: list[dict[str, Any]]) -> dict[str, Any]:
    """Lexicon/heuristic sentiment score in [-1, 1] from post titles."""
    n = len(posts)
    if n == 0:
        return {
            "score": 0.0,
            "n_posts": 0,
            "bull_ratio": 0.0,
            "bear_ratio": 0.0,
            "bull_hits": 0,
            "bear_hits": 0,
            "neutral_hits": 0,
        }

    bull_hits = 0
    bear_hits = 0
    neutral_hits = 0
    scores: list[float] = []

    for post in posts:
        text = str(post.get("title") or "")
        if not text:
            continue
        bull = sum(1 for kw in _BULL_KEYWORDS if kw in text)
        bear = sum(1 for kw in _BEAR_KEYWORDS if kw in text)
        if bull > bear:
            bull_hits += 1
            scores.append(min(1.0, (bull - bear) / 3.0))
        elif bear > bull:
            bear_hits += 1
            scores.append(max(-1.0, -(bear - bull) / 3.0))
        else:
            neutral_hits += 1
            scores.append(0.0)

    scored_n = len(scores)
    score = sum(scores) / scored_n if scored_n else 0.0
    return {
        "score": round(max(-1.0, min(1.0, score)), 4),
        "n_posts": n,
        "bull_ratio": round(bull_hits / scored_n, 4) if scored_n else 0.0,
        "bear_ratio": round(bear_hits / scored_n, 4) if scored_n else 0.0,
        "bull_hits": bull_hits,
        "bear_hits": bear_hits,
        "neutral_hits": neutral_hits,
    }


def overnight_vs_intraday(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Decompose daily returns from OHLCV rows.

    overnight_t = open_t / close_{t-1} - 1
    intraday_t  = close_t / open_t - 1
    """
    if not rows:
        return []

    sorted_rows = sorted(rows, key=lambda r: str(r.get("trade_date") or ""))
    out: list[dict[str, Any]] = []
    prev_close: float | None = None

    for row in sorted_rows:
        try:
            open_px = float(row["open"])
            close_px = float(row["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if open_px <= 0 or close_px <= 0:
            continue

        overnight: float | None = None
        if prev_close is not None and prev_close > 0:
            overnight = open_px / prev_close - 1.0
        intraday = close_px / open_px - 1.0
        out.append({
            "trade_date": str(row.get("trade_date") or "")[:10],
            "overnight": round(overnight, 6) if overnight is not None else None,
            "intraday": round(intraday, 6),
            "close": close_px,
        })
        prev_close = close_px
    return out


def summarize_overnight_intraday(
    series: list[dict[str, Any]],
    *,
    last_n: int = 5,
) -> dict[str, Any]:
    """Summary stats for overnight/intraday decomposition series."""
    if not series:
        return {"error": "no series"}

    overnights = [x["overnight"] for x in series if x.get("overnight") is not None]
    intradays = [x["intraday"] for x in series if x.get("intraday") is not None]
    tail = series[-last_n:] if last_n > 0 else series

    def _mean(vals: list[float]) -> float | None:
        return round(sum(vals) / len(vals), 6) if vals else None

    return {
        "n_days": len(series),
        "overnight_mean": _mean(overnights),
        "intraday_mean": _mean(intradays),
        "last_n": last_n,
        "recent": tail,
    }
