"""Barra-lite 组合风险分析工具。"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from market.barra_lite import estimate_factor_model, portfolio_risk
from market.envelope import clamp_int, err, ok
from market.market_data import fetch_one
from tools.base import BaseTool


class AnalyzePortfolioRiskTool(BaseTool):
    name = "analyze_portfolio_risk"
    summary = "Barra-lite 组合风险分解"
    description = (
        "用 mom/size/vol（可选行业哑变量）估计截面因子模型，输出组合波动、"
        "系统/特异风险占比与因子风险贡献。非商业 Barra。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "codes": {
                "type": "array",
                "items": {"type": "string"},
            },
            "weights": {
                "type": "object",
                "description": "{code: weight}，缺省等权",
            },
            "start_date": {"type": "string"},
            "end_date": {"type": "string"},
            "window": {"type": "integer", "default": 20},
            "lookback": {"type": "integer", "default": 60},
            "industry_map": {
                "type": "object",
                "description": "可选 {code: 行业}",
            },
        },
        "required": ["codes", "start_date", "end_date"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        codes = [str(c) for c in (args.get("codes") or [])]
        if not codes:
            return err("需要 codes")
        start = str(args.get("start_date") or "")
        end = str(args.get("end_date") or "")
        if not start or not end:
            return err("需要 start_date/end_date")
        window = clamp_int(args.get("window"), 20, 5, 60)
        lookback = clamp_int(args.get("lookback"), 60, 20, 252)
        w_in = args.get("weights") if isinstance(args.get("weights"), dict) else {}
        weights = {c: float(w_in.get(c, 1.0)) for c in codes}
        ind = args.get("industry_map") if isinstance(args.get("industry_map"), dict) else None

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
        if len(data) < 2:
            return err("至少需要 2 只股票的有效行情")

        try:
            model = estimate_factor_model(
                data, window=window, lookback=lookback, industry_map=ind
            )
            risk = portfolio_risk(weights, model)
        except Exception as exc:
            return err(str(exc))

        # JSON-safe
        risk["factor_cov_diag"] = [
            float(x) for x in __import__("numpy").diag(model["factor_cov"]).tolist()
        ]
        risk["factor_names"] = model["factor_names"]
        return ok(risk, market="a_share", source="calc", tool="analyze_portfolio_risk", quality="degraded")
