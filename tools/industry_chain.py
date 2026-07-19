"""Industry chain knowledge graph query tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from market.envelope import clamp_int, err, ok
from market.industry_chain import (
    default_graph_path,
    graph_stats,
    load_graph,
    query_neighbors,
    trace_chain,
)
from tools.base import BaseTool

_GRAPH_CACHE: dict[str, Any] | None = None
_GRAPH_CACHE_KEY: tuple[str, int, int] | None = None


def _graph_cache_key(path: Path) -> tuple[str, int, int]:
    """Use path + stat signature so unchanged large graphs are not reparsed."""
    resolved = path.resolve()
    try:
        stat = resolved.stat()
        return str(resolved), stat.st_mtime_ns, stat.st_size
    except OSError:
        return str(resolved), -1, -1


def _get_graph(force_reload: bool = False) -> dict[str, Any]:
    global _GRAPH_CACHE, _GRAPH_CACHE_KEY
    path = default_graph_path()
    key = _graph_cache_key(path)
    if force_reload or _GRAPH_CACHE is None or _GRAPH_CACHE_KEY != key:
        _GRAPH_CACHE = load_graph(path)
        _GRAPH_CACHE_KEY = key
    return _GRAPH_CACHE


class QueryIndustryChainTool(BaseTool):
    name = "query_industry_chain"
    summary = "产业链图谱邻居/路径/统计"
    description = (
        "查询 A 股产业链知识图谱（本地 JSON）。"
        "action=neighbors 查直接相邻；path/trace 沿链追溯；stats 看规模。"
        "需 data/industry_chain.json 或 FIAGENT_INDUSTRY_CHAIN_PATH。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "entity": {
                "type": "string",
                "description": "公司名、产品名、行业名或 A 股代码（如 601012.SH）",
            },
            "query": {
                "type": "string",
                "description": "同 entity（二选一）",
            },
            "action": {
                "type": "string",
                "enum": ["neighbors", "path", "trace", "stats"],
                "default": "neighbors",
                "description": "neighbors | path/trace | stats",
            },
            "direction": {
                "type": "string",
                "enum": ["both", "out", "in", "upstream", "downstream"],
                "default": "both",
                "description": "neighbors 方向过滤",
            },
            "depth": {
                "type": "integer",
                "default": 3,
                "description": "path/trace 最大跳数 1-10",
            },
            "limit": {
                "type": "integer",
                "default": 20,
                "description": "返回条数上限",
            },
        },
    }
    is_readonly = True

    def execute(self, args: dict[str, Any], ctx) -> str:
        action = str(args.get("action") or "neighbors").strip().lower()
        if action == "trace":
            action = "path"

        graph = _get_graph()
        loaded = bool(graph.get("loaded"))
        edge_count = int((graph.get("stats") or {}).get("edge_count") or 0)
        empty = not loaded or edge_count == 0

        base_note = None
        quality = "normal"
        if empty:
            quality = "degraded"
            base_note = graph.get("install_hint") or graph.get("message") or "产业链图谱未加载"

        if action == "stats":
            data = graph_stats(graph)
            if empty:
                return ok(
                    data,
                    market="a_share",
                    source="local",
                    tool="query_industry_chain",
                    quality=quality,
                    note=base_note,
                )
            return ok(
                data,
                market="a_share",
                source="local",
                tool="query_industry_chain",
            )

        entity = str(args.get("entity") or args.get("query") or "").strip()
        if not entity:
            return err("需要 entity 或 query 参数", note=base_note)

        limit = clamp_int(args.get("limit"), 20, 1, 500)

        if action == "neighbors":
            direction = str(args.get("direction") or "both").strip().lower()
            if empty:
                return ok(
                    {
                        "action": "neighbors",
                        "entity": entity,
                        "neighbors": [],
                        "count": 0,
                        "graph_loaded": False,
                    },
                    market="a_share",
                    source="local",
                    tool="query_industry_chain",
                    quality=quality,
                    note=base_note,
                )
            result = query_neighbors(graph, entity, direction=direction, limit=limit)
            if result.get("resolved") is None:
                return ok(
                    {**result, "action": "neighbors", "graph_path": graph.get("path")},
                    market="a_share",
                    source="local",
                    tool="query_industry_chain",
                    quality="degraded",
                    note=f"未在图谱中找到实体: {entity}",
                )
            return ok(
                {**result, "action": "neighbors", "graph_path": graph.get("path")},
                market="a_share",
                source="local",
                tool="query_industry_chain",
            )

        if action == "path":
            depth = clamp_int(args.get("depth"), 3, 1, 10)
            if empty:
                return ok(
                    {
                        "action": "path",
                        "start": entity,
                        "nodes": [],
                        "paths": [],
                        "graph_loaded": False,
                    },
                    market="a_share",
                    source="local",
                    tool="query_industry_chain",
                    quality=quality,
                    note=base_note,
                )
            result = trace_chain(graph, entity, max_depth=depth, limit=limit)
            if result.get("resolved") is None:
                return ok(
                    {**result, "action": "path", "graph_path": graph.get("path")},
                    market="a_share",
                    source="local",
                    tool="query_industry_chain",
                    quality="degraded",
                    note=f"未在图谱中找到实体: {entity}",
                )
            return ok(
                {**result, "action": "path", "graph_path": graph.get("path")},
                market="a_share",
                source="local",
                tool="query_industry_chain",
            )

        return err(f"未知 action: {action}", note=base_note)
