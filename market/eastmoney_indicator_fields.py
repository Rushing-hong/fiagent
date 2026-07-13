"""东财 F10 财务报表字段 → 标准名映射。

Day0 实测票：600519.SH / 000001.SZ / 300750.SZ（indicators 字段一致）。
balance/cashflow 对银行股可能不可用（东财通用三表为空），由调用方处理。
"""

from __future__ import annotations

from typing import Any

from market.envelope import to_float

# 标准名 → 东财原始字段别名（按优先级）。单位：金额为元，roe/比率类见 UNIT_HINTS。
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "revenue": (
        "TOTALOPERATEREVE",
        "TOTAL_OPERATE_INCOME",
        "OPERATE_INCOME_PK",
        "OPERATE_INCOME",
    ),
    "cogs": ("OPERATE_COST",),
    "gross_profit": ("MLR",),
    "net_income": ("PARENTNETPROFIT", "PARENT_NETPROFIT"),
    "recurring_net_income": ("KCFJCXSYJLR", "DEDUCT_PARENT_NETPROFIT"),
    # A 股杜邦五因子常用营业利润近似 EBIT
    "ebit": ("OPERATE_PROFIT_PK", "OPERATE_PROFIT"),
    "pretax_income": ("TOTAL_PROFIT",),
    "cfo": ("NETCASH_OPERATE_PK", "NETCASH_OPERATE", "NETCASH_OPERATENOTE"),
    "total_assets": ("TOTAL_ASSETS_PK", "TOTAL_ASSETS"),
    "total_liabilities": ("LIABILITY", "TOTAL_LIABILITIES"),
    # 优先归母权益；indicators 仅有 TOTAL_EQUITY_PK（含少数股东）
    "total_equity": ("TOTAL_PARENT_EQUITY", "TOTAL_EQUITY_PK", "TOTAL_EQUITY"),
    "accounts_receivable": ("ACCOUNTS_RECE", "NOTE_ACCOUNTS_RECE"),
    "inventory": ("INVENTORY",),
    "goodwill": ("GOODWILL",),
    "cash": ("MONETARYFUNDS",),
    "eps": ("EPSJB", "BASIC_EPS"),
    "roe": ("ROEJQ",),
    "shares_outstanding": ("TOTAL_SHARE", "SHARE_CAPITAL"),
}

# 有息负债构成（balance）；全部缺失/空 → 记 0（非无法检测）
_INTEREST_DEBT_COMPONENTS: tuple[str, ...] = (
    "SHORT_LOAN",
    "LONG_LOAN",
    "BOND_PAYABLE",
    "SHORT_BOND_PAYABLE",
    "LEASE_LIAB",
    "BORROW_FUND",
)

# 零值字段：API 返回 null 视为 0（公司无该项）
_NULL_AS_ZERO: frozenset[str] = frozenset({"goodwill", "inventory", "accounts_receivable"})

UNIT_HINTS: dict[str, str] = {
    "revenue": "元",
    "cogs": "元",
    "gross_profit": "元",
    "net_income": "元",
    "recurring_net_income": "元",
    "ebit": "元",
    "pretax_income": "元",
    "cfo": "元",
    "total_assets": "元",
    "total_liabilities": "元",
    "total_equity": "元",
    "accounts_receivable": "元",
    "inventory": "元",
    "goodwill": "元",
    "cash": "元",
    "interest_debt": "元",
    "eps": "元/股",
    "roe": "%",  # 东财 ROEJQ 已是百分数，如 32.53
    "shares_outstanding": "股",
}

_FINANCIAL_KEYWORDS: tuple[str, ...] = ("银行", "保险", "证券", "多元金融")


def report_date_key(row: dict[str, Any]) -> str | None:
    raw = row.get("REPORT_DATE")
    if raw is None:
        return None
    text = str(raw).strip()
    return text[:10] if len(text) >= 10 else text


def is_annual_report_date(report_date: str | None) -> bool:
    if not report_date:
        return False
    return str(report_date)[:10].endswith("-12-31")


def pick_field(row: dict[str, Any], aliases: tuple[str, ...]) -> tuple[float | None, str | None]:
    """返回 (值, 命中的原始字段名)。"""
    for name in aliases:
        if name not in row:
            continue
        val = to_float(row.get(name))
        if val is not None:
            return val, name
        # 显式 null：对零值字段视为 0
        return None, name
    return None, None


def compute_interest_debt(row: dict[str, Any]) -> float | None:
    """从 balance 行汇总有息负债。无任一成分键 → None；有键全空 → 0。"""
    seen = False
    total = 0.0
    for name in _INTEREST_DEBT_COMPONENTS:
        if name not in row:
            continue
        seen = True
        val = to_float(row.get(name))
        if val is not None:
            total += val
    return total if seen else None


def normalize_row(row: dict[str, Any], *, statement: str = "indicators") -> dict[str, Any]:
    """将单期东财行转为标准字段。缺失为 null；goodwill 等 null→0。"""
    out: dict[str, Any] = {
        "report_date": report_date_key(row),
        "statement": statement,
    }
    sources: dict[str, str] = {}
    for std, aliases in FIELD_ALIASES.items():
        val, src = pick_field(row, aliases)
        if val is None and std in _NULL_AS_ZERO and src is not None:
            val = 0.0
        out[std] = val
        if src:
            sources[std] = src

    debt = compute_interest_debt(row)
    if debt is None and statement == "indicators":
        # indicators 无明细时不填 interest_debt
        out["interest_debt"] = None
    else:
        out["interest_debt"] = debt
        if debt is not None:
            sources["interest_debt"] = "+".join(_INTEREST_DEBT_COMPONENTS)

    out["_sources"] = sources
    return out


def attach_normalized(
    periods: list[dict[str, Any]],
    *,
    statement: str,
) -> list[dict[str, Any]]:
    """在每期 raw 旁挂 normalized（保留全部原始字段）。"""
    wrapped: list[dict[str, Any]] = []
    for row in periods:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item["normalized"] = normalize_row(row, statement=statement)
        wrapped.append(item)
    return wrapped


def mapping_failed(normalized_periods: list[dict[str, Any]], required: tuple[str, ...]) -> bool:
    """必选标准字段在所有期均为空 → 映射失败。"""
    if not normalized_periods:
        return True
    for field in required:
        if any(p.get(field) is not None for p in normalized_periods):
            continue
        return True
    return False


def is_financial_board_name(name: str) -> bool:
    return any(kw in name for kw in _FINANCIAL_KEYWORDS)


def extract_normalized_list(periods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从已 attach 的 periods 抽出 normalized 列表。"""
    out: list[dict[str, Any]] = []
    for row in periods:
        if not isinstance(row, dict):
            continue
        norm = row.get("normalized")
        if isinstance(norm, dict):
            out.append(norm)
        else:
            out.append(normalize_row(row))
    return out
