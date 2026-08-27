"""Trading compliance rules — versioned, deterministic, hard veto."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from policy.rules_registry import current_rules


@dataclass(frozen=True)
class TradingRules:
    board: str
    security_status: str
    price_limit: float
    t_plus: int
    lot_size: int
    stamp_tax_sell: float
    commission_estimate: float
    transfer_fee: float
    rule_version: str
    effective_from: str
    status_verified: bool
    action_allowed: bool
    veto_reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "board": self.board,
            "security_status": self.security_status,
            "price_limit": self.price_limit,
            "t_plus": self.t_plus,
            "lot_size": self.lot_size,
            "stamp_tax_sell": self.stamp_tax_sell,
            "commission_estimate": self.commission_estimate,
            "transfer_fee": self.transfer_fee,
            "rule_version": self.rule_version,
            "effective_from": self.effective_from,
            "status_verified": self.status_verified,
            "action_allowed": self.action_allowed,
            "veto_reasons": list(self.veto_reasons),
        }


def _normalize_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if re.fullmatch(r"\d{6}", s):
        if s.startswith(("6", "5")):
            return f"{s}.SH"
        if s.startswith(("4", "8")):
            return f"{s}.BJ"
        return f"{s}.SZ"
    return s


def _infer_board(symbol: str) -> tuple[str, float]:
    code = symbol.split(".")[0]
    if symbol.endswith(".BJ") or code.startswith(("4", "8")):
        return "BSE", 0.30
    if code.startswith("688"):
        return "STAR", 0.20
    if code.startswith("300"):
        return "CHINEXT", 0.20
    return "MAIN", 0.10


def _infer_status(symbol: str, override: str | None = None) -> tuple[str, bool]:
    if override:
        status = override.strip().upper()
        if status not in {"NORMAL", "ST", "SUSPENDED", "DELISTED"}:
            raise ValueError(f"不支持的 security_status={status}")
        return status, True
    # This engine has no live security-status provider.  Never turn an unknown
    # status into NORMAL, especially for executable committee recommendations.
    return "UNVERIFIED", False


class ComplianceEngine:
    """A-share trading rule lookup. Hard veto — not overridable by LLM."""

    @property
    def RULE_VERSION(self) -> str:
        return current_rules().version

    def get_trading_rules(
        self,
        security: str,
        as_of_date: str | None = None,
        action: str = "buy",
        *,
        security_status: str | None = None,
        require_verified_status: bool = True,
    ) -> TradingRules:
        sym = _normalize_symbol(security)
        board, limit = _infer_board(sym)
        rules = current_rules(as_of_date)
        status, status_verified = _infer_status(sym, security_status)
        action_l = (action or "buy").strip().lower()
        vetoes: list[str] = []

        if status == "ST" and board == "MAIN":
            limit = 0.10  # ST 2026-07-06 unified 10%
        if status in ("DELISTED", "SUSPENDED"):
            vetoes.append(f"security_status={status}")
        if require_verified_status and not status_verified:
            vetoes.append("security_status_unverified")
        if action_l not in ("buy", "sell", "hold"):
            vetoes.append(f"unknown_action={action_l}")

        return TradingRules(
            board=board,
            security_status=status,
            price_limit=limit,
            t_plus=1,
            lot_size=100,
            stamp_tax_sell=0.0005,
            commission_estimate=0.0003,
            transfer_fee=0.00001,
            rule_version=rules.version,
            effective_from=rules.effective_from,
            status_verified=status_verified,
            action_allowed=len(vetoes) == 0,
            veto_reasons=tuple(vetoes),
        )

    def check_symbols(self, symbols: list[str], action: str = "buy") -> dict[str, TradingRules]:
        return {s: self.get_trading_rules(s, action=action) for s in symbols}
