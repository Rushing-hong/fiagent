"""K 线形态识别工具。支持自定义 PatternConfig 调参。"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from market.market_data import fetch_one
from tools._pattern_lib import (
    PatternConfig,
    _PATTERN_FUNCS,
    candlestick_patterns,
    double_top_bottom,
    head_and_shoulders,
    support_resistance,
    triangle,
)
from tools.base import BaseTool


def _rows_to_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.set_index("trade_date")
    df.index = pd.to_datetime(df.index)
    if "amount" not in df.columns:
        df["amount"] = 0.0
    return df.sort_index()


class PatternTool(BaseTool):
    name = "pattern"
    summary = "K 线技术形态识别（支持自定义阈值调参）"
    description = (
        "对 A 股 OHLCV 做技术形态检测：头肩顶、双顶双底、K 线形态、支撑阻力等。\n"
        "可通过 cfg 参数调节识别阈值，适配日线/周线/分钟线等不同周期。\n\n"
        "示例: {\"code\": \"600519.SH\", \"start_date\": \"2024-01-01\", \"end_date\": \"2024-12-31\"}\n"
        "调参: {\"patterns\": \"candlestick\", \"cfg\": {\"doji_body_ratio\": 0.08, \"hammer_shadow_ratio\": 2.5}}"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "A 股代码，如 600519.SH"},
            "start_date": {"type": "string"},
            "end_date": {"type": "string"},
            "patterns": {
                "type": "string",
                "description": "逗号分隔的形态名，或 'all'。可选: peaks_valleys, candlestick, support_resistance, trend_slope, head_and_shoulders, double_top_bottom, triangle, broadening",
                "default": "all",
            },
            "window": {"type": "integer", "default": 10, "description": "检测窗口大小"},
            "cfg": {
                "type": "object",
                "description": (
                    "可选。自定义检测阈值。可用字段:\n"
                    "  doji_body_ratio (default 0.10): Doji 实体/振幅比\n"
                    "  hammer_shadow_ratio (default 2.0): 锤子线下影/实体比\n"
                    "  hammer_upper_max (default 1.0): 锤子线上影最大倍数\n"
                    "  sr_cluster_pct (default 0.05): 支撑阻力聚类阈值\n"
                    "  hs_shoulder_symmetry (default 0.05): 头肩顶对称性容差\n"
                    "  dtb_tolerance (default 0.03): 双顶双底容差\n"
                    "  triangle_flat_ratio (default 0.02): 三角形平坦判定阈值"
                ),
            },
        },
        "required": ["code", "start_date", "end_date"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        code = str(args.get("code", "")).strip().upper()
        start = args.get("start_date", "")
        end = args.get("end_date", "")
        window = int(args.get("window", 10))
        patterns = args.get("patterns", "all")
        cfg_raw = args.get("cfg") or {}

        # Build PatternConfig from user params
        cfg = PatternConfig.from_dict(cfg_raw) if isinstance(cfg_raw, dict) else PatternConfig()

        if patterns == "all":
            selected = list(_PATTERN_FUNCS.keys())
        else:
            selected = [p.strip() for p in str(patterns).split(",") if p.strip() in _PATTERN_FUNCS]
        if not selected:
            return json.dumps(
                {"ok": False, "error": f"无效形态名，可选: {list(_PATTERN_FUNCS)}"},
                ensure_ascii=False,
            )

        rows, source = fetch_one(code, start, end)
        if not rows:
            return json.dumps({"ok": False, "error": "无行情数据"}, ensure_ascii=False)
        df = _rows_to_df(rows)
        if df.empty:
            return json.dumps({"ok": False, "error": "行情为空"}, ensure_ascii=False)

        code_results: dict[str, Any] = {}
        for name in selected:
            # For patterns that support cfg, use it; otherwise fall back to default lambda
            if name == "candlestick" and df is not None:
                result = candlestick_patterns(df["open"], df["high"], df["low"], df["close"], cfg=cfg)
                code_results[name] = result.value_counts().to_dict()
            elif name == "support_resistance":
                code_results[name] = support_resistance(df["close"], window=window, cfg=cfg)
            elif name == "head_and_shoulders":
                code_results[name] = {"count": int(head_and_shoulders(df["close"], window=window, cfg=cfg).sum())}
            elif name == "double_top_bottom":
                s = double_top_bottom(df["close"], window=window, cfg=cfg)
                code_results[name] = {"double_top": int((s == 1).sum()), "double_bottom": int((s == -1).sum())}
            elif name == "triangle":
                s = triangle(df["close"], window=window, cfg=cfg)
                code_results[name] = {"ascending": int((s == 1).sum()), "descending": int((s == -1).sum())}
            else:
                code_results[name] = _PATTERN_FUNCS[name](df, window)

        return json.dumps(
            {
                "ok": True,
                "code": code,
                "source": source,
                "patterns": selected,
                "window": window,
                "cfg_applied": cfg_raw,
                "results": code_results,
            },
            ensure_ascii=False,
            default=str,
        )
