"""工具 / Skills 按需分类（管理开关用）。"""

from __future__ import annotations

# --- 工具分类：未列出的归入「其他」---

TOOL_CATEGORIES: list[tuple[str, str, frozenset[str]]] = [
    (
        "行情数据",
        "K 线、代码搜索、行情筛选、指数成分、期货报价",
        frozenset({
            "get_market_data",
            "search_symbol",
            "screen_market",
            "get_index_constituents",
            "get_futures_quote",
        }),
    ),
    (
        "资金流向",
        "个股资金、北向、ETF 流向",
        frozenset({
            "get_fund_flow",
            "get_northbound_flow",
            "get_etf_flow",
        }),
    ),
    (
        "基本面与研报",
        "财务筛选、三大报表、杜邦/红旗/同业、研报、新闻",
        frozenset({
            "screen_fundamental",
            "get_financial_statements",
            "get_research_reports",
            "get_stock_news",
            "calc_dupont",
            "check_red_flags",
            "screen_peers",
            "calc_dcf",
            "track_consensus",
        }),
    ),
    (
        "交易异动",
        "龙虎榜、两融、大宗、股东、解禁、增减持",
        frozenset({
            "analyze_dragon_tiger",
            "northbound_signal",
            "calc_var",
            "run_stress_test",
            "get_dragon_tiger",
            "get_margin_trading",
            "get_block_trades",
            "get_shareholder_count",
            "get_lockup_expiry",
            "get_insider_trades",
        }),
    ),
    (
        "市场情绪",
        "涨跌停、板块、广度、IPO、分红、利率、宏观、交易日历",
        frozenset({
            "get_limit_board",
            "get_sector_info",
            "get_market_breadth",
            "get_ipo_calendar",
            "get_dividend_calendar",
            "get_yield_curve",
            "get_macro_data",
            "get_trade_calendar",
        }),
    ),
    (
        "衍生品",
        "可转债、期权链",
        frozenset({
            "get_cb_list",
            "screen_cb",
            "get_option_chain",
        }),
    ),
    (
        "量化研究",
        "形态、回测、因子、交易日志、问财",
        frozenset({
            "pattern",
            "run_backtest",
            "build_tradable_universe",
            "build_event_signals",
            "blend_black_litterman",
            "suggest_hedge_ratio",
            "analyze_portfolio_risk",
            "load_pit_universe",
            "build_factor_panel",
            "factor_analysis",
            "analyze_trade_journal",
            "iwencai_search",
        }),
    ),
    (
        "网络检索",
        "网页搜索与抓取",
        frozenset({
            "web_search",
            "read_url",
            "get_current_time",
        }),
    ),
]

# --- Skills 分类 ---

SKILL_CATEGORIES: list[tuple[str, str, frozenset[str]]] = [
    (
        "数据源与路由",
        "东财 / akshare / mootdx / tushare / 路由",
        frozenset({
            "eastmoney",
            "akshare",
            "mootdx",
            "tushare",
            "data-routing",
        }),
    ),
    (
        "基本面与估值",
        "财报、估值、信用、分红、过滤",
        frozenset({
            "financial-statement",
            "valuation-model",
            "credit-analysis",
            "dividend-analysis",
            "earnings-analysis",
            "fundamental-filter",
            "ashare-pre-st-filter",
            "fund-analysis",
        }),
    ),
    (
        "技术与量化",
        "技术面、缠论、因子、波动、回测诊断",
        frozenset({
            "technical-analysis",
            "chanlun",
            "minute-analysis",
            "factor-research",
            "multi-factor",
            "alpha-zoo",
            "volatility",
            "quant-statistics",
            "correlation-analysis",
            "backtest-diagnose",
            "performance-attribution",
        }),
    ),
    (
        "策略与交易",
        "配对、对冲、事件、板块轮动、期权、可转债",
        frozenset({
            "pair-trading",
            "hedging-strategy",
            "event-driven",
            "sector-rotation",
            "seasonal",
            "options-strategy",
            "options-payoff",
            "convertible-bond",
            "execution-model",
            "strategy-generate",
            "trade-journal",
        }),
    ),
    (
        "宏观与资产",
        "宏观、配置、风险、情绪、微观结构",
        frozenset({
            "macro-analysis",
            "asset-allocation",
            "risk-analysis",
            "sentiment-analysis",
            "behavioral-finance",
            "market-microstructure",
            "commodity-analysis",
            "etf-analysis",
            "hk-connect-flow",
            "ai-industry-chain",
            "corporate-events",
            "regulatory-knowledge",
            "report-generate",
        }),
    ),
]


def _group(
    names: list[str],
    categories: list[tuple[str, str, frozenset[str]]],
) -> list[tuple[str, str, list[str]]]:
    """返回 [(cat_id, hint, member_names), ...]，含「其他」。"""
    remaining = set(names)
    out: list[tuple[str, str, list[str]]] = []
    for cat_id, hint, members in categories:
        hit = sorted(n for n in names if n in members)
        if not hit:
            continue
        remaining -= set(hit)
        out.append((cat_id, hint, hit))
    if remaining:
        out.append(("其他", "未归类项", sorted(remaining)))
    return out


def group_tools(tool_names: list[str]) -> list[tuple[str, str, list[str]]]:
    return _group(tool_names, TOOL_CATEGORIES)


def group_skills(skill_names: list[str]) -> list[tuple[str, str, list[str]]]:
    return _group(skill_names, SKILL_CATEGORIES)


def category_counts(
    members: list[str],
    *,
    is_enabled,
) -> tuple[int, int]:
    """(enabled, total)。"""
    total = len(members)
    enabled = sum(1 for n in members if is_enabled(n))
    return enabled, total
