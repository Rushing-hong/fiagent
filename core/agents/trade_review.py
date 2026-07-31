"""Trade journal review workflow with deterministic attribution."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from openai import OpenAI

from core.agents.profile import load_profile
from core.agents.runner import AgentRunner, AgentTask
from core.context import AgentContext
from core.loop import run_agent_turn
from hooks.registry import HookRegistry
from research.attribution import analyze_journal_with_attribution
from research.evidence_store import EvidenceStore
from research.lineage import enrich_attribution_with_lineage
from research.memory import persist_validated_lessons
from ui import ui

_JOURNAL_PATH_RE = re.compile(
    r"(?:[\w./\\-]+[/\\])?[\w.-]+\.(?:csv|xlsx|xls)",
    re.I,
)


def extract_journal_path(text: str) -> str | None:
    m = _JOURNAL_PATH_RE.search(text or "")
    return m.group(0) if m else None


class TradeReviewOrchestrator:
    """Single-agent trade review + pre-computed attribution + optional research linkage."""

    def __init__(
        self,
        root: Path,
        client: OpenAI,
        hooks: HookRegistry,
        store: EvidenceStore | None = None,
    ) -> None:
        self.root = root
        self.client = client
        self.hooks = hooks
        self.evidence = store or EvidenceStore()
        self.runner = AgentRunner(root, client, hooks, store=self.evidence)

    def run(
        self,
        query: str,
        messages: list[dict[str, Any]],
        ctx: AgentContext,
    ) -> str:
        run = self.evidence.start_run(query, "trade_review")
        ui.info(f"交易复盘启动 (run={run.id})")

        journal_path = extract_journal_path(query)
        preface_blocks: list[str] = []
        attribution_payload: dict[str, Any] | None = None

        if journal_path:
            ui.info(f"解析交易日记: {journal_path}")
            try:
                full = analyze_journal_with_attribution(journal_path, ctx=ctx)
                if full.get("status") == "ok":
                    attribution_payload = full.get("attribution")
                    if attribution_payload:
                        attribution_payload = enrich_attribution_with_lineage(
                            self.evidence, attribution_payload,
                        )
                    self.evidence.save_report(
                        run.id,
                        "attribution_engine",
                        json.dumps(full.get("attribution"), ensure_ascii=False, indent=2),
                        structured=full.get("attribution"),
                    )
                    preface_blocks.append(
                        "### 预计算归因（确定性）\n```json\n"
                        + json.dumps(full.get("attribution"), ensure_ascii=False, indent=2)
                        + "\n```"
                    )
                    syms = {
                        s.get("symbol")
                        for s in (attribution_payload or {}).get("sample_roundtrips", [])
                        if s.get("symbol")
                    }
                    for sym in list(syms)[:5]:
                        linked = self.evidence.find_lineage_for_symbol(sym, limit=3)
                        if not linked:
                            linked = self.evidence.find_recent_runs_by_symbol(sym, limit=3)
                        if linked:
                            preface_blocks.append(
                                f"### 决策血缘 / 历史研究（{sym}）\n"
                                + json.dumps(linked, ensure_ascii=False, indent=2)
                            )
                else:
                    preface_blocks.append(f"日记解析失败: {full.get('error', full)}")
            except Exception as exc:
                preface_blocks.append(f"日记解析异常: {exc}")
        else:
            preface_blocks.append(
                "未检测到 .csv/.xlsx 路径。请在问题中提供文件路径，"
                "或让 Agent 先向你确认路径后再调用 analyze_trade_journal。"
            )

        profile = load_profile("trade_review", self.root)
        task = AgentTask(
            instruction=query,
            context_blocks=preface_blocks,
        )
        result = self.runner.run(profile, task)
        agent_text = result.content if result.success else f"失败: {result.error}"
        self.evidence.save_report(run.id, "trade_review", agent_text)

        if attribution_payload:
            self.evidence.save_trade_attribution(
                run.id,
                journal_path or "",
                attribution_payload,
            )
            syms = {
                s.get("symbol")
                for s in attribution_payload.get("sample_roundtrips", [])
                if s.get("symbol")
            }
            for sym in list(syms)[:3]:
                saved = persist_validated_lessons(
                    self.evidence, run.id, attribution_payload, symbol=str(sym),
                )
                if saved:
                    ui.info(f"已写入 {len(saved)} 条经校验经验 ({sym})")

        brief = "\n\n".join([
            f"## 用户请求\n{query}",
            f"## Trade Review Run ID\n{run.id}",
            *preface_blocks,
            "## Trade Review Agent 报告",
            agent_text,
            "## 你的任务",
            "作为复盘协调者，整合以上归因矩阵与 Agent 报告，"
            "输出最终复盘结论：行为偏差、可学习经验、需丢弃的伪经验、下一步行动。",
        ])
        messages.append({"role": "user", "content": brief})
        ui.info("[复盘] 生成最终报告…")
        run_agent_turn(self.client, messages, ctx, self.hooks)

        self.evidence.finish_run(run.id, "completed")
        for m in reversed(messages):
            if m.get("role") == "assistant" and (m.get("content") or "").strip():
                final = str(m["content"]).strip()
                self.evidence.save_report(run.id, "trade_review_final", final)
                return final
        return agent_text
