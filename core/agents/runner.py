"""Headless agent runner for sub-agent turns."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import OpenAI

from core.agents.profile import AgentProfile
from core.context import AgentContext
from core.loop import collect_agent_turn
from hooks.registry import HookRegistry
from research.evidence_store import EvidenceStore
from research.run_context import ResearchRunContext, set_run_context
from research.validators import (
    validate_agent_output,
    validate_evidence_references,
    validate_quant_tool_evidence,
)
from ui import ui

_STRUCTURED_SUFFIX = (
    "\n\n**必须**在报告末尾附上符合角色规范的 ```json``` 结构化卡片；"
    "缺 JSON 卡视为未完成。"
)


@dataclass
class AgentTask:
    instruction: str
    context_blocks: list[str] | None = None
    require_structured: bool = True


@dataclass
class AgentResult:
    agent_name: str
    content: str
    messages: list[dict[str, Any]]
    tool_rounds: int
    success: bool
    structured: dict[str, Any] | None = None
    validation_errors: list[str] = field(default_factory=list)
    error: str | None = None


class AgentRunner:
    """Run a single agent profile in isolation (sub-agent turn)."""

    def __init__(
        self,
        root: Path,
        client: OpenAI,
        hooks: HookRegistry,
        *,
        store: EvidenceStore | None = None,
    ) -> None:
        self.root = root
        self.client = client
        self.hooks = hooks
        self.store = store

    def _validate_output(
        self,
        agent_name: str,
        content: str,
        run_id: str,
    ) -> dict[str, Any]:
        validation = validate_agent_output(agent_name, content)
        if self.store is None or not run_id:
            return validation

        runtime_errors: list[str] = []
        if agent_name == "quant_research":
            runtime_errors.extend(validate_quant_tool_evidence(
                validation.get("structured"),
                self.store.list_tool_calls(run_id, agent_name),
            ))
        if agent_name in {
            "data_guardian",
            "market_regime",
            "company_research",
            "quant_research",
        }:
            runtime_errors.extend(validate_evidence_references(
                validation.get("structured"),
                self.store.list_evidence(run_id),
            ))
        if runtime_errors:
            validation["valid"] = False
            validation["errors"] = list(validation.get("errors") or []) + runtime_errors
        return validation

    def run(
        self,
        profile: AgentProfile,
        task: AgentTask,
        *,
        run_id: str = "",
        pit_safe_for_backtest: bool = False,
        evidence_items: list[dict] | None = None,
    ) -> AgentResult:
        ctx = AgentContext(self.root, profile=profile)
        ctx.refresh()

        parts: list[str] = []
        if task.context_blocks:
            parts.append("## 上游上下文")
            parts.extend(task.context_blocks)
        parts.append("## 任务")
        parts.append(task.instruction)
        parts.append(
            "请输出结构化 Markdown 报告。结论须标注证据来源；"
            "若数据不足请明确说明，不要编造数字。"
        )
        if task.require_structured:
            parts.append(_STRUCTURED_SUFFIX)

        user_body = "\n\n".join(parts)
        messages: list[dict[str, Any]] = ctx.fresh_messages()
        messages.append({"role": "user", "content": user_body})

        model_override = None
        if profile.name == "red_team":
            model_override = os.getenv("FIAGENT_RED_TEAM_MODEL", "").strip() or None

        rc = None
        if run_id and self.store:
            rc = ResearchRunContext(
                run_id=run_id,
                store=self.store,
                agent_name=profile.name,
                pit_safe_for_backtest=pit_safe_for_backtest,
                evidence_items=list(evidence_items or []),
            )
            set_run_context(rc)

        try:
            content, rounds = collect_agent_turn(
                self.client,
                messages,
                ctx,
                self.hooks,
                max_rounds=profile.max_tool_rounds,
                quiet=True,
                model_override=model_override,
            )
            validation = self._validate_output(profile.name, content, run_id)
            errors = list(validation.get("errors") or [])

            if task.require_structured and not validation.get("valid"):
                revision = (
                    "上一轮输出未通过结构化校验："
                    + "; ".join(errors)
                    + "。请修订并补全 ```json``` 卡片。"
                )
                messages.append({"role": "user", "content": revision})
                content, rounds = collect_agent_turn(
                    self.client,
                    messages,
                    ctx,
                    self.hooks,
                    max_rounds=min(4, profile.max_tool_rounds),
                    quiet=True,
                    model_override=model_override,
                )
                validation = self._validate_output(profile.name, content, run_id)
                errors = list(validation.get("errors") or [])

            valid = not task.require_structured or bool(validation.get("valid"))
            error = None
            structured = validation.get("structured")
            if not valid:
                error = "结构化校验未通过: " + "; ".join(errors)
                # Invalid cards must never enter claims or downstream synthesis.
                structured = None

            return AgentResult(
                agent_name=profile.name,
                content=content,
                messages=messages,
                tool_rounds=rounds,
                success=valid,
                structured=structured,
                validation_errors=errors,
                error=error,
            )
        except Exception as exc:
            return AgentResult(
                agent_name=profile.name,
                content="",
                messages=messages,
                tool_rounds=0,
                success=False,
                error=str(exc),
            )
        finally:
            if rc is not None:
                set_run_context(None)
            if self.store is not None:
                self.store.close_thread()
