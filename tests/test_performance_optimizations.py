"""Regression tests for low-risk performance optimizations."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd

from skills.registry import SkillRegistry
from tools.base import ToolRegistry


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
