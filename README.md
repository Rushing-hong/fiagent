# fiagent

<p align="center">
  <b>AI Quant Research Agent for the Chinese Market</b><br>
  DeepSeek ReAct Agent · 40+ Tools · 49 Domain Skills · Dual UI
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/model-DeepSeek%20V4-purple" alt="Model">
</p>

---

fiagent is a terminal-based AI investment research assistant. Chat in natural language to query market data, backtest strategies, analyze factors, research fundamentals, or review your trades — across **A-shares, futures, convertible bonds, and ETF options**.

```
You: How crowded is the AI computing sector right now?
🤖: [calls get_market_breadth] TMT turnover 38.5%, extremely crowded. Telecom ETF net inflow ¥44.3B H1...

You: Screen stocks with PE<15, ROE>20%, market cap>¥100B
🤖: [calls screen_fundamental] Found 23. Top 5: Moutai PE=14.2 ROE=28%...

You: Backtest Moutai with dual-MA strategy, 2020-2025
🤖: [calls run_backtest] Annual return 12.5%, Sharpe 1.15, max drawdown -22.3%...
```

---

## Design References

Built on ideas from two excellent open-source projects:

| Project | What we took | GitHub |
|------|------|------|
| **Vibe-Trading** | Skills architecture — progressive disclosure, `SKILL.md` layout, domain skill categorization | [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) |
| **OpenCode** | Streaming pipeline — reasoning/content/tool_call delta merging, collapsible UI with `e`/digit-key navigation | [anomalyco/opencode](https://github.com/anomalyco/opencode) |

---

## Quick Start

```bash
# 1. Install
git clone https://github.com/Rushing-hong/fiagent.git
cd fiagent
pip install -r requirements.txt

# 2. Set API Key (pick one)
echo DEEPSEEK_API_KEY=sk-xxxxxxxx > .env   # Option A: write to .env
set DEEPSEEK_API_KEY=sk-xxxxxxxx           # Option B: env var (prompted on first run)

# 3. Launch
python agent.py          # Textual TUI — recommended
python agent.py --plain  # Rich terminal mode
```

**In-chat commands**: `/help` `/new` `/sessions` `/resume <id>` `/reload` `/thinking` `/tui` `/plain`

**Environment variables**:
| Variable | Description |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API key (required) |
| `FIAGENT_TZ` | Timezone, default `Asia/Shanghai` |
| `FIAGENT_MAX_TOOL_ROUNDS` | Max tool-call rounds, default 10 |
| `FIAGENT_IWENCAI_KEY` | Hithink iwencai API key (optional) |
| `FIAGENT_PLAIN_UI` | Set to `1` for plain terminal mode |

---

## Features

### 40+ Tools — Four Markets

| Market | Tools | Purpose |
|------|------|------|
| **A-Shares** | `get_market_data` `screen_market` `screen_fundamental` `iwencai_search` | Quotes / screening / stock picking |
| | `get_dragon_tiger` `get_margin_trading` `get_block_trades` | Dragon & tiger board / margin / block trades |
| | `get_fund_flow` `get_northbound_flow` `get_etf_flow` | Capital flows |
| | `get_financial_statements` `get_research_reports` `get_stock_news` | Financials / analyst reports / news |
| | `get_shareholder_count` `get_lockup_expiry` `get_sector_info` | Shareholders / lockup expiry / sectors |
| | `get_limit_board` `get_dividend_calendar` `get_insider_trades` | Limit boards / dividends / insider trades |
| | `get_ipo_calendar` `get_index_constituents` `get_yield_curve` | IPOs / index constituents / yield curve |
| | `get_market_breadth` | TMT crowding / sector breadth |
| **Futures** | `get_futures_quote` | 6 exchanges (CFFEX/SHFE/INE/DCE/ZCE/GFEX) |
| **CB** | `get_cb_list` `screen_cb` | Full market CB snapshot + dual-low / bargain / YTM screening |
| **Options** | `get_option_chain` | ETF option T-quote + Greeks + PCR |
| **Quant** | `pattern` `factor_analysis` `run_backtest` `analyze_trade_journal` | Pattern detection / factor evaluation / backtesting / trade journal analysis |
| **General** | `read` `write` `edit` `grep` `read_url` `web_search` | File / web / search |

### Backtesting Engine

```
get_market_data → [built-in strategy OR custom signal] → run_backtest → performance report
```

4 built-in strategies (MA crossover / RSI / Momentum / Buy & Hold) + custom signal CSV mode. A-share rules enforced automatically: T+1 settlement, price limits, stamp duty 0.05%, commission 0.03% (2026.7.6 rules).

### Trade Journal Analysis

Upload Hithink Flush / Eastmoney / Futu trade statements → automatic FIFO pairing → outputs:

- **Trading profile**: win rate, profit-loss ratio, Sharpe, max drawdown, intraday distribution
- **Behavioral diagnostics**: disposition effect, overtrading, chasing, anchoring bias
- **Strategy style**: scalping / swing trading / long-term value / high-frequency

### 49 Domain Skills

Progressive disclosure — only summaries in the system prompt; the Agent loads full text via `load_skill` as needed. Coverage:

| Category | Skills |
|------|------|
| Technical | `technical-analysis` (15 candlestick patterns + 7 indicators + Chan theory) |
| Fundamentals | `financial-statement` (3-statement linkage + 12 red flags + DuPont) `valuation-model` (DCF/DDM/PE-Band) |
| Strategy | `strategy-generate` `multi-factor` `pair-trading` `sector-rotation` |
| Allocation | `asset-allocation` (MPT/BL/risk budget/all-weather) `risk-analysis` (VaR/CVaR/MC) |
| Derivatives | `convertible-bond` `options-strategy` `options-payoff` |
| Behavioral | `behavioral-finance` `sentiment-analysis` `market-microstructure` |
| Macro/Sector | `macro-analysis` `ai-industry-chain` `commodity-analysis` `credit-analysis` |
| Market | `etf-analysis` `fund-analysis` `dividend-analysis` `earnings-analysis` |
| Others | `report-generate` `backtest-diagnose` `regulatory-knowledge` `event-driven` etc. |

---

## Architecture

```
User Input
  → Hooks (turn.start)
  → core/loop  ReAct loop (max 10 rounds)
       ├─ core/stream  Streaming DeepSeek V4 (reasoning + text + tool_calls)
       ├─ tools  Parallel readonly (8 workers) / serial writes
       ├─ Hooks (llm.before/after, tool.before/after)
       └─ ui  Render
  → session/SQLite  Persist
  → Hooks (turn.end)
```

```
fiagent/
├── agent.py              # Entry point
├── core/                 # ReAct loop + streaming LLM + CLI + commands
├── session/              # SQLite multi-session persistence
├── hooks/                # Pluggable event hooks
├── ui/                   # Rich terminal + Textual TUI
├── market/               # Data sources (Eastmoney/akshare) + backtesting engine
├── tools/                # 40+ agent-callable tools
├── skills/               # 49 domain skill documents
├── analysis/             # Factor analysis core algorithms
├── prompts/              # System prompt templates
└── tests/                # Unit tests
```

---

## Contributing

PRs welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for tool and skill guidelines.

- **New tool**: Subclass `BaseTool` in `tools/`, auto-discovered on `/reload`
- **New skill**: Write `skills/<name>/SKILL.md`, Agent loads on demand
- **New hook**: Write a module in `hooks/`, register in `hooks.json`

## License

MIT
