"""Extract symbols and position hints from research text / user query."""

from __future__ import annotations

import re

from policy.risk_engine import PositionProposal

_SYMBOL_RE = re.compile(
    r"\b(\d{6})(?:\.(?:SH|SZ|BJ))?\b",
    re.I,
)
_WEIGHT_RE = re.compile(
    r"(\d{1,2}(?:\.\d+)?)\s*%|仓位\s*(\d{1,2}(?:\.\d+)?)",
)


def extract_symbols(*texts: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for m in _SYMBOL_RE.finditer(text or ""):
            code = m.group(1)
            if code.startswith(("6", "5")):
                sym = f"{code}.SH"
            elif code.startswith(("4", "8")):
                sym = f"{code}.BJ"
            else:
                sym = f"{code}.SZ"
            if sym not in seen:
                seen.add(sym)
                found.append(sym)
    return found


def infer_target_weight(query: str, default: float = 0.05) -> float:
    q = query or ""
    for m in _WEIGHT_RE.finditer(q):
        raw = m.group(1) or m.group(2)
        if raw:
            v = float(raw)
            return v / 100.0 if v > 1 else v
    if "满仓" in q:
        return 0.95
    if "重仓" in q:
        return 0.15
    if "轻仓" in q:
        return 0.03
    return default


def build_proposals(query: str, *report_texts: str) -> list[PositionProposal]:
    symbols = extract_symbols(query, *report_texts)
    if not symbols:
        return []
    w = infer_target_weight(query)
    return [PositionProposal(symbol=s, target_weight=w) for s in symbols[:3]]
