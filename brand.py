"""Global product brand (user-facing copy).

Env vars keep the ``FIAGENT_*`` prefix for backward compatibility.
"""

from __future__ import annotations

# Atrading — A-share + trading
APP_NAME = "Atrading"
APP_SLUG = "atrading"

# One-liner
TAGLINE = "A-share research agent — data, backtest, and trade review"
TAGLINE_ZH = "A股投研助手 · 行情 · 回测 · 复盘"

# HTTP / CLI
USER_AGENT = f"Mozilla/5.0 (compatible; {APP_NAME}/1.0)"
CLI_TITLE = APP_NAME

# Legacy
LEGACY_NAME = "fiagent"
ENV_PREFIX = "FIAGENT"  # existing .env keys stay unchanged
