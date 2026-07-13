"""事件 → 回测信号 CSV：龙虎榜 / 限售解禁 → signal_file 供 run_backtest(custom)。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from market.envelope import clamp_int, err, ok, to_float
from tools.base import BaseTool
from tools.stock_disclosure import DragonTigerTool, LockupExpiryTool

logger = logging.getLogger(__name__)


def _suffix(bare: str) -> str:
    bare = str(bare).zfill(6)
    if bare.startswith(("5", "6", "9")):
        return f"{bare}.SH"
    if bare.startswith(("4", "8")):
        return f"{bare}.BJ"
    return f"{bare}.SZ"


def _parse_day(s: str) -> datetime:
    return datetime.strptime(str(s)[:10], "%Y-%m-%d")


def _write_signal_csv(
    events: list[tuple[str, str, float]],
    *,
    hold_days: int,
    out_path: Path,
) -> dict[str, Any]:
    """events: (code, signal_date YYYY-MM-DD, weight)."""
    if not events:
        raise ValueError("无事件可写入信号")
    codes = sorted({e[0] for e in events})
    dates = sorted({e[1] for e in events})
    start = _parse_day(dates[0])
    end = _parse_day(dates[-1]) + timedelta(days=hold_days * 2 + 5)
    idx = pd.bdate_range(start, end)
    frame = pd.DataFrame(0.0, index=idx, columns=codes)
    for code, d0, w in events:
        d = pd.Timestamp(_parse_day(d0))
        held = 0
        for ts in frame.index:
            if ts < d:
                continue
            if held >= hold_days:
                break
            frame.at[ts, code] = max(frame.at[ts, code], float(w))
            held += 1

    nonzero = frame.abs().sum(axis=1) > 0
    if nonzero.any():
        first = nonzero.idxmax()
        last = nonzero[::-1].idxmax()
        frame = frame.loc[first:last]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out_path)
    return {
        "path": str(out_path),
        "n_events": len(events),
        "n_codes": len(codes),
        "n_days": int(len(frame)),
        "date_range": (
            f"{frame.index[0].date()} ~ {frame.index[-1].date()}" if len(frame) else None
        ),
    }


class BuildEventSignalsTool(BaseTool):
    name = "build_event_signals"
    summary = "事件驱动信号 CSV（龙虎榜/解禁→run_backtest）"
    description = (
        "把龙虎榜或限售解禁事件转成自定义信号 CSV（权重 0/1），"
        "供 run_backtest(strategy=custom, signal_file=...)。\n"
        "- dragon_tiger: 指定日期区间，净买入额≥阈值的上榜股，"
        "在事件日的下一交易日开始持有 hold_days 天（模拟次日开盘可买）。\n"
        "- lockup: 解禁比例≥阈值，解禁日起持有 hold_days 天"
        "（简化版「利空出尽」；未做跌幅过滤，Agent 可后处理）。\n"
        "默认 signal_lag=1 时请知悉：CSV 上的信号日已是可交易日起点。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "event_type": {
                "type": "string",
                "enum": ["dragon_tiger", "lockup"],
            },
            "start_date": {"type": "string", "description": "YYYY-MM-DD（龙虎榜扫描起点）"},
            "end_date": {"type": "string", "description": "YYYY-MM-DD（龙虎榜扫描终点）"},
            "hold_days": {"type": "integer", "default": 5},
            "min_net_buy": {
                "type": "number",
                "default": 50_000_000,
                "description": "龙虎榜净买入下限（元），默认5000万",
            },
            "min_free_ratio": {
                "type": "number",
                "default": 5.0,
                "description": "解禁比例下限（%，与东财 FREE_RATIO 口径一致时常为百分数）",
            },
            "code": {"type": "string", "description": "可选，限售解禁单票"},
            "output_path": {
                "type": "string",
                "default": "signals/event_signals.csv",
                "description": "相对工作区的输出路径",
            },
        },
        "required": ["event_type"],
    }
    is_readonly = False

    def execute(self, args: dict, ctx) -> str:
        etype = str(args.get("event_type") or "")
        hold_days = clamp_int(args.get("hold_days"), 5, 1, 60)
        out_rel = str(args.get("output_path") or "signals/event_signals.csv")
        try:
            from tools._fs import resolve_path
            out_path = Path(resolve_path(ctx, out_rel))
        except Exception:
            root = Path(getattr(ctx, "root", None) or ".")
            out_path = (root / out_rel).resolve()

        events: list[tuple[str, str, float]] = []
        meta: dict[str, Any] = {"event_type": etype, "hold_days": hold_days}

        if etype == "dragon_tiger":
            start = str(args.get("start_date") or "").strip()
            end = str(args.get("end_date") or "").strip()
            if not start or not end:
                return err("dragon_tiger 需要 start_date 与 end_date")
            min_net = to_float(args.get("min_net_buy")) or 50_000_000.0
            d0, d1 = _parse_day(start), _parse_day(end)
            if d1 < d0:
                return err("end_date 早于 start_date")
            # cap scan window to limit API calls
            days = (d1 - d0).days + 1
            if days > 15:
                return err("龙虎榜扫描窗口请 ≤15 个自然日（避免过多请求）")
            tool = DragonTigerTool()
            cur = d0
            while cur <= d1:
                # skip weekends lightly
                if cur.weekday() < 5:
                    raw = tool.execute({"date": cur.strftime("%Y-%m-%d")}, ctx)
                    env = json.loads(raw)
                    if env.get("ok"):
                        for row in (env.get("data") or {}).get("appearances") or []:
                            net = to_float(row.get("net_buy"))
                            code = row.get("code")
                            if not code or net is None or net < min_net:
                                continue
                            # tradable from next calendar day (engine signal_lag may add another day —
                            # here we set signal on event day so lag=1 → next session)
                            events.append((_suffix(str(code)), cur.strftime("%Y-%m-%d"), 1.0))
                cur += timedelta(days=1)
            meta["min_net_buy"] = min_net
            meta["scan"] = f"{start}~{end}"

        elif etype == "lockup":
            min_ratio = to_float(args.get("min_free_ratio"))
            if min_ratio is None:
                min_ratio = 5.0
            tool = LockupExpiryTool()
            payload: dict[str, Any] = {"horizon_days": 180}
            if args.get("code"):
                payload["code"] = args["code"]
            env = json.loads(tool.execute(payload, ctx))
            if not env.get("ok"):
                return err(env.get("error") or "解禁数据获取失败")
            for row in (env.get("data") or {}).get("records") or []:
                ratio = to_float(row.get("free_ratio"))
                code = row.get("code")
                fd = row.get("free_date")
                if not code or not fd or ratio is None:
                    continue
                # FREE_RATIO 有时是 0.05 有时是 5；兼容
                ratio_pct = ratio * 100 if ratio <= 1 else ratio
                if ratio_pct < min_ratio:
                    continue
                events.append((_suffix(str(code)), str(fd)[:10], 1.0))
            meta["min_free_ratio_pct"] = min_ratio

        else:
            return err("event_type 须为 dragon_tiger 或 lockup")

        if not events:
            return err("未筛到符合阈值的事件")

        try:
            info = _write_signal_csv(events, hold_days=hold_days, out_path=out_path)
        except Exception as exc:
            return err(f"写信号文件失败: {exc}")

        return ok(
            {**meta, **info, "sample_events": events[:15]},
            market="a_share",
            source="eastmoney+calc",
            tool="build_event_signals",
        )
