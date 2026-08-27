"""Alignment tests: PIT gate, validators, team selection, CIO isolation."""

from __future__ import annotations

import json
import tempfile
from collections import deque
from pathlib import Path
from types import SimpleNamespace

from core.agents.profile import load_profile
from core.agents.team_selector import select_team
from core.context import AgentContext
from paths import PROJECT_ROOT
from research.evidence_store import EvidenceStore
from research.run_context import ResearchRunContext, pit_gate_block_message, set_run_context
from research.validators import (
    parse_data_guardian_evidence,
    validate_agent_output,
    validate_company_card,
    validate_evidence_references,
)
from research.memory import extract_validated_lessons


def test_pit_gate_blocks_backtest_without_evidence():
    with tempfile.TemporaryDirectory() as tmp:
        store = EvidenceStore(Path(tmp) / "research.db")
        try:
            set_run_context(ResearchRunContext("r1", store, "quant_research", pit_safe_for_backtest=False))
            msg = pit_gate_block_message("run_backtest")
            assert msg is not None
            assert "PIT_GATE_BLOCKED" in msg
            set_run_context(ResearchRunContext("r1", store, "quant_research", pit_safe_for_backtest=True))
            assert pit_gate_block_message("run_backtest") is None
        finally:
            set_run_context(None)
            store.close()


def test_parallel_readonly_tools_inherit_research_context(tmp_path):
    from core.loop import _execute_tool_calls
    from hooks.registry import HookRegistry

    store = EvidenceStore(tmp_path / "research.db")
    run = store.start_run("parallel PIT", "research")
    executed: list[str] = []

    class ToolContext:
        @staticmethod
        def is_readonly_tool(_name: str) -> bool:
            return True

        @staticmethod
        def is_repeatable_tool(_name: str) -> bool:
            return False

        @staticmethod
        def execute_tool(name: str, _arguments: str) -> str:
            executed.append(name)
            return json.dumps({
                "ok": True,
                "source": "test-source",
                "quality": "normal",
                "data": {"code": "600519.SH"},
            })

    calls = [
        SimpleNamespace(
            id="backtest",
            function=SimpleNamespace(name="run_backtest", arguments="{}"),
        ),
        SimpleNamespace(
            id="market",
            function=SimpleNamespace(
                name="get_market_data",
                arguments='{"codes":["600519.SH"]}',
            ),
        ),
    ]

    try:
        set_run_context(ResearchRunContext(
            run.id,
            store,
            "quant_research",
            pit_safe_for_backtest=False,
        ))
        results = _execute_tool_calls(
            calls,
            HookRegistry(),
            ToolContext(),
            {},
            deque(),
        )
        assert "PIT_GATE_BLOCKED" in results["backtest"]
        assert "run_backtest" not in executed
        assert "get_market_data" in executed

        logged = store.list_tool_calls(run.id, "quant_research")
        assert {row["tool_name"] for row in logged} == {
            "run_backtest",
            "get_market_data",
        }
        assert next(row for row in logged if row["tool_name"] == "run_backtest")["success"] is False
        assert next(row for row in logged if row["tool_name"] == "get_market_data")["success"] is True

        payload = json.loads(results["market"])
        evidence_id = payload["_evidence"]["evidence_id"]
        assert evidence_id.startswith(f"EV-{run.id}-")
        assert {item.evidence_id for item in store.list_evidence(run.id)} == {evidence_id}
    finally:
        set_run_context(None)
        store.close()


def test_validate_company_card():
    ok, errs = validate_company_card({"symbol": "600519.SH", "fundamental_score": 80})
    assert ok
    assert not errs


def test_validate_agent_output_company():
    text = '分析\n```json\n{"symbol": "600519.SH", "fundamental_score": 80}\n```'
    out = validate_agent_output("company_research", text)
    assert out["valid"]
    assert out["structured"]["symbol"] == "600519.SH"


def test_parse_data_guardian_evidence():
    text = '```json\n[{"symbol": "600519.SH", "pit_safe": true, "source": "cninfo"}]\n```'
    items, pit_ok = parse_data_guardian_evidence(text)
    assert pit_ok
    assert len(items) == 1


def test_validate_evidence_references_accepts_canonical_and_alias(tmp_path):
    store = EvidenceStore(tmp_path / "research.db")
    try:
        run = store.start_run("evidence refs", "research")
        record = store.add_evidence(
            run.id,
            symbol="600519.SH",
            source="test",
            extra={"evidence_id": "EV-ALIAS-001"},
        )
        evidence = store.list_evidence(run.id)
        assert validate_evidence_references(
            {"evidence_ids": [record.evidence_id, "EV-ALIAS-001"]},
            evidence,
        ) == []
        assert validate_evidence_references(
            {"evidence": [{"evidence_id": record.evidence_id}]},
            evidence,
        ) == []
        errors = validate_evidence_references(
            {"evidence_ids": ["EV-MISSING"]},
            evidence,
        )
        assert errors and "EV-MISSING" in errors[0]
    finally:
        store.close()


def test_data_guardian_enrichment_reuses_canonical_tool_evidence(tmp_path):
    store = EvidenceStore(tmp_path / "research.db")
    try:
        run = store.start_run("enrich evidence", "research")
        original = store.add_evidence(
            run.id,
            symbol="600519.SH",
            source="market-tool",
            quality="unknown",
            extra={"tool_call_id": "call-1", "tool_name": "get_market_data"},
        )
        enriched = store.add_evidence(
            run.id,
            symbol="600519.SH",
            source="market-tool",
            as_of_time="2026-08-10T10:00:00+08:00",
            pit_safe=True,
            quality="normal",
            fields=["close"],
            extra={"evidence_id": original.evidence_id},
        )
        assert enriched.evidence_id == original.evidence_id
        rows = store.list_evidence(run.id)
        assert len(rows) == 1
        assert rows[0].pit_safe is True
        assert rows[0].quality == "normal"
        assert rows[0].payload["tool_call_id"] == "call-1"
        assert rows[0].payload["fields"] == ["close"]
    finally:
        store.close()


def test_agent_runner_fails_after_second_invalid_structured_output(
    tmp_path,
    monkeypatch,
):
    import core.agents.runner as runner_module
    from core.agents.runner import AgentRunner, AgentTask
    from hooks.registry import HookRegistry

    invalid = (
        "量化报告\n"
        '```json\n{"backtest_grade":"B","live_readiness":false}\n```'
    )
    turns: list[int] = []

    def fake_collect(*_args, **_kwargs):
        turns.append(1)
        return invalid, 1

    monkeypatch.setattr(runner_module, "collect_agent_turn", fake_collect)
    store = EvidenceStore(tmp_path / "research.db")
    run = store.start_run("invalid quant", "research")
    try:
        runner = AgentRunner(
            PROJECT_ROOT,
            client=object(),
            hooks=HookRegistry(),
            store=store,
        )
        result = runner.run(
            load_profile("quant_research"),
            AgentTask("回测", require_structured=True),
            run_id=run.id,
            pit_safe_for_backtest=True,
        )
        assert len(turns) == 2
        assert result.success is False
        assert result.structured is None
        assert result.error and "结构化校验未通过" in result.error
        assert result.validation_errors
    finally:
        store.close()


def test_team_selector_strategy_backtest():
    team = select_team("双均线回测策略诊断")
    assert team.workflow_id == "strategy_backtest"
    assert team.researchers == ["quant_research"]


def test_team_selector_market_crowding():
    team = select_team("AI算力板块是否拥挤")
    assert "market_regime" in team.researchers


def test_cio_profile_tool_isolation():
    ctx = AgentContext(PROJECT_ROOT, profile=load_profile("orchestrator"))
    names = {n for n, _ in ctx.enabled_tools()}
    assert "read_agent_report" in names
    assert "get_market_data" not in names
    blocked = ctx.execute_tool("get_market_data", '{"codes":["600519.SH"]}')
    assert "授权范围" in blocked


def test_evidence_claims_and_tool_calls():
    with tempfile.TemporaryDirectory() as tmp:
        store = EvidenceStore(Path(tmp) / "research.db")
        try:
            run = store.start_run("test", "research")
            store.save_claim(run.id, "company_research", "research_card", {"symbol": "600519.SH"})
            store.log_tool_call(run.id, "company_research", "calc_dcf", "{}", "{}", success=True)
            assert len(store.list_claims(run.id)) == 1
            assert len(store.list_tool_calls(run.id)) == 1
        finally:
            store.close()


def test_validated_lessons_filter():
    attr = {
        "learnable_lessons": ["过度交易需降频", "亏损单不宜写入正向策略记忆"],
        "behavior_flags": {},
    }
    lessons = extract_validated_lessons(attr)
    assert any("过度交易" in x for x in lessons)
    assert not any("不宜写入" in x for x in lessons)


def test_orchestration_tool_read_report():
    from tools.orchestration import ReadAgentReportTool
    from research.run_context import set_run_context, ResearchRunContext

    with tempfile.TemporaryDirectory() as tmp:
        store = EvidenceStore(Path(tmp) / "research.db")
        try:
            run = store.start_run("q", "research")
            store.save_report(run.id, "market_regime", "report body")
            set_run_context(ResearchRunContext(run.id, store, "orchestrator"))
            tool = ReadAgentReportTool()
            ctx = AgentContext(PROJECT_ROOT, profile=load_profile("orchestrator"))
            out = json.loads(tool.execute({"agent_name": "market_regime"}, ctx))
            assert out["status"] == "ok"
            assert "report body" in out["content"]
        finally:
            set_run_context(None)
            store.close()
