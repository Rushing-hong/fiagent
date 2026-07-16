"""北向资金信号化（市场级，非个股聪明钱神话）。"""

from __future__ import annotations

from typing import Any

from market.envelope import clamp_int, err, normalize_meta, now_as_of, ok
from market.eastmoney import get_json
from tools.base import BaseTool
from tools.stock_flow import (
    _HISTORY_FIELDS1,
    _HISTORY_FIELDS2,
    _HISTORY_URL,
    _REALTIME_FIELDS,
    _REALTIME_URL,
    _parse_history,
    _parse_realtime,
)


def _signalize(history: list[dict[str, Any]], streak_n: int = 3) -> dict[str, Any]:
    """history items: date, total (万元)."""
    series = []
    for h in history:
        # stock_flow history keys — check parse
        total = h.get("total")
        if total is None:
            total = h.get("net")
        if total is None:
            continue
        series.append({"date": str(h.get("date") or h.get("trade_date") or "")[:10], "total_wan": float(total)})
    if not series:
        return {"error": "无历史序列"}

    vals = [x["total_wan"] for x in series]
    latest = vals[-1]
    # percentile of latest among history
    pct = sum(1 for v in vals if v <= latest) / len(vals)
    # streak of positive / negative
    streak = 0
    sign = 1 if latest >= 0 else -1
    for v in reversed(vals):
        if (v >= 0 and sign > 0) or (v < 0 and sign < 0):
            streak += 1
        else:
            break
    streak_signal = None
    if streak >= streak_n and sign > 0:
        streak_signal = "inflow_streak"
    elif streak >= streak_n and sign < 0:
        streak_signal = "outflow_streak"

    strength = "high" if pct >= 0.9 or pct <= 0.1 else ("elevated" if pct >= 0.75 or pct <= 0.25 else "normal")
    return {
        "latest_total_wan": round(latest, 2),
        "latest_date": series[-1]["date"],
        "percentile": round(pct, 4),
        "streak_days": streak,
        "streak_signal": streak_signal,
        "strength": strength,
        "unit": "CNY_wan",
        "note": "市场级北向净流入信号；不等于个股聪明钱拆解",
    }


class NorthboundSignalTool(BaseTool):
    name = "northbound_signal"
    summary = "北向资金日频信号（连续净流入/分位）"
    description = (
        "基于 get_northbound_flow 历史，输出：最新净流入（万元）、历史分位、"
        "连续流入/流出天数与 streak_signal。单位 CNY_wan；勿与元混用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "lookback_days": {"type": "integer", "default": 60},
            "streak_n": {"type": "integer", "default": 3},
            "persist": {"type": "boolean", "default": True},
        },
    }
    is_readonly = False
    repeatable = True

    def execute(self, args: dict, ctx) -> str:
        lookback = clamp_int(args.get("lookback_days"), 60, 5, 250)
        streak_n = clamp_int(args.get("streak_n"), 3, 2, 20)
        try:
            realtime_payload = get_json(_REALTIME_URL, params={"fields": _REALTIME_FIELDS})
            history_payload = get_json(
                _HISTORY_URL,
                params={
                    "fields1": _HISTORY_FIELDS1,
                    "fields2": _HISTORY_FIELDS2,
                    "klt": "101",
                    "lmt": "250",
                },
            )
        except Exception as exc:
            return err(str(exc))

        history = _parse_history(history_payload, lookback)
        # normalize keys
        norm = []
        for h in history:
            if not isinstance(h, dict):
                continue
            total = h.get("total")
            if total is None:
                sh = h.get("shanghai_connect")
                sz = h.get("shenzhen_connect")
                if sh is not None or sz is not None:
                    total = (sh or 0) + (sz or 0)
            norm.append({
                "date": h.get("date") or h.get("trade_date") or h.get("timestamp"),
                "total": total,
            })
        sig = _signalize(norm, streak_n=streak_n)
        if "error" in sig:
            return err(sig["error"])

        if bool(args.get("persist", True)):
            try:
                from market.research_store import get_store
                get_store().upsert_micro_signals([{
                    "asof": sig["latest_date"],
                    "code": "_NORTHBOUND_",
                    "signal_id": "northbound_total_wan",
                    "value": sig["latest_total_wan"],
                    "unit": "CNY_wan",
                    "meta_json": sig,
                }])
            except Exception:
                pass

        meta = normalize_meta(
            source="eastmoney",
            fetch_time=now_as_of(),
            frequency="daily",
            unit="CNY_wan",
        )
        return ok(
            {
                "realtime": _parse_realtime(realtime_payload),
                "signal": sig,
                "history_tail": norm[-10:],
            },
            quality="degraded",
            market="a_share",
            tool="northbound_signal",
            _meta=meta,
        )
