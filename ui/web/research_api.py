"""Research / eval API payloads for Web UI."""

from __future__ import annotations

from evals.dashboard import build_dashboard
from research.evidence_store import EvidenceStore

_AGENT_LABELS = {
    "data_guardian": "Data Guardian",
    "market_regime": "Market Regime",
    "company_research": "Company Research",
    "quant_research": "Quant Research",
    "red_team": "Red-Team",
    "orchestrator": "CIO",
    "trade_review": "Trade Review",
    "attribution_engine": "归因引擎",
}


def agent_label(name: str) -> str:
    return _AGENT_LABELS.get(name, name)


def list_runs_payload(limit: int = 30) -> dict:
    store = EvidenceStore()
    try:
        return {"status": "ok", "runs": store.list_recent_runs(limit=limit)}
    finally:
        store.close()


def run_detail_payload(run_id: str) -> dict:
    store = EvidenceStore()
    try:
        detail = store.get_run_detail(run_id)
        if not detail:
            return {"status": "error", "error": f"run not found: {run_id}"}
        detail["run_status"] = detail.get("status", "unknown")
        detail["status"] = "ok"
        detail["agent_labels"] = _AGENT_LABELS
        return detail
    finally:
        store.close()


def evals_payload() -> dict:
    return build_dashboard()


def lessons_payload(symbol: str | None = None, limit: int = 20) -> dict:
    store = EvidenceStore()
    try:
        return {
            "status": "ok",
            "lessons": store.list_validated_lessons(symbol=symbol, limit=limit),
        }
    finally:
        store.close()
