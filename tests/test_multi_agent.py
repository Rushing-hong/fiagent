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


def test_dispatch_defaults_to_main_agent_even_for_deep_wording():
    from core.agents.dispatch import _resolve_turn_mode

    mode, query = _resolve_turn_mode("深度分析宁德时代")
    assert mode == AgentMode.FAST
    assert query == "深度分析宁德时代"


def test_dispatch_slash_command_remains_one_shot_collaboration():
    from core.agents.dispatch import _resolve_turn_mode

    mode, query = _resolve_turn_mode("/research 深度分析宁德时代")
    assert mode == AgentMode.RESEARCH
    assert query == "深度分析宁德时代"


def test_dispatch_button_override_applies_to_one_turn_only():
    from core.agents.dispatch import _resolve_turn_mode

    selected, _ = _resolve_turn_mode("分析茅台", AgentMode.COMMITTEE)
    following, _ = _resolve_turn_mode("继续解释一下")

    assert selected == AgentMode.COMMITTEE
    assert following == AgentMode.FAST


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


def test_quant_grade_requires_successful_calculation_tool():
    from research.validators import validate_quant_tool_evidence

    card = {"backtest_grade": "C", "live_readiness": False}
    failed = [{"tool_name": "analyze_portfolio_risk", "success": False}]
    assert validate_quant_tool_evidence(card, failed)
    assert validate_quant_tool_evidence(
        card,
        [{"tool_name": "run_backtest", "success": True}],
    ) == []
    assert validate_quant_tool_evidence(
        {"backtest_grade": "D", "live_readiness": False},
        failed,
    ) == []


def test_research_run_is_partial_when_an_agent_failed():
    from core.agents.orchestrator import _failed_report_keys

    reports = {
        "data": "ok",
        "market": "失败: provider rejected request",
        "company": "ok",
        "research_done": "ok",
    }
    assert _failed_report_keys(reports) == ["market"]


def test_all_agent_profiles_share_system_prefix():
    from paths import PROJECT_ROOT

    main = AgentContext(PROJECT_ROOT)
    data = AgentContext(PROJECT_ROOT, profile=load_profile("data_guardian"))
    red = AgentContext(PROJECT_ROOT, profile=load_profile("red_team"))

    assert main.build_system_prompt() == data.build_system_prompt()
    assert data.build_system_prompt() == red.build_system_prompt()
    assert "今天：" not in main.build_system_prompt()
    assert data.fresh_messages()[0] == red.fresh_messages()[0]

    base_messages = data.fresh_messages() + [{"role": "user", "content": "检查数据"}]
    request = data.with_runtime_context_for_api(base_messages)
    assert request[0] == base_messages[0]
    assert request[1]["role"] == "user"
    assert request[1]["content"].startswith("【应用注入的 Agent 运行时约束】")
    assert load_profile("data_guardian").system_prompt in request[1]["content"]
    assert request[1]["content"].endswith("## 用户任务\n检查数据")
    assert len(request) == len(base_messages)


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
