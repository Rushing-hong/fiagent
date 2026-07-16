"""VaR / CVaR 与 A 股情景压力工具。"""

from __future__ import annotations

from typing import Any

import numpy as np

from market.envelope import err, normalize_meta, now_as_of, ok, to_float
from market.market_data import fetch_one
from market.risk_metrics import (
    ASHARE_STRESS_SCENARIOS,
    apply_stress_shocks,
    historical_var,
    parametric_var,
)
from tools.base import BaseTool


class CalcVarTool(BaseTool):
    name = "calc_var"
    summary = "组合/标的 VaR 与 CVaR"
    description = (
        "历史模拟或参数法（正态）估计日频 VaR/CVaR。"
        "可传 returns 数组，或 codes+日期拉收盘算等权组合收益。"
        "输出损失为正；_meta.frequency=daily。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "returns": {"type": "array", "items": {"type": "number"}},
            "codes": {"type": "array", "items": {"type": "string"}},
            "start_date": {"type": "string"},
            "end_date": {"type": "string"},
            "alpha": {"type": "number", "default": 0.05},
            "method": {"type": "string", "enum": ["historical", "parametric", "both"], "default": "both"},
        },
    }
    is_readonly = True
    repeatable = True

    def execute(self, args: dict, ctx) -> str:
        alpha = float(args.get("alpha") or 0.05)
        method = str(args.get("method") or "both")
        rets = args.get("returns")
        if isinstance(rets, list) and rets:
            arr = np.array([float(x) for x in rets], dtype=float)
        else:
            codes = args.get("codes")
            start = str(args.get("start_date") or "")
            end = str(args.get("end_date") or "")
            if not isinstance(codes, list) or not codes or not start or not end:
                return err("需要 returns，或 codes+start_date+end_date")
            arr = _equal_weight_returns([str(c) for c in codes], start, end)
            if arr.size < 10:
                return err("收益样本不足")
        try:
            out: dict[str, Any] = {"alpha": alpha}
            if method in ("historical", "both"):
                out["historical"] = historical_var(arr, alpha=alpha)
            if method in ("parametric", "both"):
                out["parametric"] = parametric_var(arr, alpha=alpha)
        except Exception as exc:
            return err(str(exc))
        meta = normalize_meta(
            source="calc",
            fetch_time=now_as_of(),
            frequency="daily",
            unit="ratio",
        )
        return ok(out, market="a_share", tool="calc_var", quality="degraded", _meta=meta,
                  note="VaR 为损失正数；基于历史收益，不含涨跌停路径依赖。")


class RunStressTestTool(BaseTool):
    name = "run_stress_test"
    summary = "A股预设情景压力测试"
    description = (
        "对当前权益做 2015股灾/2018贸易战/2020疫情/2022封控 等情景一次性冲击。"
        "含涨跌停流动性冻结的粗近似（固定 shock），非逐日回放。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "equity": {"type": "number", "description": "组合权益（元）", "default": 1_000_000},
            "scenarios": {
                "type": "array",
                "items": {"type": "string"},
                "description": f"子集，默认全部。可选: {list(ASHARE_STRESS_SCENARIOS)}",
            },
        },
    }
    is_readonly = True
    repeatable = True

    def execute(self, args: dict, ctx) -> str:
        equity = to_float(args.get("equity"))
        if equity is None:
            equity = 1_000_000.0
        scenarios = args.get("scenarios") if isinstance(args.get("scenarios"), list) else None
        results = apply_stress_shocks(float(equity), scenarios=scenarios)
        meta = normalize_meta(
            source="calc",
            fetch_time=now_as_of(),
            frequency="event",
            unit="CNY_yuan",
        )
        return ok(
            {
                "equity": equity,
                "results": results,
                "catalog": {
                    k: {"label": v["label"], "start": v["start"], "end": v["end"], "shock": v["shock"]}
                    for k, v in ASHARE_STRESS_SCENARIOS.items()
                },
            },
            market="a_share",
            tool="run_stress_test",
            quality="degraded",
            note="情景为固定冲击近似，用于压力沟通而非精确回放。",
            _meta=meta,
        )


def _equal_weight_returns(codes: list[str], start: str, end: str) -> np.ndarray:
    import pandas as pd
    closes = {}
    for code in codes:
        try:
            rows, _ = fetch_one(code, start, end)
            if not rows:
                continue
            df = pd.DataFrame(rows)
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            closes[code] = df.set_index("trade_date")["close"].astype(float)
        except Exception:
            continue
    if len(closes) < 1:
        return np.array([])
    panel = pd.DataFrame(closes).sort_index().ffill()
    return panel.pct_change().mean(axis=1).dropna().values
