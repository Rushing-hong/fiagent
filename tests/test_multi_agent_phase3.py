"""Phase 3: trade review, get_trading_rules tool, eval dashboard."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from core.agents.trade_review import extract_journal_path
from core.agents.profile import load_profile
from core.agents.router import AgentMode, route_query
from evals.dashboard import build_dashboard
from evals.metrics import EvalRecorder, RunMetrics
from evals.tracker import TurnEvalStats, eval_tracking_enabled
from policy.compliance_engine import ComplianceEngine
from research.attribution import build_attribution_from_journal
from research.evidence_store import EvidenceStore
from tools.trading_rules import GetTradingRulesTool


def test_route_trade_review():
    assert route_query("帮我复盘交易记录") == AgentMode.TRADE_REVIEW


def test_extract_journal_path():
    assert extract_journal_path("/review uploads/trades.csv 分析") == "uploads/trades.csv"


def test_get_trading_rules_tool():
    tool = GetTradingRulesTool()
    from paths import PROJECT_ROOT
    from core.context import AgentContext

    ctx = AgentContext(PROJECT_ROOT)
    out = json.loads(tool.execute({"security": "688001.SH", "action": "buy"}, ctx))
    assert out["status"] == "ok"
    assert out["board"] == "STAR"
    assert out["t_plus"] == 1


def test_compliance_st_status_override():
    eng = ComplianceEngine()
    rules = eng.get_trading_rules("600519.SH", security_status="ST")
    assert rules.security_status == "ST"
    assert rules.action_allowed is True


def test_trade_review_profile():
    p = load_profile("trade_review")
    assert p.tool_allowed("analyze_trade_journal")
    assert p.tool_allowed("get_trading_rules")


def test_build_attribution_from_journal_minimal():
    journal = {
        "status": "ok",
        "profile": {"win_rate": 0.55, "total_pnl": 1000},
        "behavior": {
            "disposition_effect": {"severity": "high"},
            "overtrading": {"severity": "low"},
            "chasing_momentum": {"severity": "medium"},
        },
    }
    attr = build_attribution_from_journal(journal)
    assert attr["status"] == "ok"
    assert attr["behavior_flags"]["disposition_effect"] == "high"
    assert len(attr["learnable_lessons"]) >= 1


def test_evidence_find_runs_by_symbol():
    with tempfile.TemporaryDirectory() as tmp:
        store = EvidenceStore(Path(tmp) / "research.db")
        try:
            run = store.start_run("分析600519", "research")
            store.save_report(run.id, "company_research", "600519.SH 茅台基本面良好")
            found = store.find_recent_runs_by_symbol("600519.SH")
            assert len(found) == 1
            assert found[0]["run_id"] == run.id
        finally:
            store.close()


def test_eval_dashboard_with_data():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "runs.jsonl"
        rec = EvalRecorder(path)
        rec.record(RunMetrics(
            variant="A", query="q", latency_ms=1000, success=True,
            extra={"tool_calls": 3, "pit_unsafe_hits": 0},
        ))
        rec.record(RunMetrics(
            variant="B", query="q", latency_ms=5000, success=True,
            extra={"tool_calls": 12, "pit_unsafe_hits": 1},
        ))
        dash = build_dashboard(path)
        assert dash["status"] == "ok"
        assert "A" in dash["by_variant"]
        assert dash["aggregate"]["total_tool_calls"] == 15


def test_turn_eval_stats_pit_detection():
    stats = TurnEvalStats()
    stats.record_tool("load_pit_universe", '{"pit_safe": false}')
    assert stats.pit_unsafe_hits == 1
