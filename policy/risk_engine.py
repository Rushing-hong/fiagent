"""Portfolio risk limits — deterministic gate for Committee mode."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class PositionProposal:
    symbol: str
    target_weight: float
    sector: str = ""


@dataclass
class RiskLimits:
    max_single_name: float = 0.10
    max_sector: float = 0.30
    max_gross_exposure: float = 1.0
    min_cash_buffer: float = 0.05


@dataclass
class RiskDecision:
    approved: bool
    adjusted_weight: float | None
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    risk_budget_multiplier: float = 1.0

    def to_dict(self) -> dict:
        return {
            "approved": self.approved,
            "adjusted_weight": self.adjusted_weight,
            "violations": self.violations,
            "warnings": self.warnings,
            "risk_budget_multiplier": self.risk_budget_multiplier,
        }


class RiskEngine:
    """Deterministic position risk check. LLM cannot override `approved=False`."""

    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()

    def evaluate(
        self,
        proposal: PositionProposal,
        *,
        existing_weights: dict[str, float] | None = None,
        sector_weights: dict[str, float] | None = None,
        market_risk_multiplier: float = 1.0,
    ) -> RiskDecision:
        existing = dict(existing_weights or {})
        sectors = dict(sector_weights or {})
        violations: list[str] = []
        warnings: list[str] = []
        try:
            requested = float(proposal.target_weight)
        except (TypeError, ValueError):
            requested = float("nan")
        if not math.isfinite(requested) or not 0.0 <= requested <= 1.0:
            violations.append(f"目标仓位无效: {proposal.target_weight!r}")
        w = max(0.0, min(1.0, requested)) if math.isfinite(requested) else 0.0
        try:
            multiplier = float(market_risk_multiplier)
        except (TypeError, ValueError):
            multiplier = 0.0
        if not math.isfinite(multiplier) or not 0.0 < multiplier <= 1.0:
            violations.append(f"风险预算系数无效: {market_risk_multiplier!r}")
            multiplier = min(1.0, max(0.0, multiplier)) if math.isfinite(multiplier) else 0.0

        cap = self.limits.max_single_name * multiplier
        if w > cap:
            violations.append(
                f"单票仓位 {w:.1%} 超过上限 {cap:.1%} (risk_multiplier={market_risk_multiplier})"
            )
            w = cap

        sec = proposal.sector.strip()
        if sec:
            sector_total = sectors.get(sec, 0.0) + w - existing.get(proposal.symbol, 0.0)
            sec_cap = self.limits.max_sector * multiplier
            if sector_total > sec_cap:
                violations.append(
                    f"行业 {sec} 暴露 {sector_total:.1%} 超过上限 {sec_cap:.1%}"
                )

        gross = sum(existing.values()) + w - existing.get(proposal.symbol, 0.0)
        max_gross = min(self.limits.max_gross_exposure, 1.0 - self.limits.min_cash_buffer)
        if gross > max_gross:
            violations.append(
                f"总敞口 {gross:.1%} 超过上限 {max_gross:.1%}（含最低现金缓冲）"
            )

        if w > 0 and (1.0 - gross) < self.limits.min_cash_buffer:
            violations.append(
                f"现金缓冲不足（剩余约 {1.0 - gross:.1%}，要求 ≥ {self.limits.min_cash_buffer:.1%}）"
            )

        approved = len(violations) == 0
        return RiskDecision(
            approved=approved,
            adjusted_weight=w if violations else proposal.target_weight,
            violations=violations,
            warnings=warnings,
            risk_budget_multiplier=multiplier,
        )

    def evaluate_batch(
        self,
        proposals: list[PositionProposal],
        **kwargs,
    ) -> list[RiskDecision]:
        existing = dict(kwargs.pop("existing_weights", None) or {})
        sectors = dict(kwargs.pop("sector_weights", None) or {})
        decisions: list[RiskDecision] = []
        for proposal in proposals:
            decision = self.evaluate(
                proposal,
                existing_weights=existing,
                sector_weights=sectors,
                **kwargs,
            )
            decisions.append(decision)
            if not decision.approved:
                continue
            old_weight = float(existing.get(proposal.symbol, 0.0) or 0.0)
            accepted_weight = float(decision.adjusted_weight or 0.0)
            existing[proposal.symbol] = accepted_weight
            sector = proposal.sector.strip()
            if sector:
                sectors[sector] = float(sectors.get(sector, 0.0) or 0.0) + accepted_weight - old_weight
        return decisions
