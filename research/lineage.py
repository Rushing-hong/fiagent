"""Decision lineage: research → policy → execution sim → trade attribution."""

from __future__ import annotations

from typing import Any

from research.evidence_store import EvidenceStore


def record_proposal_lineage(
    store: EvidenceStore,
    run_id: str,
    symbol: str,
    target_weight: float,
    *,
    parent_id: str | None = None,
) -> str:
    return store.save_lineage_step(
        run_id,
        symbol,
        "proposal",
        {"target_weight": target_weight},
        parent_id=parent_id,
    )


def record_execution_lineage(
    store: EvidenceStore,
    run_id: str,
    symbol: str,
    fill_report: dict[str, Any],
    *,
    parent_id: str | None = None,
) -> str:
    return store.save_lineage_step(
        run_id,
        symbol,
        "execution_sim",
        fill_report,
        parent_id=parent_id,
    )


def enrich_attribution_with_lineage(
    store: EvidenceStore,
    attribution: dict[str, Any],
) -> dict[str, Any]:
    """Attach prior research/execution lineage to roundtrip samples."""
    samples = list(attribution.get("sample_roundtrips") or [])
    enriched: list[dict[str, Any]] = []
    for s in samples:
        sym = s.get("symbol")
        if not sym:
            enriched.append(s)
            continue
        chain = store.find_lineage_for_symbol(str(sym), limit=3)
        row = dict(s)
        row["decision_lineage"] = chain
        if chain:
            latest_exec = next(
                (c for c in chain if c.get("step") == "execution_sim"),
                None,
            )
            if latest_exec:
                payload = latest_exec.get("payload") or {}
                status = payload.get("status")
                if status in ("rejected", "partial") and row.get("attribution") != "correct_profit":
                    row["attribution_hint"] = "execution_not_filled"
        enriched.append(row)
    out = dict(attribution)
    out["sample_roundtrips"] = enriched
    return out


def build_lineage_summary(store: EvidenceStore, run_id: str) -> list[dict[str, Any]]:
    return store.get_lineage_chain(run_id)
