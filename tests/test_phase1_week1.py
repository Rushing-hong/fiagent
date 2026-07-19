"""Phase1 Week1: calendar, store schema, macro envelope."""

from __future__ import annotations

import json
from pathlib import Path

from market.envelope import normalize_meta
from market.research_store import ResearchStore
from market.trade_calendar import is_trading_day, trading_days


def test_normalize_meta_fields():
    m = normalize_meta(source="akshare", frequency="monthly", unit="index_point")
    assert m["frequency"] == "monthly"
    assert m["unit"] == "index_point"
    assert m["stale"] is False
    assert "fetch_time" in m


def test_research_store_schema_and_macro(tmp_path: Path):
    store = ResearchStore(db_path=tmp_path / "research.db")
    assert store.count_trade_days() == 0
    n = store.replace_trade_calendar(["2024-01-02", "2024-01-03", "2024-01-04"])
    assert n == 3
    assert store.load_trade_days("2024-01-02", "2024-01-03") == ["2024-01-02", "2024-01-03"]

    store.upsert_macro_points([
        {
            "indicator": "pmi_mfg",
            "asof": "2024-06-01",
            "value": 50.5,
            "unit": "index_point",
            "frequency": "monthly",
            "source": "akshare",
            "fetch_time": "2024-07-01T00:00:00+08:00",
        }
    ])
    rows = store.load_macro("pmi_mfg")
    assert len(rows) == 1 and rows[0]["value"] == 50.5

    store.upsert_factor_values([
        ("2024-06-03", "600519.SH", "ep", 0.05, "alpha"),
        ("2024-06-03", "600519.SH", "mom_1m", 0.1, "alpha"),
        ("2024-06-03", "600519.SH", "size", 1.2, "risk"),
    ])
    # table exists / writable
    cur = store._conn().execute("SELECT COUNT(*) FROM factor_values")
    assert int(cur.fetchone()[0]) == 3


def test_trade_calendar_helpers(monkeypatch):
    import market.trade_calendar as tc

    monkeypatch.setattr(
        tc,
        "_cached_set",
        lambda: frozenset(["2024-01-02", "2024-01-03", "2024-01-05"]),
    )
    monkeypatch.setattr(
        tc,
        "_cached_days",
        lambda: ("2024-01-02", "2024-01-03", "2024-01-05"),
    )
    assert is_trading_day("2024-01-02") is True
    assert is_trading_day("2024-01-04") is False
    assert trading_days("2024-01-01", "2024-01-05") == [
        "2024-01-02",
        "2024-01-03",
        "2024-01-05",
    ]


def test_get_macro_tool_offline_shape(monkeypatch):
    import tools.get_macro_data as mod

    def fake_pmi():
        return (
            [
                {"indicator": "pmi_mfg", "asof": "2024-05-01", "value": 50.1, "unit": "index_point", "frequency": "monthly"},
                {"indicator": "pmi_mfg", "asof": "2024-06-01", "value": 50.5, "unit": "index_point", "frequency": "monthly"},
                {"indicator": "pmi_non_mfg", "asof": "2024-06-01", "value": 51.0, "unit": "index_point", "frequency": "monthly"},
            ],
            "akshare.macro_china_pmi",
        )

    monkeypatch.setattr(mod, "_fetch_pmi", fake_pmi)
    tool = mod.GetMacroDataTool()
    raw = tool.execute({"indicator": "pmi", "persist": False, "limit": 10}, None)
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["_meta"]["frequency"] == "monthly"
    assert payload["_meta"]["unit"] == "index_point"
    assert payload["data"]["latest"]["value"] in (50.5, 51.0)
