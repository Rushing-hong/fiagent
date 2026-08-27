"""Soft tool-usage advisories (no hard blocks by agent profile)."""

from __future__ import annotations

from core.config import env_int

SOFT_WARN_AT = env_int("FIAGENT_TOOL_SOFT_WARN_AT", 3, minimum=2, maximum=50)
STRONG_WARN_AT = env_int(
    "FIAGENT_TOOL_STRONG_WARN_AT", 5, minimum=SOFT_WARN_AT + 1, maximum=100
)

# Lighter / cheaper substitutes when a heavy tool is over-used.
_TOOL_ALTERNATIVES: dict[str, list[str]] = {
    "run_backtest": ["pattern", "get_market_data", "factor_analysis"],
    "backtest_universe": ["fundamental_screen", "get_market_data"],
    "factor_analysis": ["get_market_data", "pattern"],
    "iwencai": ["fundamental_screen", "screen_peers"],
    "web_search": ["read", "get_stock_disclosure"],
    "web_fetch": ["read", "get_stock_disclosure"],
    "run_python": ["financial_calc", "calc_dcf"],
    "get_macro_data": ["get_market_breadth", "get_sentiment"],
    "analyze_portfolio_risk": ["calc_var", "get_market_data"],
    "build_factor_panel": ["factor_analysis", "get_market_data"],
    "blend_black_litterman": ["analyze_portfolio_risk", "calc_var"],
}


def tool_usage_advisory(tool_name: str, count: int, *, repeatable: bool) -> str:
    """Return a prefix warning for tool results; empty if below threshold."""
    min_count = STRONG_WARN_AT if repeatable else SOFT_WARN_AT
    if count < min_count:
        return ""

    alts = _TOOL_ALTERNATIVES.get(tool_name, [])
    if alts:
        alt_line = f"可改用更轻量的工具：{', '.join(f'`{a}`' for a in alts)}。"
    else:
        alt_line = "请优先基于已有工具结果直接回答，避免重复拉数。"

    if count >= STRONG_WARN_AT:
        return (
            f"【重要】工具 `{tool_name}` 本轮已调用 {count} 次。"
            f"是否真的还需要继续？{alt_line}"
            "若任务可简化，请降级方案或向用户说明数据已足够。\n\n"
        )
    return (
        f"【提醒】工具 `{tool_name}` 第 {count} 次调用。"
        f"{alt_line}请优先利用已有结果。\n\n"
    )
