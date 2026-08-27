"""Structured research card schemas (validation targets for agent outputs)."""

from __future__ import annotations

from typing import Any

MARKET_REGIME_CARD = {
    "required": ["market_regime", "risk_budget_multiplier"],
    "optional": [
        "regime_probabilities", "crowding", "style_bias", "evidence_ids", "evidence_refs",
    ],
}

COMPANY_RESEARCH_CARD = {
    "required": ["symbol"],
    "score_fields": [
        "fundamental_score", "earnings_quality_score",
        "valuation_score", "governance_risk_score",
    ],
    "optional": [
        "horizon", "base_value", "bull_value", "bear_value",
        "catalysts", "risks", "invalidation_conditions", "evidence_ids", "evidence_refs",
    ],
}

QUANT_RESEARCH_CARD = {
    "required": ["backtest_grade", "live_readiness"],
    "enums": {
        "backtest_grade": {"A", "B", "C", "D"},
        "execution_realism": {"high", "medium", "low"},
        "pit_integrity": {"full", "partial", "unknown"},
        "capacity_confidence": {"high", "medium", "low"},
    },
    "optional": [
        "symbol", "horizon", "momentum_score", "factor_tilt",
        "evidence_ids", "evidence_refs",
    ],
}

DATA_GUARDIAN_EVIDENCE = {
    "required": ["symbol", "pit_safe"],
    "optional": ["source", "as_of_time", "quality", "fields", "evidence_id"],
}

CIO_CLAIM = {
    "required": ["stance", "confidence"],
    "optional": [
        "symbols", "target_weights", "summary", "invalidation_conditions",
        "evidence_refs",
    ],
}
