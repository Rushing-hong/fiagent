"""Intent router: Fast / Research / Committee."""

from __future__ import annotations

import os
import re
from enum import Enum


class AgentMode(str, Enum):
    FAST = "fast"
    RESEARCH = "research"
    COMMITTEE = "committee"
    TRADE_REVIEW = "trade_review"


_COMMITTEE_PATTERNS = [
    r"是否(应该)?买入",
    r"是否.{0,6}买入",
    r"该不该买",
    r"值不值得买",
    r"值得买入",
    r"构建.{0,6}组合",
    r"投资组合",
    r"仓位(建议|配置|分配)",
    r"投资委员会",
    r"实盘(可行|准备)",
    r"对冲方案",
]

_REVIEW_PATTERNS = [
    r"交易复盘",
    r"复盘(我的)?交易",
    r"分析(我的)?交易记录",
    r"交割单",
    r"交易日记",
    r"trade\s*journal",
    r"analyze_trade_journal",
]

_RESEARCH_PATTERNS = [
    r"深度分析",
    r"深入研究",
    r"全面分析",
    r"详细分析",
    r"研究(一下|下)",
    r"怎么看",
    r"是否值得(关注|投资|持有)",
    r"行业(机会|前景|研究)",
    r"板块(拥挤|机会|研究)",
    r"事件影响",
    r"诊断.{0,4}策略",
    r"回测.{0,6}诊断",
]


def _env_mode() -> AgentMode | None:
    raw = os.getenv("FIAGENT_AGENT_MODE", "").strip().lower()
    if raw in ("fast", "research", "committee", "trade_review", "auto", ""):
        if raw in ("fast", "research", "committee", "trade_review"):
            return AgentMode(raw)
    return None


def route_query(text: str, *, force: AgentMode | None = None) -> AgentMode:
    """Classify user intent. ``force`` overrides heuristics and env."""
    if force is not None:
        return force
    env = _env_mode()
    if env is not None:
        return env

    q = (text or "").strip()
    if not q:
        return AgentMode.FAST

    for pat in _COMMITTEE_PATTERNS:
        if re.search(pat, q, re.I):
            return AgentMode.COMMITTEE

    for pat in _REVIEW_PATTERNS:
        if re.search(pat, q, re.I):
            return AgentMode.TRADE_REVIEW

    for pat in _RESEARCH_PATTERNS:
        if re.search(pat, q, re.I):
            return AgentMode.RESEARCH

    # Short factual queries → fast
    if len(q) < 40 and not re.search(r"(分析|研究|买入|组合|策略诊断)", q):
        return AgentMode.FAST

    return AgentMode.FAST
