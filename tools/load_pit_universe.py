"""点位可交易股票池：读写本地 universe 快照。"""

from __future__ import annotations

from typing import Any

from market.envelope import err, ok
from market.research_store import get_store
from tools.base import BaseTool


class LoadPitUniverseTool(BaseTool):
    name = "load_pit_universe"
    summary = "加载点位可交易池快照"
    description = (
        "从本地 research.db 读取 asof 日或之前最近的可交易池快照。"
        "需先用 build_tradable_universe(save_snapshot=true) 累积历史。"
        "无快照时无法凭空回放全市场点位成分。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "asof": {"type": "string", "description": "YYYY-MM-DD"},
            "name": {"type": "string", "default": "default"},
            "list_only": {
                "type": "boolean",
                "default": False,
                "description": "仅列出已有快照日期",
            },
        },
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        name = str(args.get("name") or "default")
        store = get_store()
        if bool(args.get("list_only")):
            rows = store.list_universes(name=name)
            return ok(
                {"name": name, "snapshots": rows, "count": len(rows)},
                market="a_share",
                source="local",
                tool="load_pit_universe",
            )
        asof = str(args.get("asof") or "").strip()
        if not asof:
            return err("需要 asof，或 list_only=true")
        pit = store.load_universe_pit(asof, name=name)
        if pit is None:
            return err(
                f"无 name={name} 的 universe 快照；请先 build_tradable_universe(save_snapshot=true)"
            )
        return ok(
            {
                **pit,
                "count": len(pit["codes"]),
                "note": "点位成分来自本地累积快照，非交易所官方历史名单回放",
            },
            market="a_share",
            source="local",
            tool="load_pit_universe",
            quality="degraded",
        )
