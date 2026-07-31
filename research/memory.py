"""Validated long-term memory — only lessons passing attribution review."""

from __future__ import annotations

from typing import Any

from research.evidence_store import EvidenceStore


def extract_validated_lessons(attribution: dict[str, Any]) -> list[str]:
    """Pick lessons safe to persist (exclude 'do not learn' warnings)."""
    lessons = list(attribution.get("learnable_lessons") or [])
    flags = attribution.get("behavior_flags") or {}
    buckets = attribution.get("attribution_buckets") or {}

    if buckets.get("correct_profit", 0) > 0:
        lessons.append("盈利回合中逻辑与执行一致性较好，可强化同类入场纪律")

    validated: list[str] = []
    for lesson in lessons:
        text = str(lesson).strip()
        if not text or "不宜写入" in text or "不应学习" in text:
            continue
        validated.append(text)
    return validated


def persist_validated_lessons(
    store: EvidenceStore,
    run_id: str,
    attribution: dict[str, Any],
    *,
    symbol: str = "",
) -> list[str]:
    saved: list[str] = []
    for lesson in extract_validated_lessons(attribution):
        store.save_validated_lesson(
            lesson,
            source_run_id=run_id,
            symbol=symbol,
            payload={"attribution_flags": attribution.get("behavior_flags")},
        )
        saved.append(lesson)
    return saved
