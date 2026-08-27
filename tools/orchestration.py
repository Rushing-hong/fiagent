"""CIO orchestration tools — read reports/evidence, no raw market data."""

from __future__ import annotations

import json

from research.evidence_store import EvidenceStore
from research.run_context import get_run_context
from tools.base import BaseTool


def _resolve_run_id(args: dict) -> str | None:
    run_id = str(args.get("run_id") or "").strip()
    if run_id:
        return run_id
    rc = get_run_context()
    return rc.run_id if rc else None


def _get_store(run_id: str) -> EvidenceStore:
    rc = get_run_context()
    if rc and rc.run_id == run_id:
        return rc.store
    return EvidenceStore()


class ReadAgentReportTool(BaseTool):
    name = "read_agent_report"
    summary = "读取本 run 的专家 Agent 报告"
    description = "从证据库读取指定 agent 的结构化/文本报告。CIO 综合阶段使用，禁止绕过专家直接取数。"
    parameters = {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "research run id，默认同上下文"},
            "agent_name": {
                "type": "string",
                "description": "如 data_guardian, market_regime, company_research, quant_research, red_team",
            },
        },
        "required": ["agent_name"],
    }
    is_readonly = True
    repeatable = True

    def execute(self, args: dict, ctx) -> str:
        run_id = _resolve_run_id(args)
        if not run_id:
            return json.dumps({"status": "error", "error": "缺少 run_id"}, ensure_ascii=False)
        store = _get_store(run_id)
        reports = store.list_reports(run_id)
        agent = str(args.get("agent_name", ""))
        for r in reports:
            if r["agent_name"] == agent:
                return json.dumps({
                    "status": "ok",
                    "run_id": run_id,
                    "agent_name": agent,
                    "content": r["content"],
                    "structured": r.get("structured"),
                }, ensure_ascii=False)
        return json.dumps({
            "status": "error",
            "error": f"未找到 agent={agent} 的报告",
        }, ensure_ascii=False)


class ListRunEvidenceTool(BaseTool):
    name = "list_run_evidence"
    summary = "列出本 run 的证据与血缘"
    description = "返回 evidence、policy decisions、decision lineage 摘要。"
    parameters = {
        "type": "object",
        "properties": {
            "run_id": {"type": "string"},
            "symbol": {"type": "string"},
        },
    }
    is_readonly = True
    repeatable = True

    def execute(self, args: dict, ctx) -> str:
        run_id = _resolve_run_id(args)
        if not run_id:
            return json.dumps({"status": "error", "error": "缺少 run_id"}, ensure_ascii=False)
        store = _get_store(run_id)
        sym = args.get("symbol")
        return json.dumps({
            "status": "ok",
            "run_id": run_id,
            "evidence": [
                {
                    "evidence_id": e.evidence_id,
                    "symbol": e.symbol,
                    "pit_safe": e.pit_safe,
                    "quality": e.quality,
                }
                for e in store.list_evidence(run_id)
            ],
            "policy": store.list_policy_decisions(run_id),
            "lineage": store.get_lineage_chain(run_id, sym) if sym else store.get_lineage_chain(run_id),
            "claims": store.list_claims(run_id),
        }, ensure_ascii=False)


class ListValidatedLessonsTool(BaseTool):
    name = "list_validated_lessons"
    summary = "读取经复盘验证的长期经验"
    description = "仅包含通过 Trade Review 归因验证的经验，非 LLM 自由记忆。"
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "limit": {"type": "integer", "default": 10},
        },
    }
    is_readonly = True
    repeatable = True

    def execute(self, args: dict, ctx) -> str:
        store = EvidenceStore()
        lessons = store.list_validated_lessons(
            symbol=args.get("symbol") or None,
            limit=int(args.get("limit") or 10),
        )
        return json.dumps({"status": "ok", "lessons": lessons}, ensure_ascii=False)
