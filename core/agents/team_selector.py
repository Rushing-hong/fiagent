"""Dynamic team selection from workflow definitions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from paths import PROJECT_ROOT

WORKFLOWS_DIR = PROJECT_ROOT / "workflows"

_RESEARCHER_KEYS = frozenset({
    "market_regime", "company_research", "quant_research",
})


@dataclass
class TeamPlan:
    workflow_id: str
    researchers: list[str] = field(default_factory=list)
    include_red_team: bool = True
    description: str = ""

    @property
    def all_researchers(self) -> list[str]:
        return list(self.researchers)


def _load_workflows() -> list[dict]:
    if not WORKFLOWS_DIR.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(WORKFLOWS_DIR.glob("*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def _match_workflow(query: str, workflows: list[dict]) -> dict | None:
    q = query or ""
    for wf in workflows:
        for trig in wf.get("triggers") or []:
            if trig and re.search(re.escape(trig), q, re.I):
                return wf
    return None


def _heuristic_researchers(query: str) -> list[str]:
    q = (query or "").lower()
    team: set[str] = set()

    market_kw = ("拥挤", "板块", "行业", "市场", "风格", "北向", "宏观", "etf", "融资融券", "涨跌停")
    company_kw = ("财报", "估值", "dcf", "基本面", "公司", "茅台", "宁德", "买入", "是否值得")
    quant_kw = ("回测", "因子", "量化", "策略", "ic", "双均线", "夏普", "walk-forward")

    if any(k in q for k in market_kw):
        team.add("market_regime")
    if any(k in q for k in company_kw) or re.search(r"\d{6}", q):
        team.add("company_research")
    if any(k in q for k in quant_kw):
        team.add("quant_research")

    if not team:
        return ["market_regime", "company_research", "quant_research"]
    return sorted(team)


def select_team(
    query: str,
    *,
    mode: str = "research",
    include_red_team: bool | None = None,
) -> TeamPlan:
    """Pick researcher subset + red-team flag from workflow or heuristics."""
    workflows = _load_workflows()
    wf = _match_workflow(query, workflows)

    if wf:
        researchers = [
            a for a in (wf.get("parallel_researchers") or wf.get("agents") or [])
            if a in _RESEARCHER_KEYS
        ]
        red = "red_team" in (wf.get("agents") or [])
        return TeamPlan(
            workflow_id=str(wf.get("id", "workflow")),
            researchers=researchers or _heuristic_researchers(query),
            include_red_team=red if include_red_team is None else include_red_team,
            description=str(wf.get("description", "")),
        )

    researchers = _heuristic_researchers(query)
    if re.search(r"^(筛选|查询|股价)", query.strip()):
        researchers = ["company_research"]

    red_default = include_red_team
    if red_default is None:
        red_default = mode in ("research", "committee")

    return TeamPlan(
        workflow_id="default",
        researchers=researchers,
        include_red_team=red_default,
        description="heuristic team selection",
    )
