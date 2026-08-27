"""Phase 2 multi-agent: split researchers, policy engines, evals."""

from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path

import pytest

from core.agents.extract import build_proposals, extract_symbols, infer_target_weight
from core.agents.profile import list_profiles, load_profile
from evals.metrics import EvalRecorder, RunMetrics
from policy.compliance_engine import ComplianceEngine
from policy.risk_engine import PositionProposal, RiskEngine
from research.evidence_store import EvidenceStore


def test_list_profiles_includes_split_researchers():
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


def test_market_regime_cannot_run_dcf():
    p = load_profile("market_regime")
    assert p.tool_allowed("get_market_breadth")
    assert not p.tool_allowed("calc_dcf")


def test_company_research_cannot_run_backtest():
    p = load_profile("company_research")
    assert p.tool_allowed("calc_dcf")
    assert not p.tool_allowed("run_backtest")


def test_quant_research_cannot_run_dcf():
    p = load_profile("quant_research")
    assert p.tool_allowed("run_backtest")
    assert not p.tool_allowed("calc_dcf")


def test_extract_symbols_from_text():
    syms = extract_symbols("分析 600519 和 300750.SZ")
    assert "600519.SH" in syms
    assert "300750.SZ" in syms


def test_infer_target_weight():
    assert infer_target_weight("建议仓位 8%") == 0.08
    assert infer_target_weight("重仓买入") == 0.15


def test_build_proposals():
    props = build_proposals("是否买入 600519，仓位10%", "标的 600519.SH")
    assert len(props) >= 1
    assert props[0].symbol == "600519.SH"
    assert abs(props[0].target_weight - 0.10) < 0.001


def test_compliance_engine_main_board():
    eng = ComplianceEngine()
    rules = eng.get_trading_rules("600519.SH", action="buy")
    assert rules.board == "MAIN"
    assert rules.t_plus == 1
    assert rules.action_allowed is False
    assert "security_status_unverified" in rules.veto_reasons
    assert rules.price_limit == 0.10


def test_risk_engine_veto_overweight():
    eng = RiskEngine()
    d = eng.evaluate(PositionProposal("600519.SH", 0.25))
    assert d.approved is False
    assert d.adjusted_weight == 0.10


def test_risk_engine_batch_enforces_portfolio_cash_buffer():
    decisions = RiskEngine().evaluate_batch(
        [PositionProposal(f"600{i:03d}.SH", 0.10) for i in range(12)]
    )
    assert all(d.approved for d in decisions[:9])
    assert decisions[9].approved is False
    assert any("现金缓冲" in item for item in decisions[9].violations)


def test_compliance_does_not_fabricate_historical_rules_or_status():
    eng = ComplianceEngine()
    rules = eng.get_trading_rules("600519.SH")
    assert rules.security_status == "UNVERIFIED"
    assert rules.status_verified is False
    with pytest.raises(ValueError, match="历史交易规则"):
        eng.get_trading_rules("600519.SH", as_of_date="2025-01-01")


def test_evidence_store_policy_decisions():
    with tempfile.TemporaryDirectory() as tmp:
        store = EvidenceStore(Path(tmp) / "research.db")
        try:
            run = store.start_run("test", "committee")
            store.save_policy_decision(
                run.id, "risk", {"symbol": "600519.SH", "approved": False},
                approved=False,
            )
            rows = store.list_policy_decisions(run.id)
            assert len(rows) == 1
            assert rows[0]["engine"] == "risk"
            assert rows[0]["approved"] is False
        finally:
            store.close()


def test_evidence_store_closes_worker_connections(tmp_path):
    store = EvidenceStore(tmp_path / "research.db")
    run = store.start_run("parallel", "committee")

    def write(i: int) -> None:
        store.save_report(run.id, f"worker-{i}", "ok")
        store.close_thread()

    workers = [threading.Thread(target=write, args=(i,)) for i in range(6)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert len(store.list_reports(run.id)) == 6
    store.close()


def test_eval_recorder_append_and_summarize():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "runs.jsonl"
        rec = EvalRecorder(path)
        rec.record(RunMetrics(variant="A", query="q1", latency_ms=100, success=True))
        rec.record(RunMetrics(variant="B", query="q2", latency_ms=200, success=True))
        assert len(rec.load_all()) == 2
        summary = rec.summarize_by_variant()
        assert summary["A"]["count"] == 1
        assert summary["B"]["avg_latency_ms"] == 200
