"""构建 Alpha/risk 因子面板 → research.db + 可选 IC。"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

from analysis.factor_core import compute_ic_series
from market.envelope import clamp_int, err, normalize_meta, now_as_of, ok
from market.factor_zoo import (
    ALPHA_FACTOR_IDS,
    ALPHA_SIGN_EXPECTATION,
    RISK_FACTOR_IDS,
    compute_day_zscores,
    equal_weight_market_returns,
    list_factors,
    purpose_of,
)
from market.market_data import fetch_one
from market.research_store import get_store
from market.trade_calendar import trading_days
from tools.base import BaseTool


def _resolve_codes(args: dict) -> tuple[list[str] | None, str | None]:
    codes = args.get("codes")
    if isinstance(codes, list) and codes:
        return [str(c) for c in codes], None
    asof = str(args.get("universe_asof") or "").strip()
    name = str(args.get("universe_name") or "default")
    if not asof:
        return None, "需要 codes，或提供 universe_asof 加载点位池"
    pit = get_store().load_universe_pit(asof, name=name)
    if pit is None or not pit.get("codes"):
        return None, f"无 universe 快照 asof<={asof} name={name}；请先 build_tradable_universe(save_snapshot=true)"
    max_n = clamp_int(args.get("max_names"), 50, 5, 500)
    return list(pit["codes"])[:max_n], None


class BuildFactorPanelTool(BaseTool):
    name = "build_factor_panel"
    summary = "构建 alpha_*/risk_* 因子面板并写库"
    description = (
        "用 OHLCV 计算价量因子截面（alpha_* 选股 / risk_* 风险，勿混用）。\n"
        "可 codes 或 universe_asof 点位池；结果 bulk 写入 research.db factor_values；\n"
        "可选对 alpha 因子算前瞻1日 IC，符号不符打 invert_signal_note。\n"
        "列出因子：list_only=true。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "codes": {"type": "array", "items": {"type": "string"}},
            "universe_asof": {"type": "string", "description": "点位池日期 YYYY-MM-DD"},
            "universe_name": {"type": "string", "default": "default"},
            "max_names": {"type": "integer", "default": 50},
            "start_date": {"type": "string"},
            "end_date": {"type": "string"},
            "purpose": {
                "type": "string",
                "enum": ["alpha", "risk", "both"],
                "default": "both",
            },
            "factor_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "子集；默认按 purpose 全开",
            },
            "persist": {"type": "boolean", "default": True},
            "compute_ic": {"type": "boolean", "default": True},
            "top_n": {"type": "integer", "default": 10, "description": "最新日 alpha 综合打分 TopN"},
            "list_only": {"type": "boolean", "default": False},
        },
        "required": ["start_date", "end_date"],
    }
    is_readonly = False
    repeatable = True

    def execute(self, args: dict, ctx) -> str:
        if bool(args.get("list_only")):
            return ok(
                {"factors": list_factors()},
                market="a_share",
                tool="build_factor_panel",
                _meta=normalize_meta(source="local", frequency="none", unit="none"),
            )

        start = str(args.get("start_date") or "")
        end = str(args.get("end_date") or "")
        if not start or not end:
            return err("需要 start_date / end_date")

        codes, cerr = _resolve_codes(args)
        if cerr:
            return err(cerr)
        assert codes is not None

        purpose = str(args.get("purpose") or "both")
        requested = args.get("factor_ids")
        if isinstance(requested, list) and requested:
            fids = [str(x) for x in requested]
        elif purpose == "alpha":
            fids = list(ALPHA_FACTOR_IDS)
        elif purpose == "risk":
            fids = list(RISK_FACTOR_IDS)
        else:
            fids = list(ALPHA_FACTOR_IDS) + list(RISK_FACTOR_IDS)

        # load OHLCV
        data: dict[str, pd.DataFrame] = {}
        for code in codes:
            try:
                rows, _ = fetch_one(code, start, end)
                if not rows:
                    continue
                df = pd.DataFrame(rows)
                df["trade_date"] = pd.to_datetime(df["trade_date"])
                data[code] = df.set_index("trade_date").sort_index()
            except Exception:
                continue
        if len(data) < 3:
            return err("有效行情不足 3 只，无法做截面因子")

        codes = list(data.keys())
        days = trading_days(start, end)
        # need lookback buffer inside data — use intersection with panel index
        panel_dates = sorted(set().union(*[set(df.index) for df in data.values()]))
        day_ts = [pd.Timestamp(d) for d in days if pd.Timestamp(d) in set(panel_dates)]
        # skip early warmup
        day_ts = [d for d in day_ts if sum(1 for c in codes if _has_hist(data[c], d, 60)) >= 3]
        if len(day_ts) < 5:
            return err("交易日样本不足（需足够 lookback）")

        mkt = equal_weight_market_returns(data)
        store = get_store()
        bulk: list[tuple[str, str, str, float, str]] = []
        last_alpha: dict[str, float] = {c: 0.0 for c in codes}
        notes: list[str] = []

        for d in day_ts:
            zs = compute_day_zscores(data, d, codes, fids, market_rets=mkt)
            asof = d.strftime("%Y-%m-%d")
            score = {c: 0.0 for c in codes}
            n_a = 0
            for fid, cmap in zs.items():
                pur = purpose_of(fid)
                for code, val in cmap.items():
                    bulk.append((asof, code, fid, float(val), pur))
                    if pur == "alpha":
                        # composite: reverse factors already signed in expectation via -z if needed
                        sign = ALPHA_SIGN_EXPECTATION.get(fid, 1)
                        score[code] += sign * float(val)
                        n_a += 1
            if n_a:
                last_alpha = score

        if bool(args.get("persist", True)) and bulk:
            # prune hot window ~60 trading days before end
            keep_from = day_ts[max(0, len(day_ts) - 60)].strftime("%Y-%m-%d")
            store.prune_factor_values(keep_from)
            store.upsert_factor_values(bulk)

        # IC on alpha factors
        ic_report: dict[str, Any] = {}
        if bool(args.get("compute_ic", True)):
            fwd = _forward_returns(data, day_ts, codes, horizon=1)
            for fid in fids:
                if purpose_of(fid) != "alpha":
                    continue
                fpanel = _factor_panel_from_bulk(bulk, fid, day_ts, codes)
                if fpanel.empty or fwd.empty:
                    continue
                ic = compute_ic_series(fpanel, fwd)
                if ic.empty:
                    continue
                ic_mean = float(ic.mean())
                ic_std = float(ic.std()) if len(ic) > 1 else 0.0
                ir = ic_mean / ic_std if ic_std > 0 else 0.0
                exp = ALPHA_SIGN_EXPECTATION.get(fid, 0)
                entry: dict[str, Any] = {
                    "ic_mean": round(ic_mean, 6),
                    "ic_ir": round(ir, 4),
                    "ic_n": int(len(ic)),
                    "sign_expectation": exp,
                }
                if exp and ic_mean * exp < 0:
                    entry["invert_signal_note"] = (
                        f"IC符号与假设表不符（期望{'正' if exp > 0 else '负'}）"
                    )
                    notes.append(f"{fid}: invert_signal_note")
                if abs(ir) < 1e-6:
                    entry["weak_alpha_note"] = "IC_IR≈0"
                    notes.append(f"{fid}: weak_alpha_note")
                ic_report[fid] = entry

        top_n = clamp_int(args.get("top_n"), 10, 1, 50)
        ranked = sorted(last_alpha.items(), key=lambda x: x[1], reverse=True)
        top = [{"code": c, "score": round(s, 4)} for c, s in ranked[:top_n]]

        meta = normalize_meta(
            source="calc+market_data",
            fetch_time=now_as_of(),
            frequency="daily",
            unit="none",
            purpose=purpose,
        )
        return ok(
            {
                "codes": codes,
                "n_days": len(day_ts),
                "factor_ids": fids,
                "rows_written": len(bulk) if args.get("persist", True) else 0,
                "latest_asof": day_ts[-1].strftime("%Y-%m-%d"),
                "top_scores": top,
                "ic": ic_report,
                "notes": notes,
                "factor_catalog": list_factors(),
            },
            quality="degraded",
            note="价量因子 v0；非财报 Value/Growth。risk_* 勿当选股 Alpha。",
            market="a_share",
            tool="build_factor_panel",
            _meta=meta,
        )


def _has_hist(df: pd.DataFrame, date: pd.Timestamp, need: int) -> bool:
    if date not in df.index:
        return False
    loc = df.index.get_loc(date)
    if isinstance(loc, slice):
        return False
    return int(loc) >= need


def _forward_returns(
    data: dict[str, pd.DataFrame],
    dates: list[pd.Timestamp],
    codes: list[str],
    horizon: int = 1,
) -> pd.DataFrame:
    rows = []
    idx = []
    for d in dates:
        row = {}
        for code in codes:
            df = data[code]
            if d not in df.index:
                row[code] = np.nan
                continue
            loc = df.index.get_loc(d)
            if isinstance(loc, slice):
                row[code] = np.nan
                continue
            i = int(loc)
            if i + horizon >= len(df):
                row[code] = np.nan
                continue
            c0 = float(df["close"].iloc[i])
            c1 = float(df["close"].iloc[i + horizon])
            row[code] = (c1 / c0 - 1.0) if c0 > 0 else np.nan
        rows.append(row)
        idx.append(d)
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))


def _factor_panel_from_bulk(
    bulk: list[tuple[str, str, str, float, str]],
    factor_id: str,
    dates: list[pd.Timestamp],
    codes: list[str],
) -> pd.DataFrame:
    by = {}
    for asof, code, fid, val, _pur in bulk:
        if fid != factor_id:
            continue
        by.setdefault(asof, {})[code] = val
    rows = []
    idx = []
    for d in dates:
        key = d.strftime("%Y-%m-%d")
        rows.append({c: by.get(key, {}).get(c, np.nan) for c in codes})
        idx.append(d)
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))
