"""Tests for industry chain graph loader and query tool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from market.industry_chain import (
    load_graph,
    query_neighbors,
    trace_chain,
)
from tools.industry_chain import QueryIndustryChainTool

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "industry_chain_sample.json"


def test_load_graph_from_fixture():
    g = load_graph(FIXTURE)
    assert g["loaded"] is True
    assert g["stats"]["edge_count"] == 12
    assert g["stats"]["entity_count"] >= 8
    assert "上游材料" in g["stats"]["relations"]


def test_load_graph_missing_file(tmp_path: Path):
    g = load_graph(tmp_path / "nope.json")
    assert g["loaded"] is False
    assert g["error"] == "file_not_found"
    assert g["edges"] == []
    assert "ChainKnowledgeGraph" in (g.get("install_hint") or "")


def test_query_neighbors_by_name():
    g = load_graph(FIXTURE)
    r = query_neighbors(g, "隆基绿能", direction="out", limit=10)
    assert r["resolved"] == "隆基绿能"
    names = {n["entity"] for n in r["neighbors"]}
    assert "单晶硅片" in names
    assert "光伏组件" in names


def test_query_neighbors_by_code():
    g = load_graph(FIXTURE)
    r = query_neighbors(g, "601012.SH", limit=10)
    assert r["resolved"] == "隆基绿能"


def test_query_neighbors_upstream_filter():
    g = load_graph(FIXTURE)
    r = query_neighbors(g, "单晶硅片", direction="upstream", limit=10)
    rels = {n["relation"] for n in r["neighbors"]}
    assert "上游材料" in rels


def test_trace_chain_from_product():
    g = load_graph(FIXTURE)
    r = trace_chain(g, "多晶硅", max_depth=2, limit=20)
    assert r["resolved"] == "多晶硅"
    entities = {n["entity"] for n in r["nodes"]}
    assert "工业硅" in entities
    assert "单晶硅片" in entities
    assert "通威股份" in entities


def test_trace_chain_depth_limit():
    g = load_graph(FIXTURE)
    r = trace_chain(g, "光伏", max_depth=1, limit=50)
    depths = {n["depth"] for n in r["nodes"]}
    assert depths <= {0, 1}


def test_tool_neighbors_ok():
    tool = QueryIndustryChainTool()
    # bypass cache by loading fixture path via monkeypatch on load_graph
    import tools.industry_chain as mod

    orig = mod.load_graph
    mod.load_graph = lambda path=None: orig(FIXTURE)
    mod._GRAPH_CACHE = None
    try:
        out = json.loads(tool.execute({"entity": "通威股份", "action": "neighbors"}, None))
        assert out["ok"] is True
        assert out["quality"] == "normal"
        assert out["data"]["resolved"] == "通威股份"
    finally:
        mod.load_graph = orig
        mod._GRAPH_CACHE = None


def test_tool_stats_degraded_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FIAGENT_INDUSTRY_CHAIN_PATH", str(tmp_path / "missing.json"))
    import tools.industry_chain as mod

    mod._GRAPH_CACHE = None
    tool = QueryIndustryChainTool()
    out = json.loads(tool.execute({"action": "stats"}, None))
    assert out["ok"] is True
    assert out["quality"] == "degraded"
    assert "ChainKnowledgeGraph" in (out.get("note") or "")


def test_tool_path_unknown_entity():
    import tools.industry_chain as mod

    orig = mod.load_graph
    mod.load_graph = lambda path=None: orig(FIXTURE)
    mod._GRAPH_CACHE = None
    try:
        tool = QueryIndustryChainTool()
        out = json.loads(tool.execute({"entity": "不存在公司", "action": "path"}, None))
        assert out["ok"] is True
        assert out["quality"] == "degraded"
        assert out["data"]["resolved"] is None
    finally:
        mod.load_graph = orig
        mod._GRAPH_CACHE = None
