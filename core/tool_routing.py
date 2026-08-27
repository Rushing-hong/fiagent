"""Conservative request-side tool schema routing.

The model does not need all finance schemas for an explicit code-edit or DCF
request. We only narrow the list when the latest user request contains clear
domain cues; ambiguous requests deliberately return ``None`` (all tools).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from core.config import env_bool


_BASE_TOOLS = frozenset({"load_skill", "search_symbol", "get_market_data"})

_ROUTES: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (
        frozenset({"行情", "k线", "走势", "股价", "价格", "指数", "成分股", "期货", "合约"}),
        frozenset({
            "get_market_data", "search_symbol", "screen_market",
            "get_index_constituents", "get_futures_quote", "get_trading_rules",
        }),
    ),
    (
        frozenset({"资金流", "主力资金", "北向", "沪股通", "深股通", "etf流"}),
        frozenset({"get_fund_flow", "get_northbound_flow", "get_etf_flow", "northbound_signal"}),
    ),
    (
        frozenset({
            "基本面", "财报", "财务", "研报", "估值", "dcf", "杜邦", "同业",
            "一致预期", "盈利", "营收", "利润", "资产负债", "现金流", "红旗",
        }),
        frozenset({
            "calc_dcf", "calc_dupont", "check_red_flags", "get_financial_statements",
            "get_research_reports", "get_stock_news", "screen_fundamental",
            "screen_peers", "track_consensus",
        }),
    ),
    (
        frozenset({
            "龙虎榜", "两融", "融资融券", "大宗交易", "股东人数", "解禁",
            "增持", "减持", "压力测试", "var",
        }),
        frozenset({
            "analyze_dragon_tiger", "calc_var", "get_block_trades", "get_dragon_tiger",
            "get_insider_trades", "get_lockup_expiry", "get_margin_trading",
            "get_shareholder_count", "northbound_signal", "run_stress_test",
        }),
    ),
    (
        frozenset({
            "市场情绪", "涨停", "跌停", "板块", "市场广度", "ipo", "分红",
            "股息", "利率", "收益率曲线", "宏观", "交易日历", "股吧", "隔夜",
        }),
        frozenset({
            "calc_overnight_returns", "get_dividend_calendar", "get_guba_sentiment",
            "get_ipo_calendar", "get_limit_board", "get_macro_data",
            "get_market_breadth", "get_sector_info", "get_trade_calendar", "get_yield_curve",
        }),
    ),
    (
        frozenset({"esg", "碳价", "碳排", "产业链"}),
        frozenset({"get_carbon_prices", "get_esg_overview", "query_industry_chain", "search_esg_reports"}),
    ),
    (
        frozenset({"可转债", "转债", "期权", "期权链"}),
        frozenset({"get_cb_list", "get_option_chain", "screen_cb", "simulate_execution"}),
    ),
    (
        frozenset({
            "量化", "回测", "因子", "策略", "组合优化", "对冲", "风险分析",
            "形态", "技术分析", "交易信号", "交易日志", "问财", "pit",
        }),
        frozenset({
            "analyze_portfolio_risk", "analyze_trade_journal", "blend_black_litterman",
            "build_event_signals", "build_factor_panel", "build_tradable_universe",
            "factor_analysis", "load_pit_universe", "pattern", "run_backtest",
            "suggest_hedge_ratio", "simulate_execution",
        }),
    ),
    (
        frozenset({"网页", "网站", "联网", "网络搜索", "搜索互联网", "网址", "url"}),
        frozenset({"get_current_time", "read_url", "web_search"}),
    ),
    (
        frozenset({
            "代码", "文件", "目录", "脚本", "仓库", "git", "bug", "重构",
            "优化结构", "修改代码", "写入文件", "保存到", "skill",
        }),
        frozenset({
            "delete_skill", "edit", "grep", "load_skill", "patch_skill", "read",
            "save_skill", "write", "list_run_evidence", "list_validated_lessons",
            "read_agent_report",
        }),
    ),
)


def _latest_user_text(messages: Iterable[dict[str, Any]]) -> str:
    for message in reversed(list(messages)):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and not content.startswith("【系统实时时钟】"):
            return content.strip().lower()
    return ""


def select_tool_names(
    messages: Iterable[dict[str, Any]],
    available_names: Iterable[str],
) -> frozenset[str] | None:
    """Return selected names, or ``None`` when all schemas should be kept."""
    # Exact tool definitions participate in provider prompt-cache prefixes.
    # Cache-first is the default; routing remains an explicit token-first mode.
    if not env_bool("FIAGENT_TOOL_ROUTING", False):
        return None
    text = _latest_user_text(messages)
    if not text:
        return None

    available = frozenset(available_names)
    baseline = _BASE_TOOLS & available
    selected = set(baseline)
    matched_routes = 0
    for keywords, names in _ROUTES:
        if any(keyword in text for keyword in keywords):
            selected.update(names & available)
            matched_routes += 1

    # A caller that names a function explicitly should always get its schema.
    selected.update(name for name in available if name.lower() in text)
    if matched_routes == 0 and selected == set(baseline):
        return None
    # Broad cross-domain requests benefit more from full availability than
    # from a marginal schema saving.
    if matched_routes >= 6 or len(selected) >= max(1, int(len(available) * 0.75)):
        return None
    return frozenset(selected)
