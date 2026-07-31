"""Versioned A-share trading rules registry (single source of truth)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class RuleSet:
    version: str
    effective_from: str
    t_plus: int
    lot_size: int
    stamp_tax_sell: float
    commission_default: float
    transfer_fee: float
    board_limits: dict[str, float]


CURRENT_RULES = RuleSet(
    version="CN-A-2026-07",
    effective_from="2026-07-06",
    t_plus=1,
    lot_size=100,
    stamp_tax_sell=0.0005,
    commission_default=0.0003,
    transfer_fee=0.00001,
    board_limits={
        "MAIN": 0.10,
        "STAR": 0.20,
        "CHINEXT": 0.20,
        "BSE": 0.30,
        "ST_MAIN": 0.10,
    },
)


def current_rules(as_of: str | None = None) -> RuleSet:
    """Return the rule set for a supported date without fabricating history."""
    requested = date.fromisoformat(as_of) if as_of else date.today()
    first_supported = date.fromisoformat(CURRENT_RULES.effective_from)
    if requested < first_supported:
        raise ValueError(
            f"未配置 {requested.isoformat()} 的历史交易规则；最早支持 {CURRENT_RULES.effective_from}"
        )
    return CURRENT_RULES
