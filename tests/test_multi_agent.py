"""Multi-agent foundation tests."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from core.agents.profile import load_profile, list_profiles
from core.agents.router import AgentMode, route_query
from core.agents.task_graph import TaskGraph, TaskNode
from core.context import AgentContext
from research.evidence_store import EvidenceStore


def test_list_profiles_includes_mvp_roles():
    names = list_profiles()
    for required in (
        "data_guardian",
        "market_regime",
        "company_research",
        "quant_research",
        "trade_review",
        "red_team",
        "orchestrator",
    ):
        assert required in names


def test_load_data_guardian_profile():
    p = load_profile("data_guardian")
    assert p.name == "data_guardian"
    assert p.tool_allowed("search_symbol")
    assert not p.tool_allowed("run_backtest")
    assert p.skill_allowed("data-routing")


def test_load_red_team_has_no_tools():
    p = load_profile("red_team")
    assert p.allowed_tools == frozenset()
    assert not p.tool_allowed("read")


def test_route_query_fast_for_short_lookup():
    assert route_query("600519 最新股价") == AgentMode.FAST


def test_route_query_research_for_deep_analysis():
    assert route_query("深度分析宁德时代") == AgentMode.RESEARCH


def test_route_query_committee_for_buy_decision():
    assert route_query("茅台现在是否值得买入") == AgentMode.COMMITTEE


def test_context_profile_tool_isolation():
    from paths import PROJECT_ROOT

    ctx = AgentContext(PROJECT_ROOT, profile=load_profile("data_guardian"))
    ctx.refresh()
    names = {n for n, _ in ctx.enabled_tools()}
    assert "search_symbol" in names
    assert "run_backtest" not in names


def test_context_profile_blocks_unauthorized_tool():
    from paths import PROJECT_ROOT

    ctx = AgentContext(PROJECT_ROOT, profile=load_profile("red_team"))
    result = ctx.execute_tool("get_market_data", "{}")
    assert "授权范围" in result


def test_task_graph_runs_dependencies_in_order():
    order: list[str] = []

    def mk(name: str, deps: list[str]):
        def _run() -> str:
            order.append(name)
            return name
        return TaskNode(name, name, _run, depends_on=deps)

    g = TaskGraph()
    g.add(mk("a", []))
    g.add(mk("b", ["a"]))
    g.add(mk("c", ["a"]))
    g.add(mk("d", ["b", "c"]))
    g.run()
    assert order.index("a") < order.index("b")
    assert order.index("a") < order.index("c")
    assert order.index("b") < order.index("d")
    assert order.index("c") < order.index("d")


def test_evidence_store_run_and_reports():
    with tempfile.TemporaryDirectory() as tmp:
        store = EvidenceStore(Path(tmp) / "research.db")
        try:
            run = store.start_run("测试问题", "research")
            store.save_report(run.id, "data_guardian", "证据快照")
            store.add_evidence(run.id, symbol="600519.SH", pit_safe=True, quality="normal")
            store.finish_run(run.id)
            reports = store.list_reports(run.id)
            assert len(reports) == 1
            assert reports[0]["agent_name"] == "data_guardian"
            ev = store.list_evidence(run.id)
            assert len(ev) == 1
            assert ev[0].pit_safe is True
        finally:
            store.close()
