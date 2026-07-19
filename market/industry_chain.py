"""A-share industry chain knowledge graph — load & query helpers.

Expected input schemas (``load_graph`` accepts any of these):

1. **Edge list** — JSON array or JSONL (one object per line)::

       [
         {"source": "隆基绿能", "target": "单晶硅片", "relation": "主营产品"},
         {"source": "多晶硅", "target": "单晶硅片", "relation": "上游材料"}
       ]

   Aliases for endpoints / relation are normalized automatically, e.g.
   ``from_entity`` / ``to_entity`` / ``rel`` (ChainKnowledgeGraph product edges),
   ``company_name`` + ``product_name``, ``company_name`` + ``industry_name``.

2. **Structured dict**::

       {
         "companies": {"601012.SH": {"name": "隆基绿能"}},
         "products": {"单晶硅片": {}},
         "industries": {"光伏": {}},
         "edges": [
           {"source": "隆基绿能", "target": "单晶硅片", "relation": "主营产品"}
         ]
       }

   Entity maps are optional metadata; traversal uses ``edges`` only.

3. **ChainKnowledgeGraph bundle** — same dict shape after merging upstream JSONL
   files from https://github.com/liuhuanyong/ChainKnowledgeGraph (``data/`` folder).
   Place a single merged file at ``data/industry_chain.json`` or set env
   ``FIAGENT_INDUSTRY_CHAIN_PATH``.

When the file is missing, ``load_graph`` returns ``loaded=False`` with empty
``edges`` and an ``install_hint`` string — no network download at import time.
"""

from __future__ import annotations

import json
import os
from collections import deque
from pathlib import Path
from typing import Any, Literal

from paths import DATA_DIR, PROJECT_ROOT

Direction = Literal["both", "out", "in", "upstream", "downstream"]

_INSTALL_HINT = (
    "产业链图谱未安装：从 https://github.com/liuhuanyong/ChainKnowledgeGraph "
    "下载 data/*.json（或自行合并为 edges 列表），保存为 "
    f"{DATA_DIR / 'industry_chain.json'}，"
    "或设置环境变量 FIAGENT_INDUSTRY_CHAIN_PATH 指向本地 JSON。"
)

_DEFAULT_REL_UP = frozenset({"上游材料", "上游", "upstream", "parent", "上级行业", "上级"})
_DEFAULT_REL_DOWN = frozenset({"下游产品", "下游", "downstream", "child", "下游应用"})


def default_graph_path() -> Path:
    env = os.environ.get("FIAGENT_INDUSTRY_CHAIN_PATH", "").strip()
    if env:
        p = Path(env)
        return p if p.is_absolute() else PROJECT_ROOT / p
    return DATA_DIR / "industry_chain.json"


def _strip(s: Any) -> str:
    return str(s or "").strip()


def _normalize_relation(rel: str) -> str:
    return _strip(rel) or "related"


def _edge_endpoints(raw: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    """Map heterogeneous edge records to (source, target, relation, extra)."""
    relation = _normalize_relation(
        raw.get("relation")
        or raw.get("rel")
        or raw.get("type")
        or raw.get("label")
    )
    extra = {k: v for k, v in raw.items() if k not in {
        "source", "target", "relation", "rel", "type", "label",
        "from_entity", "to_entity",
        "company_code", "company_name", "product_name",
        "industry_code", "industry_name",
    }}

    if raw.get("source") and raw.get("target"):
        return _strip(raw["source"]), _strip(raw["target"]), relation, extra

    if raw.get("from_entity") and raw.get("to_entity"):
        return _strip(raw["from_entity"]), _strip(raw["to_entity"]), relation, extra

    company = _strip(raw.get("company_name") or raw.get("company"))
    product = _strip(raw.get("product_name") or raw.get("product"))
    industry = _strip(raw.get("industry_name") or raw.get("industry"))
    code = _strip(raw.get("company_code") or raw.get("code"))

    if company and product:
        if code:
            extra.setdefault("company_code", code)
        return company, product, relation or "主营产品", extra

    if company and industry:
        if code:
            extra.setdefault("company_code", code)
        ic = _strip(raw.get("industry_code"))
        if ic:
            extra.setdefault("industry_code", ic)
        return company, industry, relation or "所属行业", extra

    if product and industry:
        return product, industry, relation, extra

    raise ValueError(f"无法解析边记录: {raw!r}")


def _parse_raw_content(text: str) -> Any:
    text = text.strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        rows: list[dict[str, Any]] = []
        for line_no, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"第 {line_no} 行 JSON 解析失败: {exc}") from exc
            if isinstance(obj, dict):
                rows.append(obj)
        return rows


def _collect_edges(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        raw_edges = payload
    elif isinstance(payload, dict):
        if "edges" in payload:
            raw_edges = payload.get("edges") or []
        elif "links" in payload:
            raw_edges = payload.get("links") or []
        else:
            raw_edges = []
    else:
        raise ValueError(f"不支持的图谱格式: {type(payload).__name__}")

    edges: list[dict[str, Any]] = []
    for raw in raw_edges:
        if not isinstance(raw, dict):
            continue
        try:
            src, tgt, rel, extra = _edge_endpoints(raw)
        except ValueError:
            continue
        if not src or not tgt:
            continue
        edge = {"source": src, "target": tgt, "relation": rel}
        edge.update(extra)
        edges.append(edge)
    return edges


def _entity_maps(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key in ("companies", "products", "industries"):
        block = payload.get(key)
        if isinstance(block, dict):
            for ent_id, meta in block.items():
                name = _strip(ent_id)
                if not name:
                    continue
                info = dict(meta) if isinstance(meta, dict) else {}
                info.setdefault("kind", key[:-1] if key.endswith("ies") else key.rstrip("s"))
                display = _strip(info.get("name"))
                canonical = display or name
                out.setdefault(canonical, {**info, "kind": info["kind"]})
                if name != canonical:
                    out.setdefault(name, {**info, "alias_of": canonical})
                code = _strip(info.get("code") or info.get("company_code"))
                if code:
                    out.setdefault(code.upper(), {**info, "alias_of": canonical})
                    out.setdefault(code.casefold(), {**info, "alias_of": canonical})
    return out


def _build_index(edges: list[dict[str, Any]], entities: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out_adj: dict[str, list[dict[str, Any]]] = {}
    in_adj: dict[str, list[dict[str, Any]]] = {}
    entity_set: set[str] = set(entities.keys())

    def _touch(name: str) -> None:
        entity_set.add(name)
        out_adj.setdefault(name, [])
        in_adj.setdefault(name, [])

    for e in edges:
        src, tgt = e["source"], e["target"]
        _touch(src)
        _touch(tgt)
        out_adj[src].append(e)
        in_adj[tgt].append(e)

    # code → company name alias index
    aliases: dict[str, str] = {}
    for name, meta in entities.items():
        if meta.get("alias_of"):
            aliases[name.casefold()] = meta["alias_of"]
        code = _strip(meta.get("code") or meta.get("company_code"))
        canonical = meta.get("alias_of") or name
        if code:
            aliases[code.upper()] = canonical
            aliases[code.casefold()] = canonical

    for e in edges:
        code = _strip(e.get("company_code"))
        if code:
            aliases.setdefault(code.upper(), e["source"])
            aliases.setdefault(code.casefold(), e["source"])

    return {
        "out_adj": out_adj,
        "in_adj": in_adj,
        "entities": entities,
        "entity_set": entity_set,
        "aliases": aliases,
    }


def _resolve_entity(graph: dict[str, Any], query: str) -> str | None:
    q = _strip(query)
    if not q:
        return None
    idx = graph.get("_index") or {}
    entity_set: set[str] = idx.get("entity_set") or set()
    aliases: dict[str, str] = idx.get("aliases") or {}

    hit = aliases.get(q.upper()) or aliases.get(q.casefold())
    if hit:
        return hit

    if q in entity_set:
        return q

    q_cf = q.casefold()
    partial = [n for n in entity_set if q_cf in n.casefold() or n.casefold() in q_cf]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        # prefer exact substring match length
        partial.sort(key=lambda n: (abs(len(n) - len(q)), n))
        return partial[0]
    return None


def _empty_graph(path: Path, *, error: str, message: str) -> dict[str, Any]:
    return {
        "loaded": False,
        "path": str(path),
        "edges": [],
        "entities": {},
        "stats": {"edge_count": 0, "entity_count": 0},
        "error": error,
        "message": message,
        "install_hint": _INSTALL_HINT,
        "_index": _build_index([], {}),
    }


def load_graph(path: str | Path | None = None) -> dict[str, Any]:
    """Load industry chain JSON/JSONL from *path* (default: env or data/industry_chain.json)."""
    p = Path(path) if path is not None else default_graph_path()
    if not p.exists():
        return _empty_graph(p, error="file_not_found", message=f"图谱文件不存在: {p}")

    try:
        text = p.read_text(encoding="utf-8")
        payload = _parse_raw_content(text)
    except OSError as exc:
        return _empty_graph(p, error="read_error", message=str(exc))
    except ValueError as exc:
        return _empty_graph(p, error="parse_error", message=str(exc))

    try:
        edges = _collect_edges(payload)
        entities = _entity_maps(payload)
    except ValueError as exc:
        return _empty_graph(p, error="parse_error", message=str(exc))

    entity_names = set(entities.keys())
    for e in edges:
        entity_names.add(e["source"])
        entity_names.add(e["target"])

    stats = {
        "edge_count": len(edges),
        "entity_count": len(entity_names),
        "relations": sorted({e["relation"] for e in edges}),
    }
    index = _build_index(edges, entities)

    return {
        "loaded": True,
        "path": str(p.resolve()),
        "edges": edges,
        "entities": entities,
        "stats": stats,
        "error": None,
        "message": None,
        "install_hint": None,
        "_index": index,
    }


def _direction_edges(
    graph: dict[str, Any],
    entity: str,
    direction: Direction,
) -> list[dict[str, Any]]:
    idx = graph.get("_index") or {}
    out_adj: dict[str, list] = idx.get("out_adj") or {}
    in_adj: dict[str, list] = idx.get("in_adj") or {}

    rel_up = _DEFAULT_REL_UP
    rel_down = _DEFAULT_REL_DOWN

    def _filter(edges: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
        if mode == "out":
            return list(edges)
        if mode == "in":
            return list(edges)
        if mode == "upstream":
            return [e for e in edges if e.get("relation") in rel_up]
        if mode == "downstream":
            return [e for e in edges if e.get("relation") in rel_down]
        return list(edges)

    d = direction.lower()
    if d in ("out", "downstream"):
        return _filter(out_adj.get(entity, []), d)
    if d in ("in", "upstream"):
        return _filter(in_adj.get(entity, []), d)

    seen: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for mode in ("out", "in"):
        for e in _filter(out_adj.get(entity, []) if mode == "out" else in_adj.get(entity, []), mode):
            key = (e["source"], e["target"], e["relation"])
            if key not in seen_keys:
                seen_keys.add(key)
                seen.append(e)
    return seen


def query_neighbors(
    graph: dict[str, Any],
    entity: str,
    direction: Direction = "both",
    limit: int = 20,
) -> dict[str, Any]:
    """Return direct neighbors of *entity* (fuzzy name/code match)."""
    resolved = _resolve_entity(graph, entity)
    if resolved is None:
        return {
            "query": entity,
            "resolved": None,
            "neighbors": [],
            "count": 0,
            "direction": direction,
        }

    edges = _direction_edges(graph, resolved, direction)
    neighbors: list[dict[str, Any]] = []
    for e in edges[: max(0, limit)]:
        if e["source"] == resolved:
            other = e["target"]
            role = "target"
        else:
            other = e["source"]
            role = "source"
        neighbors.append({
            "entity": other,
            "relation": e["relation"],
            "direction": role,
            "edge": e,
        })

    return {
        "query": entity,
        "resolved": resolved,
        "neighbors": neighbors,
        "count": len(neighbors),
        "direction": direction,
        "truncated": len(edges) > limit,
    }


def trace_chain(
    graph: dict[str, Any],
    start: str,
    max_depth: int = 3,
    limit: int = 50,
) -> dict[str, Any]:
    """BFS trace from *start* up to *max_depth* hops (both directions)."""
    resolved = _resolve_entity(graph, start)
    if resolved is None:
        return {
            "start": start,
            "resolved": None,
            "paths": [],
            "nodes": [],
            "depth_reached": 0,
        }

    max_depth = max(1, min(int(max_depth), 10))
    limit = max(1, min(int(limit), 500))

    idx = graph.get("_index") or {}
    out_adj = idx.get("out_adj") or {}
    in_adj = idx.get("in_adj") or {}

    # (node, depth, path_edges)
    queue: deque[tuple[str, int, list[dict[str, Any]]]] = deque([(resolved, 0, [])])
    visited: set[str] = {resolved}
    nodes: list[dict[str, Any]] = [{"entity": resolved, "depth": 0}]
    paths: list[dict[str, Any]] = []

    while queue and len(nodes) < limit:
        node, depth, path_edges = queue.popleft()
        if depth >= max_depth:
            continue

        for e in out_adj.get(node, []) + in_adj.get(node, []):
            nxt = e["target"] if e["source"] == node else e["source"]
            if nxt in visited:
                continue
            visited.add(nxt)
            new_path = path_edges + [e]
            nd = depth + 1
            nodes.append({"entity": nxt, "depth": nd})
            paths.append({
                "from": resolved,
                "to": nxt,
                "depth": nd,
                "edges": new_path,
            })
            if len(nodes) >= limit:
                break
            queue.append((nxt, nd, new_path))

    max_seen = max((n["depth"] for n in nodes), default=0)
    return {
        "start": start,
        "resolved": resolved,
        "max_depth": max_depth,
        "depth_reached": max_seen,
        "nodes": nodes,
        "paths": paths[:limit],
        "truncated": len(visited) > limit,
    }


def graph_stats(graph: dict[str, Any]) -> dict[str, Any]:
    """Summarize loaded graph."""
    stats = dict(graph.get("stats") or {})
    stats["loaded"] = bool(graph.get("loaded"))
    stats["path"] = graph.get("path")
    if not graph.get("loaded"):
        stats["install_hint"] = graph.get("install_hint") or _INSTALL_HINT
    return stats
