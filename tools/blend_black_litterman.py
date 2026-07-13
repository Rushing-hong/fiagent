"""Black-Litterman 观点融合 → 组合权重 / 回测信号 CSV。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from market.black_litterman import (
    black_litterman_posterior,
    cov_from_returns,
    views_from_absolute,
)
from market.envelope import err, ok, to_float
from market.market_data import fetch_one
from tools.base import BaseTool


class BlendBlackLittermanTool(BaseTool):
    name = "blend_black_litterman"
    summary = "Black-Litterman 融合观点→权重（可写 signal CSV）"
    description = (
        "用简化 Black-Litterman 把绝对收益观点与市值先验融合，输出后验多头权重。\n"
        "可选拉取日频收益估协方差；也可对角风险近似。\n"
        "输出权重可写成 signal_file 供 run_backtest(custom) 使用（单日权重重复到区间）。\n"
        "这是工程化简版，非完整机构 BL 流水线。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "codes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "资产列表",
            },
            "market_weights": {
                "type": "object",
                "description": "市值先验权重 {code: w}，缺省等权",
            },
            "views": {
                "type": "array",
                "description": (
                    '[{"assets":["600519.SH"],"q":0.03,"confidence":0.6}] '
                    "q=预期超额收益（小数），confidence∈(0,1]"
                ),
            },
            "start_date": {"type": "string", "description": "估协方差用行情起点"},
            "end_date": {"type": "string", "description": "估协方差用行情终点"},
            "delta": {"type": "number", "default": 2.5},
            "tau": {"type": "number", "default": 0.05},
            "output_path": {
                "type": "string",
                "description": "可选，写入信号 CSV 路径",
            },
            "signal_start": {"type": "string"},
            "signal_end": {"type": "string"},
        },
        "required": ["codes", "views"],
    }
    is_readonly = False

    def execute(self, args: dict, ctx) -> str:
        codes = [str(c) for c in (args.get("codes") or [])]
        views = args.get("views")
        if not codes or not isinstance(views, list) or not views:
            return err("需要 codes 与 views")

        mw = args.get("market_weights") if isinstance(args.get("market_weights"), dict) else {}
        w_mkt = np.array([float(mw.get(c, 1.0)) for c in codes], dtype=float)

        # Covariance
        start = str(args.get("start_date") or "")
        end = str(args.get("end_date") or "")
        rets_list: list[np.ndarray] = []
        if start and end:
            px = []
            for code in codes:
                try:
                    rows, _ = fetch_one(code, start, end)
                    df = pd.DataFrame(rows)
                    if df.empty or "close" not in df.columns:
                        px.append(None)
                        continue
                    df["trade_date"] = pd.to_datetime(df["trade_date"])
                    s = df.set_index("trade_date")["close"].astype(float).pct_change().dropna()
                    px.append(s)
                except Exception:
                    px.append(None)
            if all(x is not None for x in px):
                panel = pd.concat(px, axis=1, join="inner")
                panel.columns = codes
                cov = cov_from_returns(panel.values)
            else:
                cov = np.eye(len(codes)) * 0.04
        else:
            cov = np.eye(len(codes)) * 0.04

        try:
            P, Q, omega = views_from_absolute(len(codes), views, codes)
            out = black_litterman_posterior(
                cov,
                w_mkt,
                P=P,
                Q=Q,
                omega=omega,
                delta=float(args.get("delta", 2.5)),
                tau=float(args.get("tau", 0.05)),
            )
        except Exception as exc:
            return err(str(exc))

        weights = {c: float(out["weights"][i]) for i, c in enumerate(codes)}
        payload: dict[str, Any] = {
            "weights": weights,
            "mu": {c: float(out["mu"][i]) for i, c in enumerate(codes)},
            "pi": {c: float(out["pi"][i]) for i, c in enumerate(codes)},
            "note": "long-only 投影后的 BL 权重；可作 custom 信号",
        }

        out_rel = args.get("output_path")
        if out_rel:
            try:
                from tools._fs import resolve_path
                path = Path(resolve_path(ctx, str(out_rel)))
            except Exception:
                path = Path(str(out_rel))
            s0 = str(args.get("signal_start") or start or "2024-01-02")
            s1 = str(args.get("signal_end") or end or s0)
            idx = pd.bdate_range(s0, s1)
            frame = pd.DataFrame(
                {c: weights[c] for c in codes},
                index=idx,
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(path)
            payload["signal_file"] = str(path)
            payload["signal_days"] = int(len(frame))

        return ok(payload, market="a_share", source="calc", tool="blend_black_litterman")
