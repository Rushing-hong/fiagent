# Changelog

All notable changes to [fiagent](https://github.com/Rushing-hong/fiagent) are documented here.

Format roughly follows [Keep a Changelog](https://keepachangelog.com/). Dates are Asia/Shanghai.

---

## [Unreleased]

- Token/efficiency (no local slowdown): compressed `prompts/base.md` (~−26%); API-only strip of historical `reasoning_content` (tool bodies kept full — no result truncation); block non-repeatable tool on 4th call; clip tool schema descriptions to 160 chars (`FIAGENT_TOOL_DESC_MAX`)
- Phase1 Week4: Layer2 β (HS300+ZZ500+t) on backtest metrics; risk exposure attribution via Barra risk_*; L1 `assert_unit/frequency` chain guards; `tests/test_phase1_week4.py`
- Phase1 Week3: dragon-tiger seat heuristic (`analyze_dragon_tiger`), `northbound_signal`, `calc_var`/`run_stress_test` A-share scenarios, thick Layer1 attribution
- Phase1 Week2: `build_factor_panel` (alpha_*/risk_* zoo, IC notes, research.db bulk); universe_asof on backtest; Barra risk_* expansion; thin Layer1 attribution on run_backtest metrics
- Phase1 Week1 (A-share depth): `docs/PHASE1_ASHARE.md`; exchange trade calendar module + `get_trade_calendar`; `get_macro_data` (PMI/CPI/M2/GDP) with `_meta` unit/frequency; `research.db` schema freeze (macro/factor long-table/micro/artifacts); backtest prefers exchange calendar; prompt defaults to A-share
- Fix stale clock in long sessions: refresh near-user clock each LLM round; add always-on `get_current_time`

---

## [0.2.0] — 2026-07-13

Financial analysis tools + A-share backtest realism (P0–P5). See also:

- [docs/FINANCIAL_MODULES_PLAN.md](docs/FINANCIAL_MODULES_PLAN.md)
- [docs/BACKTEST_ROADMAP.md](docs/BACKTEST_ROADMAP.md)
- [docs/CHANGELOG-2026-07.md](docs/CHANGELOG-2026-07.md) (earlier July batch)

### Added — Fundamentals

- `calc_dupont` — DuPont 3/5-factor + chain substitution
- `check_red_flags` — first-pass earnings-quality flags
- `screen_peers` — industry peer PE/PB/ROE percentiles
- `calc_dcf` — explicit-assumption FCFF DCF + sensitivity
- `track_consensus` — report-EPS revision proxy + simple SUE
- `get_financial_statements` attaches `normalized` fields via Eastmoney F10 map

### Added — Backtest / quant

- Engine realism: limit-lock reject, `signal_lag`, √ impact, halt handling, cash interest
- `build_tradable_universe` / `build_event_signals` / `blend_black_litterman` / `suggest_hedge_ratio`
- Futures hedge book, sleeve blend + attribution, industry + mom/size/vol style caps
- `analyze_portfolio_risk` — Barra-lite factor risk
- `load_pit_universe` + local `data/research.db` (minute cache, consensus & universe snapshots)
- `run_backtest(interval=5|15|…)` minute entry (near-term akshare + cache)

### Docs / skills

- Roadmaps and capability groups updated
- Skills (`financial-statement`, `valuation-model`, `earnings-analysis`, `report-generate`) point agents at the new tools

### Limits (documented, not bugs)

- No commercial long-history minute/L2, official Barra, paid consensus panel, or exchange official PIT membership replay
- Free-data paths are marked `quality=degraded` where appropriate

---

## [0.1.0] — 2026-07

Initial public release and early July tool batch:

- DeepSeek ReAct agent, Textual TUI + Rich CLI
- 40+ market tools (A-share / futures / CB / options)
- 49 domain skills, session SQLite, hooks
- First `run_backtest` engine + futures/CB/options/limit-board tools

Detail: [docs/CHANGELOG-2026-07.md](docs/CHANGELOG-2026-07.md)
