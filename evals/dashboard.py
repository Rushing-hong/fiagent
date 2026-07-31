"""Evaluation dashboard: latency, tools, PIT, cost proxy."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from evals.metrics import EVAL_LOG, EvalRecorder


def _cost_proxy_ms(latency_ms: int, tool_calls: int) -> float:
    """Rough relative cost index (not USD): latency + 2s per tool call."""
    return latency_ms + tool_calls * 2000


def build_dashboard(log_path: Path = EVAL_LOG) -> dict[str, Any]:
    rows = EvalRecorder(log_path).load_all()
    if not rows:
        return {"status": "empty", "message": "无评估记录。设置 FIAGENT_EVAL=1 后运行对话。"}

    by_variant: dict[str, list[dict]] = {}
    all_tools: Counter[str] = Counter()
    total_pit = 0
    total_tools = 0

    for r in rows:
        v = r.get("variant", "?")
        by_variant.setdefault(v, []).append(r)
        extra = r.get("extra") or {}
        total_pit += int(extra.get("pit_unsafe_hits", 0))
        tc = int(extra.get("tool_calls", r.get("tool_rounds", 0)))
        total_tools += tc
        for name in extra.get("tool_sequence") or []:
            all_tools[name] += 1

    variant_stats = {}
    for v, items in by_variant.items():
        n = len(items)
        latencies = [i.get("latency_ms", 0) for i in items]
        tools = [int((i.get("extra") or {}).get("tool_calls", i.get("tool_rounds", 0))) for i in items]
        pits = [int((i.get("extra") or {}).get("pit_unsafe_hits", 0)) for i in items]
        costs = [_cost_proxy_ms(latencies[i], tools[i]) for i in range(n)]
        variant_stats[v] = {
            "runs": n,
            "success_rate": sum(1 for i in items if i.get("success")) / n,
            "avg_latency_ms": sum(latencies) / n,
            "avg_tool_calls": sum(tools) / n,
            "pit_unsafe_rate": sum(1 for p in pits if p > 0) / n,
            "avg_cost_index": sum(costs) / n,
        }

    baseline = variant_stats.get("A", {}).get("avg_cost_index")
    cost_vs_fast = {}
    if baseline:
        for v, st in variant_stats.items():
            cost_vs_fast[v] = round(st["avg_cost_index"] / baseline, 2)

    return {
        "status": "ok",
        "total_runs": len(rows),
        "by_variant": variant_stats,
        "cost_index_vs_fast": cost_vs_fast,
        "aggregate": {
            "total_tool_calls": total_tools,
            "total_pit_unsafe_hits": total_pit,
            "top_tools": all_tools.most_common(15),
        },
        "interpretation": {
            "variants": "A=Fast单Agent, B=Research团队, D=Committee, R=TradeReview",
            "pit_unsafe_rate": "含 pit_safe:false 的工具返回占比（越低越好）",
            "cost_index": "latency_ms + 2000×tool_calls 的相对成本代理",
        },
    }


def format_dashboard_markdown(dashboard: dict[str, Any]) -> str:
    if dashboard.get("status") == "empty":
        return dashboard.get("message", "无数据")
    lines = ["# Agent 评估看板", ""]
    lines.append(f"总运行次数: {dashboard['total_runs']}")
    lines.append("")
    lines.append("## 按 Variant")
    for v, st in dashboard.get("by_variant", {}).items():
        lines.append(
            f"- **{v}**: runs={st['runs']}, success={st['success_rate']:.0%}, "
            f"latency={st['avg_latency_ms']:.0f}ms, tools={st['avg_tool_calls']:.1f}, "
            f"PIT问题率={st['pit_unsafe_rate']:.0%}, cost_index={st['avg_cost_index']:.0f}"
        )
    vs = dashboard.get("cost_index_vs_fast") or {}
    if vs:
        lines.append("")
        lines.append("## 相对 Fast 成本倍数")
        for v, mult in vs.items():
            lines.append(f"- {v}: {mult}x")
    agg = dashboard.get("aggregate") or {}
    top = agg.get("top_tools") or []
    if top:
        lines.append("")
        lines.append("## 高频工具 Top")
        for name, cnt in top[:10]:
            lines.append(f"- `{name}`: {cnt}")
    return "\n".join(lines)
