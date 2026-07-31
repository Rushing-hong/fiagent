"""Phase 4: execution simulator + decision lineage."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from core.agents.profile import load_profile
from policy.execution_engine import (
    ExecutionConfig,
    ExecutionEngine,
    OrderIntent,
    PortfolioSnapshot,
    bar_from_ohlcv_row,
)
from research.evidence_store import EvidenceStore
from research.lineage import enrich_attribution_with_lineage, record_execution_lineage
from tools.execution_simulate import SimulateExecutionTool


def _normal_bar(close: float = 10.0, volume: float = 1_000_000) -> dict:
    return bar_from_ohlcv_row({
        "open": close * 0.99,
        "high": close * 1.01,
        "low": close * 0.98,
        "close": close,
        "volume": volume,
        "amount": close * volume,
        "prev_close": close / 1.02,
    })


def test_execution_limit_up_reject():
    prev = 10.0
    limit = prev * 1.10
    bar = bar_from_ohlcv_row({
        "open": limit,
        "high": limit,
        "low": limit,
        "close": limit,
        "volume": 500_000,
        "amount": limit * 500_000,
        "prev_close": prev,
    })
    eng = ExecutionEngine()
    report = eng.simulate(
        OrderIntent(symbol="600519.SH", side="buy", quantity=1000),
        bar,
    )
    assert report.status == "rejected"
    assert report.reject_reason == "limit_up_locked"


def test_execution_partial_fill_participation():
    bar = _normal_bar(close=20.0, volume=50_000)
    cfg = ExecutionConfig(participation_rate=0.10)
    eng = ExecutionEngine(cfg)
    report = eng.simulate(
        OrderIntent(symbol="000001.SZ", side="buy", quantity=10_000),
        bar,
    )
    assert report.status == "partial"
    assert report.filled_qty == 5000
    assert report.filled_qty < report.requested_qty


def test_execution_rejects_invalid_order_or_unfillable_participation():
    bar = _normal_bar(close=20.0, volume=500)
    invalid_side = ExecutionEngine().simulate(
        OrderIntent(symbol="000001.SZ", side="hold", quantity=100), bar
    )
    assert invalid_side.reject_reason == "invalid_side"
    below_lot = ExecutionEngine(ExecutionConfig(participation_rate=0.10)).simulate(
        OrderIntent(symbol="000001.SZ", side="buy", quantity=100), bar
    )
    assert below_lot.reject_reason == "participation_limit_below_lot"


def test_execution_tplus1_sell_reject():
    bar = _normal_bar()
    pf = PortfolioSnapshot(
        positions={"600519.SH": 1000},
        buy_dates={"600519.SH": "2026-07-30"},
        trade_date="2026-07-30",
    )
    report = ExecutionEngine().simulate(
        OrderIntent(symbol="600519.SH", side="sell", quantity=1000),
        bar,
        portfolio=pf,
    )
    assert report.status == "rejected"
    assert report.reject_reason == "tplus1_locked"


def test_simulate_execution_tool_with_bar():
    tool = SimulateExecutionTool()
    from paths import PROJECT_ROOT
    from core.context import AgentContext

    ctx = AgentContext(PROJECT_ROOT)
    bar = {
        "open": 9.9,
        "high": 10.1,
        "low": 9.8,
        "close": 10.0,
        "volume": 2_000_000,
        "amount": 20_000_000,
        "prev_close": 9.8,
    }
    out = json.loads(tool.execute({
        "symbol": "600519.SH",
        "side": "buy",
        "target_weight": 0.05,
        "bar": bar,
    }, ctx))
    assert out["status"] == "ok"
    assert out["fill_status"] == "filled"
    assert out["filled_qty"] > 0


def test_decision_lineage_store():
    with tempfile.TemporaryDirectory() as tmp:
        store = EvidenceStore(Path(tmp) / "research.db")
        try:
            run = store.start_run("committee 600519", "committee")
            store.save_lineage_step(run.id, "600519.SH", "proposal", {"target_weight": 0.05})
            store.save_lineage_step(
                run.id, "600519.SH", "execution_sim",
                {"status": "partial", "filled_qty": 500},
            )
            chain = store.get_lineage_chain(run.id, "600519.SH")
            assert len(chain) == 2
            assert chain[0]["step"] == "proposal"
            found = store.find_lineage_for_symbol("600519.SH")
            assert len(found) >= 1
        finally:
            store.close()


def test_enrich_attribution_with_lineage():
    with tempfile.TemporaryDirectory() as tmp:
        store = EvidenceStore(Path(tmp) / "research.db")
        try:
            run = store.start_run("buy 600519", "committee")
            record_execution_lineage(
                store, run.id, "600519.SH",
                {"status": "rejected", "reject_reason": "limit_up_locked"},
            )
            attr = enrich_attribution_with_lineage(store, {
                "sample_roundtrips": [
                    {"symbol": "600519.SH", "pnl": -100, "attribution": "wrong_loss"},
                ],
            })
            sample = attr["sample_roundtrips"][0]
            assert sample.get("decision_lineage")
            assert sample.get("attribution_hint") == "execution_not_filled"
        finally:
            store.close()


def test_quant_profile_has_simulate_execution():
    p = load_profile("quant_research")
    assert p.tool_allowed("simulate_execution")
