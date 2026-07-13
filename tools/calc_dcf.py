"""薄 DCF：假设 → FCFF / WACC / 终值 / 敏感性矩阵（无静默默认假设）。"""

from __future__ import annotations

import json
import logging
from typing import Any

from market.eastmoney_indicator_fields import extract_normalized_list
from market.envelope import err, ok, to_float
from tools.base import BaseTool
from tools.stock_research import FinancialStatementsTool

logger = logging.getLogger(__name__)

_TV_WARN = 0.70
_G_WARN = 0.05


def _as_rate(v: Any, name: str) -> float:
    """接受 0.25 或 25（百分数）。比率类：|v|>1 视为百分数。"""
    x = to_float(v)
    if x is None:
        raise ValueError(f"缺少或无效参数: {name}")
    if abs(x) > 1.0 and name in (
        "ebit_margin", "tax_rate", "da_to_revenue", "capex_to_revenue",
        "nwc_to_delta_revenue", "rf", "erp", "kd", "g", "debt_weight",
        "revenue_growth",
    ):
        return x / 100.0
    return x


def _growth_list(raw: Any) -> list[float]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 5:
        raise ValueError("缺少必选参数: revenue_growth（须为长度5的数组）")
    out: list[float] = []
    for i, g in enumerate(raw):
        x = to_float(g)
        if x is None:
            raise ValueError(f"revenue_growth[{i}] 无效")
        if abs(x) > 1.0:
            x = x / 100.0
        out.append(x)
    return out


def compute_dcf(
    *,
    revenue_t0: float,
    revenue_growth: list[float],
    ebit_margin: float,
    tax_rate: float,
    da_to_revenue: float,
    capex_to_revenue: float,
    nwc_to_delta_revenue: float,
    rf: float,
    beta: float,
    erp: float,
    debt_weight: float,
    kd: float,
    g: float,
    shares: float,
    net_debt: float,
    exit_multiple: float | None = None,
) -> dict[str, Any]:
    """纯计算。金额与 revenue_t0 同单位；shares 为股数；返回每股价值=股权价值/股数。"""
    warnings: list[str] = []
    if g >= 1.0:  # 误传 2.5 当 250% 等已在 _as_rate 处理；此处防 g>=100%
        raise ValueError("永续增长率 g 异常偏大")
    if tax_rate < 0 or tax_rate > 0.5:
        warnings.append(f"税率 {tax_rate*100:.1f}% 偏离常见 15%/25% 区间")
    elif abs(tax_rate - 0.15) > 0.02 and abs(tax_rate - 0.25) > 0.02:
        warnings.append(f"税率 {tax_rate*100:.1f}% 非常见 15%/25%，已按传入值计算")
    if g > _G_WARN:
        warnings.append(f"g={g*100:.2f}% > 5%，仅警告、不拒算")

    ke = rf + beta * erp
    wacc = ke * (1.0 - debt_weight) + kd * (1.0 - tax_rate) * debt_weight
    if g >= wacc:
        raise ValueError("永续增长率必须小于 WACC")

    years: list[dict[str, float]] = []
    rev_prev = revenue_t0
    for t in range(1, 6):
        growth = revenue_growth[t - 1]
        rev = rev_prev * (1.0 + growth)
        ebit = rev * ebit_margin
        nopat = ebit * (1.0 - tax_rate)
        da = rev * da_to_revenue
        capex = rev * capex_to_revenue
        nwc = (rev - rev_prev) * nwc_to_delta_revenue
        fcff = nopat + da - capex - nwc
        years.append({
            "year": t,
            "revenue": rev,
            "ebit": ebit,
            "nopat": nopat,
            "da": da,
            "capex": capex,
            "nwc_change": nwc,
            "fcff": fcff,
            "growth": growth,
        })
        rev_prev = rev

    if years[0]["nopat"] < 0:
        warnings.append("DCF 对亏损公司不适用，建议用 PS/EV-Sales 替代")

    def _enterprise_value(w: float, g_use: float, tv_mode: str) -> dict[str, float]:
        pv_fcff = 0.0
        for row in years:
            t = int(row["year"])
            pv_fcff += row["fcff"] / ((1.0 + w) ** t)
        y5 = years[-1]
        if tv_mode == "gordon":
            if g_use >= w:
                return {"enterprise_value": float("nan"), "tv": float("nan"), "pv_tv": float("nan"), "pv_fcff": pv_fcff}
            tv = y5["fcff"] * (1.0 + g_use) / (w - g_use)
        else:
            assert exit_multiple is not None
            ebitda5 = y5["ebit"] + y5["da"]
            tv = ebitda5 * exit_multiple
        pv_tv = tv / ((1.0 + w) ** 5)
        ev = pv_fcff + pv_tv
        return {"enterprise_value": ev, "tv": tv, "pv_tv": pv_tv, "pv_fcff": pv_fcff}

    gordon = _enterprise_value(wacc, g, "gordon")
    gordon_equity = gordon["enterprise_value"] - net_debt
    gordon_ps = gordon_equity / shares if shares else float("nan")
    tv_share = (
        gordon["pv_tv"] / gordon["enterprise_value"]
        if gordon["enterprise_value"]
        else float("nan")
    )
    checks = [
        {
            "id": "tv_share",
            "ok": not (tv_share > _TV_WARN),
            "detail": f"终值占比 {tv_share*100:.1f}%",
            "threshold": ">70% 警告",
        },
        {
            "id": "g_lt_wacc",
            "ok": g < wacc,
            "detail": f"g={g*100:.2f}% < WACC={wacc*100:.2f}%",
        },
        {
            "id": "g_gt_5pct",
            "ok": g <= _G_WARN,
            "detail": f"g={g*100:.2f}%",
            "note": "仅警告" if g > _G_WARN else None,
        },
    ]
    if tv_share > _TV_WARN:
        warnings.append(f"终值现值/企业价值={tv_share*100:.1f}% > 70%")

    exit_result = None
    if exit_multiple is not None:
        ex = _enterprise_value(wacc, g, "exit")
        eq = ex["enterprise_value"] - net_debt
        exit_result = {
            "enterprise_value": ex["enterprise_value"],
            "equity_value": eq,
            "per_share": eq / shares if shares else float("nan"),
            "exit_multiple": exit_multiple,
            "ebitda_y5": years[-1]["ebit"] + years[-1]["da"],
            "tv": ex["tv"],
            "tv_share": ex["pv_tv"] / ex["enterprise_value"] if ex["enterprise_value"] else None,
        }

    # 敏感性：每股（永续增长法）
    wacc_cols = [wacc + d for d in (-0.01, -0.005, 0.0, 0.005, 0.01)]
    g_rows = [g + d for d in (-0.005, -0.0025, 0.0, 0.0025, 0.005)]
    matrix: list[list[float | None]] = []
    for g_i in g_rows:
        row_out: list[float | None] = []
        for w_j in wacc_cols:
            if g_i >= w_j:
                row_out.append(None)
                continue
            ev = _enterprise_value(w_j, g_i, "gordon")["enterprise_value"]
            ps = (ev - net_debt) / shares if shares else None
            row_out.append(None if ps is None else round(ps, 4))
        matrix.append(row_out)

    return {
        "wacc": wacc,
        "ke": ke,
        "years": years,
        "gordon": {
            "enterprise_value": gordon["enterprise_value"],
            "equity_value": gordon_equity,
            "per_share": gordon_ps,
            "tv": gordon["tv"],
            "tv_share": tv_share,
            "pv_fcff": gordon["pv_fcff"],
            "pv_tv": gordon["pv_tv"],
        },
        "exit": exit_result,
        "sensitivity": {
            "wacc_cols": [round(x * 100, 4) for x in wacc_cols],
            "g_rows": [round(x * 100, 4) for x in g_rows],
            "per_share": matrix,
            "base_cell": {"wacc_idx": 2, "g_idx": 2},
        },
        "checks": checks,
        "warnings": warnings,
    }


def _fetch_shares_net_debt(code: str) -> tuple[float | None, float | None, dict[str, Any]]:
    """从 indicators(+balance) 取股本与净债务。金额单位：元。"""
    meta: dict[str, Any] = {}
    fst = FinancialStatementsTool()
    ind = json.loads(fst.execute({"code": code, "statement": "indicators", "period": "annual"}, None))
    if not ind.get("ok"):
        return None, None, meta
    norms = extract_normalized_list((ind.get("data") or {}).get("periods") or [])
    if not norms:
        return None, None, meta
    n0 = norms[0]
    shares = to_float(n0.get("shares_outstanding"))
    cash = to_float(n0.get("cash"))
    debt = to_float(n0.get("interest_debt"))
    # indicators 通常无 cash/interest_debt → 补 balance
    if cash is None or debt is None:
        bal = json.loads(fst.execute({"code": code, "statement": "balance", "period": "annual"}, None))
        if bal.get("ok"):
            bn = extract_normalized_list((bal.get("data") or {}).get("periods") or [])
            if bn:
                cash = cash if cash is not None else to_float(bn[0].get("cash"))
                debt = debt if debt is not None else to_float(bn[0].get("interest_debt"))
    net_debt = None
    if cash is not None or debt is not None:
        net_debt = (debt or 0.0) - (cash or 0.0)
    meta = {
        "report_date": n0.get("report_date"),
        "revenue_display": n0.get("revenue"),
        "shares": shares,
        "cash": cash,
        "interest_debt": debt,
        "net_debt": net_debt,
    }
    return shares, net_debt, meta


def _round_years(years: list[dict[str, float]], unit_div: float) -> list[dict[str, Any]]:
    out = []
    for y in years:
        out.append({
            "year": y["year"],
            "revenue": round(y["revenue"] / unit_div, 4),
            "ebit": round(y["ebit"] / unit_div, 4),
            "nopat": round(y["nopat"] / unit_div, 4),
            "da": round(y["da"] / unit_div, 4),
            "capex": round(y["capex"] / unit_div, 4),
            "nwc_change": round(y["nwc_change"] / unit_div, 4),
            "fcff": round(y["fcff"] / unit_div, 4),
            "growth_pct": round(y["growth"] * 100, 4),
        })
    return out


class CalcDcfTool(BaseTool):
    name = "calc_dcf"
    summary = "薄 DCF（假设→FCFF/敏感性，无默认假设）"
    description = (
        "按 Agent 显式假设计算 5 年 FCFF、WACC、永续增长/退出倍数点估计与 5×5 敏感性矩阵。"
        "必传: revenue_t0, revenue_growth[5], ebit_margin, tax_rate, da_to_revenue, "
        "capex_to_revenue, nwc_to_delta_revenue, rf, beta, erp, debt_weight, kd, g。"
        "可选 exit_multiple；shares/net_debt 可省略并由 code 从财报读取（元）。"
        "不做行业默认静默填入；不输出综合估值区间。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "可选，用于拉取股本/净债务展示"},
            "revenue_t0": {"type": "number", "description": "基期营收（与 revenue_unit 一致）"},
            "revenue_unit": {
                "type": "string",
                "enum": ["yuan", "yi"],
                "default": "yuan",
                "description": "yuan=元（与 normalized 一致）; yi=亿元",
            },
            "revenue_growth": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 5,
                "maxItems": 5,
                "description": "未来5年营收增长率，如 [0.12,0.10,0.08,0.07,0.05] 或百分数",
            },
            "ebit_margin": {"type": "number"},
            "tax_rate": {"type": "number"},
            "da_to_revenue": {"type": "number", "description": "折旧摊销/营收"},
            "capex_to_revenue": {"type": "number"},
            "nwc_to_delta_revenue": {
                "type": "number",
                "description": "营运资本变动 / Δ营收",
            },
            "rf": {"type": "number", "description": "无风险利率"},
            "beta": {"type": "number"},
            "erp": {"type": "number", "description": "股权风险溢价"},
            "debt_weight": {"type": "number", "description": "目标 D/(D+E)"},
            "kd": {"type": "number", "description": "债务成本"},
            "g": {"type": "number", "description": "永续增长率"},
            "exit_multiple": {"type": "number", "description": "可选 EV/EBITDA 退出倍数"},
            "shares": {"type": "number", "description": "总股本（股）；省略则用 code 财报"},
            "net_debt": {"type": "number", "description": "净债务（与营收同单位）；省略则用财报"},
        },
        "required": [
            "revenue_t0",
            "revenue_growth",
            "ebit_margin",
            "tax_rate",
            "da_to_revenue",
            "capex_to_revenue",
            "nwc_to_delta_revenue",
            "rf",
            "beta",
            "erp",
            "debt_weight",
            "kd",
            "g",
        ],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        missing = [
            k for k in self.parameters["required"]  # type: ignore[index]
            if args.get(k) is None
        ]
        if missing:
            return err(f"缺少必选参数: {', '.join(missing)}")

        try:
            growth = _growth_list(args.get("revenue_growth"))
            ebit_margin = _as_rate(args.get("ebit_margin"), "ebit_margin")
            tax_rate = _as_rate(args.get("tax_rate"), "tax_rate")
            da_r = _as_rate(args.get("da_to_revenue"), "da_to_revenue")
            capex_r = _as_rate(args.get("capex_to_revenue"), "capex_to_revenue")
            nwc_r = _as_rate(args.get("nwc_to_delta_revenue"), "nwc_to_delta_revenue")
            rf = _as_rate(args.get("rf"), "rf")
            erp = _as_rate(args.get("erp"), "erp")
            kd = _as_rate(args.get("kd"), "kd")
            g = _as_rate(args.get("g"), "g")
            debt_weight = _as_rate(args.get("debt_weight"), "debt_weight")
            beta = to_float(args.get("beta"))
            if beta is None:
                return err("缺少必选参数: beta")
            rev0 = to_float(args.get("revenue_t0"))
            if rev0 is None:
                return err("缺少必选参数: revenue_t0")
            unit = str(args.get("revenue_unit") or "yuan").lower()
            exit_mult = to_float(args.get("exit_multiple"))
        except ValueError as exc:
            return err(str(exc))

        code = str(args.get("code") or "").strip() or None
        shares = to_float(args.get("shares"))
        net_debt_arg = to_float(args.get("net_debt"))
        fund_meta: dict[str, Any] = {}
        # 内部统一换算为「元」再算，保证每股=元/股
        rev0_yuan = rev0 * 1e8 if unit == "yi" else rev0
        net_debt_yuan: float | None = None
        if net_debt_arg is not None:
            net_debt_yuan = net_debt_arg * 1e8 if unit == "yi" else net_debt_arg
        if code and (shares is None or net_debt_yuan is None):
            sh, nd, fund_meta = _fetch_shares_net_debt(code)
            if shares is None:
                shares = sh
            if net_debt_yuan is None and nd is not None:
                net_debt_yuan = nd  # 财报已是元
        if shares is None or shares <= 0:
            return err("缺少总股本 shares（请显式传入或提供 code 以便读取）")
        if net_debt_yuan is None:
            return err("缺少净债务 net_debt（请显式传入或提供 code 以便读取）")

        try:
            result = compute_dcf(
                revenue_t0=rev0_yuan,
                revenue_growth=growth,
                ebit_margin=ebit_margin,
                tax_rate=tax_rate,
                da_to_revenue=da_r,
                capex_to_revenue=capex_r,
                nwc_to_delta_revenue=nwc_r,
                rf=rf,
                beta=beta,
                erp=erp,
                debt_weight=debt_weight,
                kd=kd,
                g=g,
                shares=shares,
                net_debt=net_debt_yuan,
                exit_multiple=exit_mult,
            )
        except ValueError as exc:
            return err(str(exc))

        # 展示：FCFF 表用亿元
        table_div = 1e8
        money_unit_label = "亿元"

        def _scale_money(x: float | None) -> float | None:
            if x is None:
                return None
            return round(x / table_div, 4)

        gordon = result["gordon"]
        exit_r = result["exit"]
        valuation = {
            "gordon_growth": {
                "per_share": round(gordon["per_share"], 4),
                "per_share_unit": "元",
                "enterprise_value": _scale_money(gordon["enterprise_value"]),
                "equity_value": _scale_money(gordon["equity_value"]),
                "tv_share": round(gordon["tv_share"], 4),
                "tv_share_pct": round(gordon["tv_share"] * 100, 2),
            }
        }
        if exit_r:
            valuation["exit_multiple"] = {
                "per_share": round(exit_r["per_share"], 4),
                "per_share_unit": "元",
                "enterprise_value": _scale_money(exit_r["enterprise_value"]),
                "equity_value": _scale_money(exit_r["equity_value"]),
                "exit_multiple": exit_r["exit_multiple"],
                "ebitda_y5": _scale_money(exit_r["ebitda_y5"]),
            }

        return ok(
            {
                "code": code,
                "assumptions": {
                    "revenue_t0": rev0,
                    "revenue_t0_yuan": rev0_yuan,
                    "revenue_unit": unit,
                    "revenue_growth": growth,
                    "ebit_margin": ebit_margin,
                    "tax_rate": tax_rate,
                    "da_to_revenue": da_r,
                    "capex_to_revenue": capex_r,
                    "nwc_to_delta_revenue": nwc_r,
                    "rf": rf,
                    "beta": beta,
                    "erp": erp,
                    "debt_weight": debt_weight,
                    "kd": kd,
                    "g": g,
                    "exit_multiple": exit_mult,
                    "shares": shares,
                    "net_debt_yuan": net_debt_yuan,
                    "wacc": result["wacc"],
                    "ke": result["ke"],
                },
                "fundamentals_snapshot": fund_meta or None,
                "fcff_table": _round_years(result["years"], table_div),
                "fcff_unit": money_unit_label,
                "valuation": valuation,
                "sensitivity": result["sensitivity"],
                "checks": result["checks"],
                "warnings": result["warnings"],
                "note": "仅点估计；无综合区间。解读由 Agent 完成。",
            },
            market="a_share",
            source="calc",
            tool="calc_dcf",
        )
