"""Research / Committee workflow orchestration."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI

from core.agents.extract import build_proposals
from core.agents.profile import AgentProfile, load_profile
from core.agents.router import AgentMode
from core.agents.runner import AgentRunner, AgentResult, AgentTask
from core.agents.task_graph import TaskGraph, TaskNode
from core.agents.team_selector import select_team
from core.context import AgentContext
from core.loop import collect_agent_turn
from hooks.registry import HookRegistry
from policy.compliance_engine import ComplianceEngine
from policy.execution_engine import (
    ExecutionEngine,
    OrderIntent,
    PortfolioSnapshot,
    bar_from_ohlcv_row,
)
from policy.risk_engine import PositionProposal, RiskEngine
from research.evidence_store import EvidenceStore
from research.lineage import record_execution_lineage, record_proposal_lineage
from research.run_context import ResearchRunContext, set_research_run_active, set_run_context
from research.validators import parse_data_guardian_evidence, validate_agent_output
from ui import ui


def _research_max_workers() -> int:
    raw = os.getenv("FIAGENT_RESEARCH_MAX_WORKERS", "").strip()
    if raw:
        return max(1, int(raw))
    parallel = os.getenv("FIAGENT_LLM_MAX_PARALLEL", "2").strip() or "2"
    return max(1, int(parallel))


def _emit_progress(payload: dict) -> None:
    try:
        from ui.web.collaboration_progress import emit_collaboration_progress
        emit_collaboration_progress(payload)
    except Exception:
        pass

_RESEARCHER_PROFILES = (
    "market_regime",
    "company_research",
    "quant_research",
)

_RESEARCHER_KEYS = {
    "market_regime": "market",
    "company_research": "company",
    "quant_research": "quant",
}


def _red_team_enabled() -> bool:
    return os.getenv("FIAGENT_RESEARCH_RED_TEAM", "1").strip() not in ("0", "false", "no")


def _failed_report_keys(reports: dict[str, str]) -> list[str]:
    return [
        key
        for key, value in reports.items()
        if key != "research_done" and (value or "").startswith("失败:")
    ]


class ResearchOrchestrator:
    """Team: Data Guardian → (selected researchers) → Red-Team → [Policy] → CIO."""

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
        self.compliance = ComplianceEngine()
        self.risk = RiskEngine()
        self.execution = ExecutionEngine()

    def run(
        self,
        query: str,
        messages: list[dict[str, Any]],
        ctx: AgentContext,
        *,
        mode: AgentMode = AgentMode.RESEARCH,
    ) -> str:
        run = self.evidence.start_run(query, mode.value)
        team = select_team(
            query,
            mode=mode.value,
            include_red_team=_red_team_enabled() and mode != AgentMode.FAST,
        )
        set_research_run_active(True)
        _emit_progress({
            "phase": "start",
            "run_id": run.id,
            "query": query,
            "mode": mode.value,
            "workflow": team.workflow_id,
            "researchers": team.researchers,
            "include_red_team": team.include_red_team and _red_team_enabled(),
        })

        try:
            return self._run_pipeline(
                run, query, mode, team, messages, ctx,
            )
        except Exception as exc:
            # Never leave interrupted/failed runs looking active forever.
            self.evidence.finish_run(run.id, "failed")
            _emit_progress({
                "phase": "complete",
                "run_id": run.id,
                "mode": mode.value,
                "status": "failed",
                "failed_count": 1,
                "error": str(exc)[:500],
            })
            raise
        finally:
            set_research_run_active(False)

    def _run_pipeline(
        self,
        run,
        query: str,
        mode: AgentMode,
        team,
        messages: list[dict[str, Any]],
        ctx: AgentContext,
    ) -> str:
        profiles: dict[str, AgentProfile] = {
            "data_guardian": load_profile("data_guardian", self.root),
            "red_team": load_profile("red_team", self.root),
            "orchestrator": load_profile("orchestrator", self.root),
        }
        for name in _RESEARCHER_PROFILES:
            profiles[name] = load_profile(name, self.root)

        reports: dict[str, str] = {}
        pit_state: dict[str, Any] = {"ok": False, "items": []}

        def stage(
            agent_name: str,
            profile: AgentProfile,
            task: AgentTask,
            key: str,
            *,
            depends: str | None = None,
            on_ok: Callable[[str, AgentResult], None] | None = None,
        ) -> str:
            deps = [depends] if depends else None
            task_id = self.evidence.start_task(run.id, agent_name, depends_on=deps)
            _emit_progress({
                "phase": "agent_start",
                "run_id": run.id,
                "agent": agent_name,
                "task_id": task_id,
            })
            try:
                result = self.runner.run(
                    profile,
                    task,
                    run_id=run.id,
                    pit_safe_for_backtest=bool(pit_state["ok"]),
                    evidence_items=pit_state["items"],
                )
                content = result.content or ""
                if result.validation_errors:
                    content += "\n\n> 结构化校验: " + "; ".join(result.validation_errors)
                if not result.success and not content.strip():
                    content = f"失败: {result.error or 'Agent 未完成'}"
                self.evidence.save_report(
                    run.id, agent_name, content,
                    task_id=task_id,
                    structured=result.structured,
                )
                if result.success and result.structured:
                    self.evidence.save_claim(
                        run.id, agent_name, "research_card", result.structured,
                    )
                self.evidence.finish_task(task_id, "completed" if result.success else "failed")
                preview = (content or "").strip()
                if len(preview) > 600:
                    preview = preview[:600] + "…"
                _emit_progress({
                    "phase": "agent_done",
                    "run_id": run.id,
                    "agent": agent_name,
                    "status": "completed" if result.success else "failed",
                    "valid": not result.validation_errors,
                    "errors": result.validation_errors[:3],
                    "preview": preview,
                    "tool_rounds": result.tool_rounds,
                })
                if result.success and on_ok:
                    on_ok(content, result)
                downstream = content
                if not result.success:
                    downstream = f"失败: {result.error or 'Agent 未完成'}"
                reports[key] = downstream
                return downstream
            finally:
                self.evidence.close_thread()

        def run_data() -> str:
            def on_data_ok(content: str, result: AgentResult) -> None:
                items, pit_ok = parse_data_guardian_evidence(content)
                pit_state["ok"] = pit_ok
                pit_state["items"] = items
                for it in items:
                    self.evidence.add_evidence(
                        run.id,
                        symbol=str(it.get("symbol", "")),
                        source=str(it.get("source", "data_guardian")),
                        pit_safe=bool(it.get("pit_safe")),
                        quality=str(it.get("quality", "unknown")),
                        fields=it.get("fields"),
                        extra=it,
                    )

            return stage(
                "data_guardian",
                profiles["data_guardian"],
                AgentTask(
                    instruction=(
                        f"为用户问题建立证据快照与数据质量门禁：\n{query}\n\n"
                        "输出须包含：标的识别、as_of_time、数据质量评级、"
                        "PIT 安全性说明。"
                        "文末 ```json``` 必须是证据数组，每项含 symbol, pit_safe, source, quality。"
                    ),
                ),
                "data",
                on_ok=on_data_ok,
            )

        def run_market() -> str:
            return stage(
                "market_regime",
                profiles["market_regime"],
                AgentTask(
                    instruction=f"分析市场状态与风格/拥挤度：\n{query}",
                    context_blocks=[f"### Data Guardian\n{reports.get('data', '')}"],
                ),
                "market",
                depends="data",
            )

        def run_company() -> str:
            return stage(
                "company_research",
                profiles["company_research"],
                AgentTask(
                    instruction=f"完成公司基本面与估值研究：\n{query}",
                    context_blocks=[f"### Data Guardian\n{reports.get('data', '')}"],
                ),
                "company",
                depends="data",
            )

        def run_quant() -> str:
            return stage(
                "quant_research",
                profiles["quant_research"],
                AgentTask(
                    instruction=(
                        f"完成量化验证与回测分级：\n{query}\n"
                        "若 PIT 门未开，不得调用 run_backtest。"
                    ),
                    context_blocks=[f"### Data Guardian\n{reports.get('data', '')}"],
                ),
                "quant",
                depends="data",
            )

        def run_red_team() -> str:
            blocks = []
            for rname in team.researchers:
                key = _RESEARCHER_KEYS[rname]
                if reports.get(key):
                    blocks.append(f"### {rname}\n{reports[key]}")
            research_blob = "\n\n".join(blocks) or "（无研究员报告）"
            return stage(
                "red_team",
                profiles["red_team"],
                AgentTask(
                    instruction=(
                        "识别以下研究材料中的证据缺陷、逻辑漏洞、"
                        "未来函数风险、估值假设问题与最可能导致亏损的情景。"
                        "不要复述看多观点，专注攻击薄弱环节。"
                    ),
                    context_blocks=[
                        f"### 证据快照（匿名）\n{reports.get('data', '')}",
                        f"### 研究材料（匿名）\n{research_blob}",
                    ],
                    require_structured=False,
                ),
                "red",
                depends="research_done",
            )

        researcher_fns = {
            "market_regime": run_market,
            "company_research": run_company,
            "quant_research": run_quant,
        }

        graph = TaskGraph()
        graph.add(TaskNode("data", "data_guardian", run_data))

        active_keys: list[str] = []
        for rname in team.researchers:
            if rname not in researcher_fns:
                continue
            key = _RESEARCHER_KEYS[rname]
            active_keys.append(key)
            graph.add(TaskNode(key, rname, researcher_fns[rname], depends_on=["data"]))

        if not active_keys:
            active_keys = ["market", "company", "quant"]
            graph.add(TaskNode("market", "market_regime", run_market, depends_on=["data"]))
            graph.add(TaskNode("company", "company_research", run_company, depends_on=["data"]))
            graph.add(TaskNode("quant", "quant_research", run_quant, depends_on=["data"]))

        def mark_research_done() -> str:
            reports["research_done"] = "ok"
            return "ok"

        graph.add(TaskNode(
            "research_done", "merge", mark_research_done,
            depends_on=active_keys,
        ))

        if team.include_red_team and _red_team_enabled():
            graph.add(TaskNode("red", "red_team", run_red_team, depends_on=["research_done"]))
        else:
            reports["red"] = "（本 run 跳过 Red-Team；设置 FIAGENT_RESEARCH_RED_TEAM=1 启用）"

        graph.run(max_workers=_research_max_workers())

        policy_brief = ""
        if mode == AgentMode.COMMITTEE:
            _emit_progress({"phase": "policy_start", "run_id": run.id})
            policy_brief = self._run_policy_gates(run.id, query, reports)
            _emit_progress({"phase": "policy_done", "run_id": run.id})

        _emit_progress({"phase": "cio_start", "run_id": run.id})
        synthesis = self._synthesize(
            query, reports, policy_brief, messages, run.id, mode, profiles["orchestrator"],
        )
        failed_keys = _failed_report_keys(reports)
        run_status = "partial" if failed_keys else "completed"
        self.evidence.finish_run(run.id, run_status)
        _emit_progress({
            "phase": "complete",
            "run_id": run.id,
            "mode": mode.value,
            "status": run_status,
            "failed_count": len(failed_keys),
            "failed_keys": failed_keys,
        })
        set_research_run_active(False)
        if failed_keys:
            ui.warn(
                f"研究团队 {len(failed_keys)} 个环节未成功: {', '.join(failed_keys)}"
                "（详情见右侧运行面板）"
            )
        return synthesis

    def _run_policy_gates(self, run_id: str, query: str, reports: dict[str, str]) -> str:
        blob = "\n".join(reports.get(k, "") for k in ("data", "market", "company", "quant"))
        proposals = build_proposals(query, blob)
        if not proposals:
            proposals = build_proposals(query)

        lines = ["## 政策硬门结果（确定性引擎，LLM 不可覆盖）", ""]
        all_ok = True
        passed_symbols: set[str] = set()
        proposal_parents: dict[str, str] = {}

        for p in proposals:
            parent_id = record_proposal_lineage(
                self.evidence, run_id, p.symbol, p.target_weight,
            )
            proposal_parents[p.symbol] = parent_id

            rules = self.compliance.get_trading_rules(
                p.symbol, action="buy", require_verified_status=True,
            )
            self.evidence.save_policy_decision(
                run_id, "compliance", rules.to_dict(), approved=rules.action_allowed,
            )
            self.evidence.save_lineage_step(
                run_id, p.symbol, "compliance", rules.to_dict(), parent_id=parent_id,
            )
            lines.append(f"### 合规 {p.symbol}")
            lines.append("```json")
            lines.append(json.dumps(rules.to_dict(), ensure_ascii=False, indent=2))
            lines.append("```")
            if not rules.action_allowed:
                all_ok = False
            else:
                passed_symbols.add(p.symbol)

        risk_mult = self._parse_risk_multiplier(reports.get("market", ""))
        decisions = self.risk.evaluate_batch(
            proposals, market_risk_multiplier=risk_mult,
        )
        for p, decision in zip(proposals, decisions):
            self.evidence.save_policy_decision(
                run_id, "risk", {"symbol": p.symbol, **decision.to_dict()},
                approved=decision.approved,
            )
            self.evidence.save_lineage_step(
                run_id,
                p.symbol,
                "risk",
                {"symbol": p.symbol, **decision.to_dict()},
                parent_id=proposal_parents.get(p.symbol),
            )
            lines.append(f"### 风险 {p.symbol}")
            lines.append("```json")
            lines.append(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2))
            lines.append("```")
            if not decision.approved:
                all_ok = False
                passed_symbols.discard(p.symbol)

        exec_brief = self._run_execution_simulations(
            run_id, proposals, passed_symbols, proposal_parents,
        )
        if exec_brief:
            lines.extend(["", exec_brief])

        lines.append("")
        if all_ok:
            lines.append("**政策门：通过**（CIO 可在此基础上给出仓位建议）")
        else:
            lines.append("**政策门：存在否决项**（CIO 必须说明如何调整或放弃建议）")
        return "\n".join(lines)

    def _fetch_latest_bar(self, symbol: str) -> dict[str, float] | None:
        try:
            from market.market_data import fetch_market_data

            end = datetime.now().strftime("%Y-%m-%d")
            start = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
            data = fetch_market_data(
                codes=[symbol.upper()],
                start_date=start,
                end_date=end,
                source="auto",
                max_rows=30,
            )
            entry = data.get(symbol.upper()) or {}
            rows = entry.get("data") or []
            if not rows:
                return None
            row = rows[-1]
            prev_close = float(rows[-2]["close"]) if len(rows) >= 2 else None
            return bar_from_ohlcv_row(row, prev_close=prev_close)
        except Exception:
            return None

    def _run_execution_simulations(
        self,
        run_id: str,
        proposals: list[PositionProposal],
        approved_symbols: set[str],
        proposal_parents: dict[str, str],
    ) -> str:
        lines = ["## 执行模拟（确定性，禁止假设收盘价全额成交）", ""]
        any_sim = False

        for p in proposals:
            if p.symbol not in approved_symbols:
                continue
            bar = self._fetch_latest_bar(p.symbol)
            if bar is None:
                skip = {
                    "status": "skipped",
                    "symbol": p.symbol,
                    "reason": "no_market_bar",
                }
                self.evidence.save_lineage_step(
                    run_id, p.symbol, "execution_sim", skip,
                    parent_id=proposal_parents.get(p.symbol),
                )
                lines.append(f"### 执行 {p.symbol}")
                lines.append("无可用行情 bar，跳过模拟（请 CIO 注明执行风险）")
                continue

            intent = OrderIntent(
                symbol=p.symbol,
                side="buy",
                target_weight=p.target_weight,
                exec_style="next_open",
            )
            report = self.execution.simulate(
                intent, bar, portfolio=PortfolioSnapshot(),
            )
            record_execution_lineage(
                self.evidence,
                run_id,
                p.symbol,
                report.to_dict(),
                parent_id=proposal_parents.get(p.symbol),
            )
            self.evidence.save_policy_decision(
                run_id, "execution", report.to_dict(),
                approved=report.status in ("filled", "partial"),
            )
            self.evidence.save_lineage_step(
                run_id, p.symbol, "target_position",
                {"symbol": p.symbol, "target_weight": p.target_weight},
                parent_id=proposal_parents.get(p.symbol),
            )
            any_sim = True
            lines.append(f"### 执行 {p.symbol}")
            lines.append("```json")
            lines.append(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
            lines.append("```")

        if not any_sim and not proposals:
            return ""
        if not any_sim:
            lines.append("（无通过合规/风险门的标的，未执行模拟）")
        return "\n".join(lines)

    @staticmethod
    def _parse_risk_multiplier(market_report: str) -> float:
        m = re.search(r"risk_budget_multiplier[\"']?\s*[:=]\s*([0-9.]+)", market_report or "")
        if m:
            try:
                return max(0.1, min(1.0, float(m.group(1))))
            except ValueError:
                pass
        return 1.0

    def _synthesize(
        self,
        query: str,
        reports: dict[str, str],
        policy_brief: str,
        messages: list[dict[str, Any]],
        run_id: str,
        mode: AgentMode,
        cio_profile: AgentProfile,
    ) -> str:
        parts = [
            f"## 用户问题\n{query}",
            f"## 运行模式\n{mode.value}",
            f"## Research Run ID\n{run_id}",
            "## Data Guardian",
            reports.get("data", "（无）"),
            "## Market Regime",
            reports.get("market", "（无）"),
            "## Company Research",
            reports.get("company", "（无）"),
            "## Quant Research",
            reports.get("quant", "（无）"),
            "## Red-Team",
            reports.get("red", "（无）"),
        ]
        if policy_brief:
            parts.extend([policy_brief, ""])
        parts.append("## 你的任务")
        parts.append(
            "作为 CIO，综合以上专家报告，输出最终投研结论。"
            "必须：1) 明确观点与置信度；2) 列出支持/反对证据；"
            "3) 说明 Red-Team 指出的风险是否改变结论；"
            "4) 给出失效条件与下一步验证建议。"
            "文末附 ```json``` CIO 结论卡：stance, confidence(0-1), symbols, target_weights, "
            "invalidation_conditions。"
            + (
                " 5) Committee 模式须参考政策硬门与执行模拟结果给出仓位建议区间；"
                "若政策门否决或执行模拟为 rejected，不得给出违规仓位或假设全额成交。"
                if mode == AgentMode.COMMITTEE else ""
            )
            + "\n禁止调用行情/回测工具；可用 read_agent_report / list_run_evidence 查阅本 run。"
        )

        brief = "\n\n".join(parts)
        cio_ctx = AgentContext(self.root, profile=cio_profile)
        cio_ctx.refresh()
        cio_messages: list[dict[str, Any]] = cio_ctx.fresh_messages()
        cio_messages.append({"role": "user", "content": brief})

        set_run_context(ResearchRunContext(run_id, self.evidence, "orchestrator"))
        try:
            final, _ = collect_agent_turn(
                self.client,
                cio_messages,
                cio_ctx,
                self.hooks,
                max_rounds=cio_profile.max_tool_rounds,
                quiet=False,
            )
        finally:
            set_run_context(None)

        validation = validate_agent_output("orchestrator", final)
        if validation.get("structured"):
            claim = validation["structured"]
            self.evidence.save_claim(run_id, "orchestrator", "cio_conclusion", claim)
            weights = claim.get("target_weights") or {}
            if isinstance(weights, dict):
                for sym, w in weights.items():
                    self.evidence.save_lineage_step(
                        run_id, str(sym), "cio_target",
                        {"target_weight": w, "stance": claim.get("stance")},
                    )

        # Keep the main conversation natural: the user already asked ``query``
        # in the parent thread.  The large internal synthesis brief belongs to
        # the isolated CIO context and must not be replayed on every later turn.
        messages.append({
            "role": "assistant",
            "content": final,
            "_fiagent": {
                "collaboration": {
                    "run_id": run_id,
                    "mode": mode.value,
                    "query": query,
                }
            },
        })
        self.evidence.save_report(
            run_id, "orchestrator", final,
            structured=validation.get("structured"),
        )
        return final or "（研究团队已完成，但未生成最终文本）"
