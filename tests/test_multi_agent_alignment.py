"""Alignment tests: PIT gate, validators, team selection, CIO isolation."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from core.agents.profile import load_profile
from core.agents.team_selector import select_team
from core.context import AgentContext
from paths import PROJECT_ROOT
from research.evidence_store import EvidenceStore
from research.run_context import ResearchRunContext, pit_gate_block_message, set_run_context
from research.validators import validate_agent_output, validate_company_card, parse_data_guardian_evidence
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
