"""Regression tests for low-risk performance optimizations."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd

from skills.registry import SkillRegistry
from tools.base import ToolRegistry


def test_tool_router_narrows_clear_request_and_keeps_ambiguous_request(monkeypatch):
    from core.tool_routing import select_tool_names

    monkeypatch.setenv("FIAGENT_TOOL_ROUTING", "on")

    available = {
        "load_skill", "search_symbol", "get_market_data", "calc_dcf",
        "get_financial_statements", "run_backtest", "web_search",
        "get_option_chain", "get_macro_data", "get_fund_flow",
        "get_limit_board", "query_industry_chain", "edit", "read", "write",
    }
    selected = select_tool_names(
        [{"role": "user", "content": "分析茅台财报并做 DCF 估值"}],
        available,
    )
    assert selected is not None
    assert "calc_dcf" in selected
    assert "get_financial_statements" in selected
    assert "run_backtest" not in selected

    assert select_tool_names(
        [{"role": "user", "content": "帮我分析一下茅台"}],
        available,
    ) is None


def test_tool_router_can_be_disabled(monkeypatch):
    from core.tool_routing import select_tool_names

    monkeypatch.setenv("FIAGENT_TOOL_ROUTING", "off")
    assert select_tool_names(
        [{"role": "user", "content": "做一个量化回测"}],
        {"load_skill", "run_backtest"},
    ) is None


def test_tool_router_is_cache_first_by_default(monkeypatch):
    from core.tool_routing import select_tool_names

    monkeypatch.delenv("FIAGENT_TOOL_ROUTING", raising=False)
    assert select_tool_names(
        [{"role": "user", "content": "做一个量化回测"}],
        {"load_skill", "run_backtest", "calc_dcf"},
    ) is None


def test_prompt_cache_usage_normalizes_provider_shapes():
    from core.llm.cache_metrics import normalize_cache_usage

    assert normalize_cache_usage({
        "prompt_tokens": 100,
        "prompt_cache_hit_tokens": 80,
        "prompt_cache_miss_tokens": 20,
    }) == {
        "prompt_tokens": 100,
        "hit_tokens": 80,
        "miss_tokens": 20,
        "write_tokens": 0,
    }
    assert normalize_cache_usage({
        "input_tokens": 120,
        "input_tokens_details": {"cached_tokens": 90},
        "cache_write_tokens": 10,
    }) == {
        "prompt_tokens": 120,
        "hit_tokens": 90,
        "miss_tokens": 30,
        "write_tokens": 10,
    }
    assert normalize_cache_usage({
        "input_tokens": 10,
        "cache_read_input_tokens": 80,
        "cache_creation_input_tokens": 20,
    }) == {
        "prompt_tokens": 110,
        "hit_tokens": 80,
        "miss_tokens": 30,
        "write_tokens": 20,
    }


def test_prompt_cache_metrics_aggregate_and_reset():
    from core.llm.cache_metrics import (
        format_cache_metrics,
        get_cache_metrics,
        record_cache_usage,
        reset_cache_metrics,
    )

    reset_cache_metrics()
    record_cache_usage("deepseek", "model-a", {
        "prompt_tokens": 100,
        "prompt_cache_hit_tokens": 75,
        "prompt_cache_miss_tokens": 25,
    })
    snapshot = get_cache_metrics()
    assert snapshot["totals"]["requests"] == 1
    assert snapshot["totals"]["hit_rate"] == 0.75
    assert "75.0%" in format_cache_metrics()
    reset_cache_metrics()
    assert get_cache_metrics()["totals"]["requests"] == 0


def test_env_helpers_fallback_and_clamp(monkeypatch):
    from core.config import env_float, env_int

    monkeypatch.setenv("FIAGENT_TEST_INT", "not-a-number")
    monkeypatch.setenv("FIAGENT_TEST_FLOAT", "nan")
    assert env_int("FIAGENT_TEST_INT", 7, minimum=1, maximum=9) == 7
    assert env_float("FIAGENT_TEST_FLOAT", 2.5, minimum=1, maximum=3) == 2.5

    monkeypatch.setenv("FIAGENT_TEST_INT", "999")
    monkeypatch.setenv("FIAGENT_TEST_FLOAT", "-10")
    assert env_int("FIAGENT_TEST_INT", 7, maximum=9) == 9
    assert env_float("FIAGENT_TEST_FLOAT", 2.5, minimum=1) == 1


def test_tool_schema_compaction_preserves_source_and_constraints():
    from tools.base import BaseTool

    class DemoTool(BaseTool):
        name = "demo"
        description = "D" * 300
        parameters = {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "P" * 300,
                    "default": 5,
                    "minimum": 1,
                }
            },
            "required": ["count"],
        }

        def execute(self, args, ctx):
            return "ok"

    schema = DemoTool().to_openai_schema()
    prop = schema["function"]["parameters"]["properties"]["count"]
    assert len(schema["function"]["description"]) <= 96
    assert len(prop["description"]) <= 64
    assert "default" not in prop
    assert prop["minimum"] == 1
    assert DemoTool.parameters["properties"]["count"]["default"] == 5


def test_tool_schema_compaction_preserves_schema_keyword_parameter_names():
    from tools.base import BaseTool

    class KeywordNamedTool(BaseTool):
        name = "keyword_named"
        description = "demo"
        parameters = {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "A parameter named description",
                },
                "default": {"type": "integer", "default": 3},
                "title": {"type": "string", "title": "Display title"},
                "examples": {
                    "type": "array",
                    "items": {"type": "string"},
                    "examples": [["one"]],
                },
                "$schema": {"type": "boolean"},
            },
            "required": ["description"],
        }

        def execute(self, args, ctx):
            return "ok"

    properties = KeywordNamedTool().to_openai_schema()["function"]["parameters"][
        "properties"
    ]
    assert set(properties) == {"description", "default", "title", "examples", "$schema"}
    assert properties["description"] == {
        "type": "string",
        "description": "A parameter named description",
    }
    assert properties["default"] == {"type": "integer"}
    assert properties["title"] == {"type": "string"}
    assert properties["examples"] == {
        "type": "array",
        "items": {"type": "string"},
    }
    assert properties["$schema"] == {"type": "boolean"}


def test_save_skill_description_parameter_remains_a_schema_object():
    from tools.skills import SaveSkillTool

    properties = SaveSkillTool().to_openai_schema()["function"]["parameters"][
        "properties"
    ]
    assert properties["description"] == {
        "type": "string",
        "description": "skill 描述（写入 frontmatter）",
    }


def test_tool_schema_compaction_preserves_object_literals_in_enum_and_const():
    from tools.base import BaseTool

    enum_value = {
        "description": "ordinary data",
        "default": 3,
        "title": "literal title",
        "examples": ["keep me"],
        "$schema": "not a schema keyword here",
    }
    const_value = {
        "description": "also ordinary data",
        "default": False,
    }

    class LiteralValueTool(BaseTool):
        name = "literal_value"
        description = "demo"
        parameters = {
            "type": "object",
            "properties": {
                "choice": {"enum": [enum_value]},
                "payload": {"const": const_value},
            },
        }

        def execute(self, args, ctx):
            return "ok"

    properties = LiteralValueTool().to_openai_schema()["function"]["parameters"][
        "properties"
    ]
    assert properties["choice"]["enum"] == [enum_value]
    assert properties["payload"]["const"] == const_value
    assert properties["choice"]["enum"][0] is not enum_value
    assert properties["payload"]["const"] is not const_value


def test_all_builtin_tool_parameter_schemas_are_valid():
    from pathlib import Path

    from jsonschema import Draft202012Validator

    from tools.base import ToolRegistry

    project_root = Path(__file__).resolve().parents[1]
    schemas = ToolRegistry(project_root / "tools").build_schemas(None)
    assert schemas

    for tool in schemas:
        Draft202012Validator.check_schema(tool["function"]["parameters"])


def test_backtest_schema_exposes_strategy_parameter_names():
    from tools.backtest import RunBacktestTool, _normalize_strategy_params

    params = RunBacktestTool().to_openai_schema()["function"]["parameters"]
    strategy_params = params["properties"]["strategy_params"]
    assert set(strategy_params["properties"]) == {
        "fast", "slow", "period", "oversold", "overbought", "window",
    }
    assert strategy_params["additionalProperties"] is False
    assert _normalize_strategy_params("momentum", {"momentum_window": 20}) == {
        "window": 20,
    }
    assert _normalize_strategy_params("momentum", {"lookback": 10}) == {
        "window": 10,
    }


def test_http_throttle_sleeps_outside_global_lock(monkeypatch):
    import market.http as mod

    class TrackingLock:
        held = False

        def __enter__(self):
            self.held = True

        def __exit__(self, *args):
            self.held = False

    lock = TrackingLock()
    monkeypatch.setattr(mod, "_throttle_lock", lock)
    monkeypatch.setattr(mod, "_last_request", {"host": 10.0})
    monkeypatch.setattr(mod.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(mod.random, "uniform", lambda _a, _b: 0.0)

    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        assert lock.held is False
        sleeps.append(seconds)

    monkeypatch.setattr(mod.time, "sleep", fake_sleep)
    mod._wait("host", 1.0)
    assert sleeps == [1.0]


def test_http_min_interval_uses_safe_env_parser(monkeypatch):
    from market.http import resolve_min_interval

    monkeypatch.setenv("FIAGENT_TEST_INTERVAL", "0.25")
    assert resolve_min_interval("FIAGENT_TEST_INTERVAL", 1.0) == 0.25
    monkeypatch.setenv("FIAGENT_TEST_INTERVAL", "invalid")
    assert resolve_min_interval("FIAGENT_TEST_INTERVAL", 1.0) == 1.0


def test_tool_result_success_reads_ok_envelope():
    from core.loop import _tool_result_succeeded

    assert _tool_result_succeeded('{"ok": true, "data": {}}') is True
    assert _tool_result_succeeded('{"ok": false, "error": "upstream"}') is False
    assert _tool_result_succeeded('{"status": "error", "error": "blocked"}') is False


def test_tool_registry_reuses_unchanged_modules(tmp_path: Path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    counter = tmp_path / "imports.txt"
    tool_file = tools_dir / "demo.py"
    source = f"""
from pathlib import Path
from tools.base import BaseTool
_p = Path({str(counter)!r})
_p.write_text(_p.read_text() + "x" if _p.exists() else "x")
class DemoTool(BaseTool):
    name = "demo"
    description = "demo"
    def execute(self, args, ctx):
        return "ok"
"""
    tool_file.write_text(source, encoding="utf-8")

    registry = ToolRegistry(tools_dir)
    assert counter.read_text() == "x"
    registry.refresh()
    assert counter.read_text() == "x"

    time.sleep(0.002)
    tool_file.write_text(source + "\n# changed\n", encoding="utf-8")
    os.utime(tool_file, None)
    registry.refresh()
    assert counter.read_text() == "xx"


def test_skill_registry_reuses_metadata_and_refreshes_changes(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "demo"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\nname: demo\ndescription: first\n---\n\nbody\n",
        encoding="utf-8",
    )

    registry = SkillRegistry(skills_dir)
    first = registry.get("demo")
    registry.refresh()
    assert registry.get("demo") is first

    time.sleep(0.002)
    skill_file.write_text(
        "---\nname: demo\ndescription: changed description\n---\n\nbody\n",
        encoding="utf-8",
    )
    os.utime(skill_file, None)
    registry.refresh()
    assert registry.get("demo") is not first
    assert registry.get("demo").description == "changed description"


def test_industry_graph_cache_avoids_reparse(tmp_path: Path, monkeypatch):
    import tools.industry_chain as mod

    graph_file = tmp_path / "graph.json"
    graph_file.write_text("[]", encoding="utf-8")
    calls: list[Path] = []

    def fake_load(path):
        calls.append(Path(path))
        return {"loaded": True, "path": str(path), "stats": {"edge_count": 0}}

    monkeypatch.setattr(mod, "default_graph_path", lambda: graph_file)
    monkeypatch.setattr(mod, "load_graph", fake_load)
    mod._GRAPH_CACHE = None
    mod._GRAPH_CACHE_KEY = None

    mod._get_graph()
    mod._get_graph()
    assert len(calls) == 1

    time.sleep(0.002)
    graph_file.write_text("[{}]", encoding="utf-8")
    os.utime(graph_file, None)
    mod._get_graph()
    assert len(calls) == 2


def test_dataframe_rows_fast_path_preserves_schema():
    from market.loaders import _rows_from_df

    df = pd.DataFrame(
        {
            "日期": ["2026-01-02 15:00:00", "2026-01-05 15:00:00"],
            "开盘": [10, 11],
            "收盘": [11, 12],
            "最高": [12, 13],
            "最低": [9, 10],
            "成交量": [100, 200],
        }
    )
    rows = _rows_from_df(df, date_col="日期")
    assert rows == [
        {
            "trade_date": "2026-01-02",
            "open": 10.0,
            "close": 11.0,
            "high": 12.0,
            "low": 9.0,
            "volume": 100.0,
        },
        {
            "trade_date": "2026-01-05",
            "open": 11.0,
            "close": 12.0,
            "high": 13.0,
            "low": 10.0,
            "volume": 200.0,
        },
    ]
