"""Parse and validate agent structured outputs + PIT evidence."""

from __future__ import annotations

import json
import re
from typing import Any

from research.schemas import (
    COMPANY_RESEARCH_CARD,
    DATA_GUARDIAN_EVIDENCE,
    MARKET_REGIME_CARD,
    QUANT_RESEARCH_CARD,
    CIO_CLAIM,
)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)
_QUANT_BACKTEST_TOOLS = frozenset({"run_backtest", "run_python"})


def extract_json_objects(text: str) -> list[Any]:
    out: list[Any] = []
    for m in _JSON_BLOCK_RE.finditer(text or ""):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def _find_card(objects: list[Any], keys: set[str]) -> dict[str, Any] | None:
    for obj in objects:
        if isinstance(obj, dict) and keys.issubset(obj.keys()):
            return obj
    for obj in objects:
        if isinstance(obj, dict):
            return obj
    return None


def _missing_required(data: dict, required: list[str]) -> list[str]:
    return [k for k in required if k not in data or data[k] in (None, "")]


def validate_market_card(data: dict[str, Any]) -> tuple[bool, list[str]]:
    errors = _missing_required(data, MARKET_REGIME_CARD["required"])
    mult = data.get("risk_budget_multiplier")
    if mult is not None:
        try:
            v = float(mult)
            if not 0.0 <= v <= 1.0:
                errors.append("risk_budget_multiplier 须在 0-1")
        except (TypeError, ValueError):
            errors.append("risk_budget_multiplier 须为数字")
    return len(errors) == 0, errors


def validate_company_card(data: dict[str, Any]) -> tuple[bool, list[str]]:
    errors = _missing_required(data, COMPANY_RESEARCH_CARD["required"])
    for field in COMPANY_RESEARCH_CARD["score_fields"]:
        if field in data and data[field] is not None:
            try:
                v = float(data[field])
                if not 0 <= v <= 100:
                    errors.append(f"{field} 须在 0-100")
            except (TypeError, ValueError):
                errors.append(f"{field} 须为数字")
    return len(errors) == 0, errors


def validate_quant_card(data: dict[str, Any]) -> tuple[bool, list[str]]:
    errors = _missing_required(data, QUANT_RESEARCH_CARD["required"])
    grade = data.get("backtest_grade")
    if grade and grade not in QUANT_RESEARCH_CARD["enums"]["backtest_grade"]:
        errors.append(f"backtest_grade 无效: {grade}")
    for field, allowed in QUANT_RESEARCH_CARD["enums"].items():
        if field == "backtest_grade":
            continue
        val = data.get(field)
        if val and str(val).lower() not in allowed:
            errors.append(f"{field} 须为 {allowed}")
    lr = data.get("live_readiness")
    if lr is not None and not isinstance(lr, bool):
        if str(lr).lower() not in ("true", "false"):
            errors.append("live_readiness 须为 boolean")
    return len(errors) == 0, errors


def validate_quant_tool_evidence(
    data: dict[str, Any] | None,
    tool_calls: list[dict[str, Any]],
) -> list[str]:
    """Prevent an A-C backtest grade when no calculation actually succeeded."""
    if not isinstance(data, dict):
        return []
    grade = str(data.get("backtest_grade") or "").upper()
    successful = {
        str(call.get("tool_name") or "")
        for call in tool_calls
        if call.get("success") is True
    }
    if grade in {"A", "B", "C"} and not successful.intersection(_QUANT_BACKTEST_TOOLS):
        return [
            "backtest_grade A-C 必须有成功的 run_backtest/run_python 计算证据；"
            "否则降为 D 且 live_readiness=false"
        ]
    return []


def validate_evidence_references(
    data: dict[str, Any] | None,
    evidence: list[Any],
) -> list[str]:
    """Reject structured cards that cite evidence absent from the run ledger."""
    if not isinstance(data, dict):
        return []

    referenced: set[str] = set()
    evidence_items = data.get("evidence")
    if isinstance(evidence_items, list):
        for item in evidence_items:
            if not isinstance(item, dict):
                continue
            ref = item.get("evidence_id")
            if isinstance(ref, str) and ref.strip():
                referenced.add(ref.strip())
    for field in ("evidence_ids", "evidence_refs"):
        values = data.get(field)
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, str) and value.strip():
                referenced.add(value.strip())
            elif isinstance(value, dict):
                ref = value.get("evidence_id") or value.get("id")
                if isinstance(ref, str) and ref.strip():
                    referenced.add(ref.strip())

    if not referenced:
        return []

    available: set[str] = set()
    for item in evidence:
        canonical = getattr(item, "evidence_id", None)
        payload = getattr(item, "payload", None)
        if isinstance(item, dict):
            canonical = item.get("evidence_id", canonical)
            payload = item.get("payload", payload)
        if isinstance(canonical, str) and canonical:
            available.add(canonical)
        if isinstance(payload, dict):
            alias = payload.get("evidence_id")
            if isinstance(alias, str) and alias:
                available.add(alias)

    missing = sorted(referenced - available)
    if not missing:
        return []
    preview = ", ".join(missing[:8])
    suffix = " …" if len(missing) > 8 else ""
    return [f"引用了当前 run 未登记的 evidence_id: {preview}{suffix}"]


def validate_cio_claim(data: dict[str, Any]) -> tuple[bool, list[str]]:
    errors = _missing_required(data, CIO_CLAIM["required"])
    conf = data.get("confidence")
    if conf is not None:
        try:
            v = float(conf)
            if not 0.0 <= v <= 1.0:
                errors.append("confidence 须在 0-1")
        except (TypeError, ValueError):
            errors.append("confidence 须为数字")
    return len(errors) == 0, errors


def parse_data_guardian_evidence(content: str) -> tuple[list[dict[str, Any]], bool]:
    """Return evidence items and whether PIT gate allows backtest tools."""
    items: list[dict[str, Any]] = []
    for obj in extract_json_objects(content):
        if isinstance(obj, list):
            for row in obj:
                if isinstance(row, dict) and "symbol" in row:
                    items.append(row)
        elif isinstance(obj, dict) and "symbol" in obj:
            items.append(obj)

    pit_ok = any(bool(it.get("pit_safe")) for it in items)
    if not items and "pit_safe" in (content or "").lower():
        pit_ok = "pit_safe\": true" in content or "pit_safe: true" in content.lower()
    return items, pit_ok


def validate_agent_output(agent_name: str, content: str) -> dict[str, Any]:
    """Parse markdown report → structured card + validation errors."""
    objects = extract_json_objects(content)
    result: dict[str, Any] = {
        "agent": agent_name,
        "valid": False,
        "structured": None,
        "errors": [],
    }

    if agent_name == "market_regime":
        card = _find_card(objects, {"market_regime", "risk_budget_multiplier"})
        if not card:
            result["errors"].append("缺少 JSON 市场状态卡")
            return result
        ok, errs = validate_market_card(card)
        result["structured"] = card
        result["valid"] = ok
        result["errors"] = errs
    elif agent_name == "company_research":
        card = _find_card(objects, {"symbol"})
        if not card:
            result["errors"].append("缺少 JSON 公司研究卡")
            return result
        ok, errs = validate_company_card(card)
        result["structured"] = card
        result["valid"] = ok
        result["errors"] = errs
    elif agent_name == "quant_research":
        card = _find_card(objects, {"backtest_grade"})
        if not card:
            result["errors"].append("缺少 JSON 量化研究卡")
            return result
        ok, errs = validate_quant_card(card)
        result["structured"] = card
        result["valid"] = ok
        result["errors"] = errs
    elif agent_name == "data_guardian":
        items, pit_ok = parse_data_guardian_evidence(content)
        result["structured"] = {"evidence": items, "pit_safe_for_backtest": pit_ok}
        result["valid"] = len(items) > 0 or pit_ok
        if not result["valid"]:
            result["errors"].append("缺少 JSON 证据列表")
        else:
            for it in items:
                miss = _missing_required(it, DATA_GUARDIAN_EVIDENCE["required"])
                if miss:
                    result["errors"].append(f"证据项缺少字段: {miss}")
                    result["valid"] = False
    elif agent_name == "orchestrator":
        card = _find_card(objects, {"stance", "confidence"})
        if card:
            ok, errs = validate_cio_claim(card)
            result["structured"] = card
            result["valid"] = ok
            result["errors"] = errs
        else:
            result["valid"] = True
    else:
        result["valid"] = True

    return result
