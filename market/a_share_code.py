"""A股代码交易所后缀归一化。"""

from __future__ import annotations


def a_share_suffix(code6: str) -> str:
    """六位数字代码 → .SH / .SZ / .BJ。"""
    c = str(code6).strip().split(".")[0]
    # 北交所：4/8 开头，以及新号段 92xxxx（须先于「9→沪」规则）
    if len(c) >= 1 and c[0] in ("4", "8"):
        return ".BJ"
    if len(c) >= 2 and c[:2] == "92":
        return ".BJ"
    if len(c) >= 1 and c[0] in ("5", "6", "9"):
        return ".SH"
    # 0/1/2/3 及 159 等深市 ETF/主板/创业板
    return ".SZ"


def to_a_share_symbol(code: str) -> str:
    """补全或校正 A 股带后缀代码（含纠正 920xxx.SH → .BJ）。"""
    raw = str(code).strip().upper()
    digits = "".join(ch for ch in (raw.rpartition(".")[0] if "." in raw else raw) if ch.isdigit())
    if len(digits) < 6:
        return raw
    bare = digits[:6]
    return bare + a_share_suffix(bare)
