"""金融计算层：杜邦分解 + 红旗扫描（读 normalized，自取数）。"""

from __future__ import annotations

import json
import logging
from typing import Any

from market.eastmoney_indicator_fields import (
    extract_normalized_list,
    is_financial_board_name,
    mapping_failed,
)
from market.envelope import err, ok, to_float
from tools.base import BaseTool
from tools.stock_disclosure import SectorInfoTool
from tools.stock_research import FinancialStatementsTool

logger = logging.getLogger(__name__)

_DUPONT_REQUIRED = ("net_income", "total_equity", "revenue", "total_assets")
_NEAR_ZERO_NI_RATIO = 0.001  # |NI|/|Rev| < 0.1%
_FLOAT_TOL = 0.001  # 0.1% absolute on ROE in decimal form → compare in percent pts


def _fetch_statement(code: str, statement: str, period: str = "annual") -> dict[str, Any]:
    raw = FinancialStatementsTool().execute(
        {"code": code, "statement": statement, "period": period},
        None,
    )
    return json.loads(raw)


def _annual_norms(env: dict[str, Any]) -> list[dict[str, Any]]:
    if not env.get("ok"):
        return []
    periods = (env.get("data") or {}).get("periods") or []
    norms = extract_normalized_list(periods)
    # 仅年报（取数已 period=annual，再保险过滤）
    out = []
    for n in norms:
        rd = str(n.get("report_date") or "")
        if rd.endswith("-12-31"):
            out.append(n)
    return out


def _detect_financial(code: str) -> bool:
    try:
        raw = SectorInfoTool().execute({"code": code, "mode": "membership"}, None)
        env = json.loads(raw)
    except Exception as exc:
        logger.warning("sector membership failed for %s: %s", code, exc)
        return False
    if not env.get("ok"):
        return False
    boards = (env.get("data") or {}).get("boards") or []
    for b in boards:
        name = str(b.get("board_name") or "")
        if is_financial_board_name(name):
            return True
    return False


def _merge_by_date(*norm_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 report_date 合并多表 normalized，后者非空字段覆盖。"""
    by_date: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for norms in norm_lists:
        for row in norms:
            rd = str(row.get("report_date") or "")
            if not rd:
                continue
            if rd not in by_date:
                by_date[rd] = {"report_date": rd}
                order.append(rd)
            base = by_date[rd]
            for k, v in row.items():
                if k in ("report_date", "statement", "_sources"):
                    continue
                if v is not None:
                    base[k] = v
    # 保持 indicators 日期顺序（通常已按新→旧）
    return [by_date[d] for d in order if d in by_date]


# ---------------------------------------------------------------------------
# DuPont
# ---------------------------------------------------------------------------


def _chain_substitution(
    base: dict[str, float],
    curr: dict[str, float],
) -> dict[str, Any]:
    """连环替代：净利率 → 周转 → 杠杆。因子为小数。"""
    npm0, at0, em0 = base["npm"], base["at"], base["em"]
    npm1, at1, em1 = curr["npm"], curr["at"], curr["em"]
    roe0 = npm0 * at0 * em0
    roe1 = npm1 * at0 * em0
    roe2 = npm1 * at1 * em0
    roe3 = npm1 * at1 * em1
    return {
        "npm_contribution": roe1 - roe0,
        "at_contribution": roe2 - roe1,
        "em_contribution": roe3 - roe2,
        "total_change": roe3 - roe0,
        "roe_base": roe0,
        "roe_curr": roe3,
    }


class CalcDupontTool(BaseTool):
    name = "calc_dupont"
    summary = "杜邦分解（3/5因子 + 连环替代）"
    description = (
        "对 A 股代码做杜邦分解：自取 indicators（normalized），"
        "输出年报 3 因子表、同比与连环替代贡献度；"
        "有 ebit/pretax 时附 5 因子。金融股改用 ROE=ROA×权益乘数。"
        "勿手算。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "A股代码，如 600519.SH"},
            "has_ma": {
                "type": "boolean",
                "default": False,
                "description": "报告期内重大并购/重组，同比口径可能被破坏",
            },
        },
        "required": ["code"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        code = str(args.get("code") or "").strip()
        if not code:
            return err("需要 code")
        has_ma = bool(args.get("has_ma"))

        ind = _fetch_statement(code, "indicators", "annual")
        if not ind.get("ok"):
            return err(ind.get("error") or f"无法获取 {code} indicators")

        norms = _annual_norms(ind)
        if mapping_failed(norms, _DUPONT_REQUIRED):
            return err(
                "字段映射失败，需更新映射表",
                note="normalized 缺少 net_income/total_equity/revenue/total_assets",
            )

        # 5 因子：缺 ebit/pretax 时补拉 income
        need_income = any(
            n.get("ebit") is None or n.get("pretax_income") is None for n in norms[:5]
        )
        if need_income:
            inc = _fetch_statement(code, "income", "annual")
            if inc.get("ok"):
                norms = _merge_by_date(norms, _annual_norms(inc))

        is_fin = _detect_financial(code)
        rows_out: list[dict[str, Any]] = []
        factor_series: list[dict[str, float]] = []

        for n in norms:
            ni = to_float(n.get("net_income"))
            eq = to_float(n.get("total_equity"))
            rev = to_float(n.get("revenue"))
            ta = to_float(n.get("total_assets"))
            rd = n.get("report_date")
            east_roe = to_float(n.get("roe"))

            if eq is not None and eq <= 0:
                rows_out.append({
                    "report_date": rd,
                    "roe": None,
                    "note": "资不抵债",
                    "npm": None,
                    "asset_turnover": None,
                    "equity_multiplier": None,
                })
                factor_series.append({})
                continue
            if None in (ni, eq, rev, ta) or rev == 0 or ta == 0:
                rows_out.append({
                    "report_date": rd,
                    "roe": None,
                    "note": "字段缺失",
                    "npm": None,
                    "asset_turnover": None,
                    "equity_multiplier": None,
                })
                factor_series.append({})
                continue

            roe = ni / eq
            npm = ni / rev
            at = rev / ta
            em = ta / eq
            product = npm * at * em
            validation = "ok" if abs(product - roe) <= _FLOAT_TOL else "failed"

            row: dict[str, Any] = {
                "report_date": rd,
                "roe": round(roe * 100, 4),
                "npm": round(npm * 100, 4),
                "asset_turnover": round(at, 6),
                "equity_multiplier": round(em, 6),
                "validation": validation,
            }
            if is_fin:
                roa = ni / ta
                row["mode"] = "financial_roa_em"
                row["roa"] = round(roa * 100, 4)
                row["check"] = round(roa * em * 100, 4)
            else:
                row["mode"] = "3factor"

            if east_roe is not None:
                # 东财 ROEJQ 为百分数
                diff_pp = roe * 100 - east_roe
                row["eastmoney_roe_weighted"] = east_roe
                if abs(diff_pp) > 2.0:
                    row["roe_gap_note"] = (
                        f"东财加权 ROE={east_roe}%，与期末口径差异{diff_pp:+.2f}pp"
                    )

            # 5 因子
            ebit = to_float(n.get("ebit"))
            pretax = to_float(n.get("pretax_income"))
            if (
                not is_fin
                and ebit is not None
                and pretax is not None
                and ebit != 0
                and pretax != 0
                and rev != 0
            ):
                tax_burden = ni / pretax
                interest_burden = pretax / ebit
                op_margin = ebit / rev
                five = tax_burden * interest_burden * op_margin * at * em
                row["five_factor"] = {
                    "tax_burden": round(tax_burden, 6),
                    "interest_burden": round(interest_burden, 6),
                    "ebit_margin": round(op_margin * 100, 4),
                    "product_roe_pct": round(five * 100, 4),
                    "validation": "ok" if abs(five - roe) <= _FLOAT_TOL else "failed",
                }

            rows_out.append(row)
            factor_series.append({"npm": npm, "at": at, "em": em, "roe": roe})

        # 连环替代：最近两期有效年报
        attribution: dict[str, Any] | None = None
        valid_idx = [i for i, f in enumerate(factor_series) if f]
        if len(valid_idx) >= 2:
            i_curr, i_base = valid_idx[0], valid_idx[1]
            attr = _chain_substitution(factor_series[i_base], factor_series[i_curr])
            attribution = {
                "base_date": rows_out[i_base]["report_date"],
                "curr_date": rows_out[i_curr]["report_date"],
                "npm_contribution_pp": round(attr["npm_contribution"] * 100, 4),
                "at_contribution_pp": round(attr["at_contribution"] * 100, 4),
                "em_contribution_pp": round(attr["em_contribution"] * 100, 4),
                "total_change_pp": round(attr["total_change"] * 100, 4),
                "order": "npm→asset_turnover→equity_multiplier",
            }
            if has_ma:
                attribution["warning"] = "可比口径可能被破坏（has_ma=true）"
        else:
            attribution = {"note": "N/A", "reason": "不足两年年报，跳过连环替代"}

        # 同比列
        for i, row in enumerate(rows_out):
            if i + 1 >= len(rows_out):
                break
            prev = rows_out[i + 1]
            if row.get("roe") is None or prev.get("roe") is None:
                continue
            row["roe_yoy_pp"] = round(row["roe"] - prev["roe"], 4)
            if has_ma:
                row["yoy_note"] = "可比口径可能被破坏（has_ma=true）"

        failed = any(r.get("validation") == "failed" for r in rows_out)
        five_failed = any(
            (r.get("five_factor") or {}).get("validation") == "failed" for r in rows_out
        )

        return ok(
            {
                "code": code,
                "is_financial": is_fin,
                "rows": rows_out,
                "attribution": attribution,
                "validation": "failed" if (failed or five_failed) else "ok",
            },
            market="a_share",
            source="eastmoney+calc",
            tool="calc_dupont",
        )


# ---------------------------------------------------------------------------
# Red flags
# ---------------------------------------------------------------------------


def _yoy_growth(curr: float | None, prev: float | None) -> float | None:
    if curr is None or prev is None or prev == 0:
        return None
    return (curr - prev) / abs(prev)


def _ni_near_zero(ni: float | None, rev: float | None) -> bool:
    if ni is None:
        return False
    if ni <= 0:
        return True
    if rev is None or rev == 0:
        return abs(ni) < 1.0  # 无营收时无法用相对口径
    return abs(ni) / abs(rev) < _NEAR_ZERO_NI_RATIO


def _flag_result(
    num: int,
    name: str,
    status: str,
    detail: str,
    note: str = "",
) -> dict[str, Any]:
    return {"id": num, "name": name, "status": status, "detail": detail, "note": note}


class CheckRedFlagsTool(BaseTool):
    name = "check_red_flags"
    summary = "财务红旗自动扫描（首批6条）"
    description = (
        "对 A 股代码扫描实务红旗：盈利质量、应收暴增、扣非占比、商誉、存货、存贷双高。"
        "自取 indicators，缺字段自动补拉 balance/cashflow。只输出触发列表与数值，无买卖建议。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "A股代码，如 600519.SH"},
        },
        "required": ["code"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        code = str(args.get("code") or "").strip()
        if not code:
            return err("需要 code")

        ind = _fetch_statement(code, "indicators", "annual")
        if not ind.get("ok"):
            return err(ind.get("error") or f"无法获取 {code} indicators")
        norms_ind = _annual_norms(ind)
        if mapping_failed(norms_ind, ("net_income", "revenue")):
            return err(
                "字段映射失败，需更新映射表",
                note="normalized 缺少 net_income/revenue",
            )

        bal = _fetch_statement(code, "balance", "annual")
        cf = _fetch_statement(code, "cashflow", "annual")
        norms_bal = _annual_norms(bal) if bal.get("ok") else []
        norms_cf = _annual_norms(cf) if cf.get("ok") else []
        merged = _merge_by_date(norms_ind, norms_bal, norms_cf)

        is_fin = _detect_financial(code)
        flags: list[dict[str, Any]] = []

        # --- #1 盈利质量 CFO/NI ---
        flags.append(self._flag_earnings_quality(merged))

        # --- #2 应收暴增 ---
        if is_fin:
            flags.append(_flag_result(2, "应收暴增", "金融股不适用", "", "银行等不适用"))
        else:
            flags.append(self._flag_receivables(merged))

        # --- #3 扣非占比 ---
        flags.append(self._flag_recurring(merged))

        # --- #4 商誉 ---
        flags.append(self._flag_goodwill(merged))

        # --- #5 存货 ---
        if is_fin:
            flags.append(_flag_result(5, "存货异常", "金融股不适用", "", "银行等不适用"))
        else:
            flags.append(self._flag_inventory(merged))

        # --- #6 存贷双高 ---
        if is_fin:
            flags.append(_flag_result(6, "存贷双高", "金融股不适用", "", "银行等不适用"))
        else:
            flags.append(self._flag_cash_debt(merged))

        detected = [f for f in flags if f["status"] in ("触发", "正常")]
        triggered = [f for f in flags if f["status"] == "触发"]
        undetectable = [f for f in flags if f["status"] == "无法检测"]
        skipped = [f for f in flags if f["status"] == "金融股不适用"]

        return ok(
            {
                "code": code,
                "is_financial": is_fin,
                "summary": {
                    "detected": len(detected),
                    "triggered": len(triggered),
                    "undetectable": len(undetectable),
                    "skipped_financial": len(skipped),
                },
                "flags": flags,
                "thresholds_note": "经验阈值，可配置；非 Beneish/Piotroski",
            },
            market="a_share",
            source="eastmoney+calc",
            tool="check_red_flags",
        )

    def _flag_earnings_quality(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        # 连续 2 期+ 满足条件才触发
        hits: list[str] = []
        details: list[str] = []
        checked = 0
        for n in rows[:5]:
            cfo = to_float(n.get("cfo"))
            ni = to_float(n.get("net_income"))
            rev = to_float(n.get("revenue"))
            rd = n.get("report_date")
            if cfo is None or ni is None:
                continue
            checked += 1
            if _ni_near_zero(ni, rev):
                bad = cfo < 0
                details.append(f"{rd}: NI≈0/负, CFO={cfo:.4g} → {'触发期' if bad else '正常期'}")
                if bad:
                    hits.append(str(rd))
            else:
                ratio = cfo / ni
                bad = ratio < 0.5
                details.append(f"{rd}: CFO/NI={ratio:.3f}")
                if bad:
                    hits.append(str(rd))
        if checked < 2:
            return _flag_result(
                1, "盈利质量差", "无法检测", "; ".join(details) or "CFO/NI 不足两期",
            )
        # 连续：从最近期往前数连续 hit
        streak = 0
        for n in rows[:5]:
            rd = str(n.get("report_date"))
            cfo = to_float(n.get("cfo"))
            ni = to_float(n.get("net_income"))
            rev = to_float(n.get("revenue"))
            if cfo is None or ni is None:
                break
            if _ni_near_zero(ni, rev):
                bad = cfo < 0
            else:
                bad = (cfo / ni) < 0.5
            if bad:
                streak += 1
            else:
                break
        if streak >= 2:
            return _flag_result(
                1, "盈利质量差", "触发",
                f"连续{streak}期; " + "; ".join(details[:3]),
            )
        return _flag_result(1, "盈利质量差", "正常", "; ".join(details[:3]))

    def _flag_receivables(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        if len(rows) < 2:
            return _flag_result(2, "应收暴增", "无法检测", "不足两期年报")
        curr, prev = rows[0], rows[1]
        ar0, ar1 = to_float(curr.get("accounts_receivable")), to_float(prev.get("accounts_receivable"))
        rev0, rev1 = to_float(curr.get("revenue")), to_float(prev.get("revenue"))
        if None in (ar0, ar1, rev0, rev1) or ar1 == 0 or rev1 == 0:
            return _flag_result(2, "应收暴增", "无法检测", "应收或营收基期为0/缺失")
        g_ar = _yoy_growth(ar0, ar1)
        g_rev = _yoy_growth(rev0, rev1)
        assert g_ar is not None and g_rev is not None
        # 营收负增长时：若应收仍大增则用绝对值比较
        thresh = g_rev * 1.5
        triggered = g_ar > thresh and g_ar > 0
        detail = f"应收增速={g_ar*100:.1f}%, 营收增速={g_rev*100:.1f}%, 阈值=营收×1.5"
        return _flag_result(2, "应收暴增", "触发" if triggered else "正常", detail)

    def _flag_recurring(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return _flag_result(3, "扣非占比低", "无法检测", "无数据")
        n = rows[0]
        rec = to_float(n.get("recurring_net_income"))
        ni = to_float(n.get("net_income"))
        if rec is None or ni is None:
            return _flag_result(3, "扣非占比低", "无法检测", "缺扣非或归母净利")
        if ni <= 0:
            return _flag_result(3, "扣非占比低", "无法检测", "归母净利≤0，比率失真")
        ratio = rec / ni
        triggered = ratio < 0.7
        return _flag_result(
            3, "扣非占比低", "触发" if triggered else "正常",
            f"扣非/归母={ratio*100:.1f}%",
            "依赖非经常性损益" if triggered else "",
        )

    def _flag_goodwill(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return _flag_result(4, "商誉炸弹", "无法检测", "无数据")
        n = rows[0]
        gw = to_float(n.get("goodwill"))
        eq = to_float(n.get("total_equity"))
        if gw is None:
            return _flag_result(4, "商誉炸弹", "无法检测", "缺商誉字段（balance 可能不可用）")
        if eq is None or eq <= 0:
            return _flag_result(4, "商誉炸弹", "无法检测", "净资产≤0")
        ratio = gw / eq
        return _flag_result(
            4, "商誉炸弹", "触发" if ratio > 0.3 else "正常",
            f"商誉/净资产={ratio*100:.1f}%",
        )

    def _flag_inventory(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        if len(rows) < 2:
            return _flag_result(5, "存货异常", "无法检测", "不足两期年报")
        curr, prev = rows[0], rows[1]
        inv0, inv1 = to_float(curr.get("inventory")), to_float(prev.get("inventory"))
        rev0, rev1 = to_float(curr.get("revenue")), to_float(prev.get("revenue"))
        if None in (inv0, inv1, rev0, rev1) or rev0 == 0 or rev1 == 0:
            return _flag_result(5, "存货异常", "无法检测", "存货或营收缺失/为0")
        r0, r1 = inv0 / rev0, inv1 / rev1
        if r1 == 0:
            return _flag_result(5, "存货异常", "无法检测", "上期存货/营收为0")
        change = (r0 - r1) / abs(r1)
        return _flag_result(
            5, "存货异常", "触发" if change > 0.5 else "正常",
            f"存货/营收同比年报变化={change*100:.1f}% (本期{r0*100:.1f}% vs 上期{r1*100:.1f}%)",
        )

    def _flag_cash_debt(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return _flag_result(6, "存贷双高", "无法检测", "无数据")
        n = rows[0]
        cash = to_float(n.get("cash"))
        debt = to_float(n.get("interest_debt"))
        rev = to_float(n.get("revenue"))
        if cash is None or debt is None:
            return _flag_result(
                6, "存贷双高", "无法检测",
                "缺货币资金或有息负债（balance 可能不可用）",
            )
        if rev is None or rev <= 0:
            return _flag_result(6, "存贷双高", "无法检测", "营收≤0")
        c_ratio, d_ratio = cash / rev, debt / rev
        triggered = c_ratio > 0.3 and d_ratio > 0.3
        return _flag_result(
            6, "存贷双高", "触发" if triggered else "正常",
            f"货币/营收={c_ratio*100:.1f}%, 有息负债/营收={d_ratio*100:.1f}%",
        )
