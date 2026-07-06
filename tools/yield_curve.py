"""Yield curve tool: China government bond yield curve, LPR, macro rates."""

from __future__ import annotations

from typing import Any

from market.envelope import err, ok, to_float
from tools.base import BaseTool


class YieldCurveTool(BaseTool):
    name = "get_yield_curve"
    summary = "国债收益率曲线 + 宏观利率（LPR/MLF/准备金率）"
    description = (
        "获取中国国债收益率曲线（1Y/3Y/5Y/10Y/30Y）和关键宏观利率。\n"
        "用于资产定价（DCF 折现率）、债券估值、利率周期判断。\n"
        "数据源: akshare（中国人民银行/中债登）"
    )
    parameters = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["yield_curve", "macro_rates", "full"],
                "default": "full",
                "description": "yield_curve=国债收益率, macro_rates=LPR/MLF/准备金, full=全部",
            },
        },
    }
    is_readonly = True

    def execute(self, args: dict[str, Any], ctx: Any) -> str:
        try:
            import akshare as ak
        except ImportError:
            return err("akshare 未安装。请执行: pip install akshare")

        mode = str(args.get("mode", "full"))
        result: dict[str, Any] = {}

        if mode in ("yield_curve", "full"):
            try:
                df = ak.bond_china_yield()
                if df is not None and not df.empty:
                    latest = df.iloc[-1] if len(df) > 0 else {}
                    result["yield_curve"] = {
                        "date": str(latest.get("日期", df.index[-1] if hasattr(df, "index") else "")),
                        "1y": to_float(latest.get("1年")),
                        "3y": to_float(latest.get("3年")),
                        "5y": to_float(latest.get("5年")),
                        "10y": to_float(latest.get("10年")),
                        "30y": to_float(latest.get("30年")),
                        "spread_10y_1y": round((to_float(latest.get("10年")) or 0) - (to_float(latest.get("1年")) or 0), 4),
                        "spread_10y_5y": round((to_float(latest.get("10年")) or 0) - (to_float(latest.get("5年")) or 0), 4),
                    }
            except Exception:
                result["yield_curve"] = {"error": "国债收益率获取失败，请升级 akshare: pip install akshare --upgrade"}

        if mode in ("macro_rates", "full"):
            rates = {}
            try:
                df_lpr = ak.macro_china_lpr()
                if df_lpr is not None and not df_lpr.empty:
                    latest = df_lpr.iloc[-1]
                    rates["lpr_1y"] = to_float(latest.get("1年期LPR"))
                    rates["lpr_5y"] = to_float(latest.get("5年期LPR"))
            except Exception:
                rates["lpr"] = "获取失败"
            try:
                df_rrr = ak.macro_china_reserve_requirement_ratio()
                if df_rrr is not None and not df_rrr.empty:
                    rates["rrr"] = to_float(df_rrr.iloc[-1].get("大型金融机构"))
            except Exception:
                rates["rrr"] = "获取失败"
            try:
                df_mlf = ak.macro_china_market_mlf()
                if df_mlf is not None and not df_mlf.empty:
                    rates["mlf_1y"] = to_float(df_mlf.iloc[-1].get("利率"))
            except Exception:
                rates["mlf"] = "获取失败"
            result["macro_rates"] = rates

        if not result:
            return err("未获取到任何利率数据")

        return ok(result, source="akshare", market="macro")
