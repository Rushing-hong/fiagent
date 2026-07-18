---
name: data-routing
description: A 股数据路由：按需求选择 fiagent 内置工具与数据源（东财/腾讯/mootdx/akshare 等），含鉴权说明与限流注意。
---
# Data Routing（A 股数据路由）

在拉行情、资金面、披露面或基本面数据前，先读本 skill，选对工具和数据源。

## 数据源概览（fiagent）

| 来源 | 用途 | 鉴权 | 说明 |
|------|------|------|------|
| tencent | A 股 OHLCV 首选 | 无 | `get_market_data` auto 首选 |
| mootdx | A 股 OHLCV | 无 | 通达信 TCP，不易封 IP，需 `pip install mootdx` |
| eastmoney | OHLCV 备用 + 几乎全部专题工具 | 无 | 按 IP 限流 |
| baostock | A 股 OHLCV | 无 | 需 `pip install baostock` |
| akshare | A 股 OHLCV 兜底 | 无 | 需 `pip install akshare` |
| tushare | 扩展数据 | `TUSHARE_TOKEN` | 见 tushare skill |
| iwencai | 自然语言选股 | `FIAGENT_IWENCAI_KEY` | 未配置则工具不出现 |

auto 链：`tencent → mootdx → eastmoney → baostock → akshare`

## 能力 → 工具路由

| 数据需求 | 工具 |
|----------|------|
| OHLCV 行情 | `get_market_data` |
| 技术形态 | `pattern` |
| 因子 IC/IR | `factor_analysis` |
| 代码搜索 | `search_symbol` |
| 全市场筛选 | `screen_market`（push2→push2delay→akshare；`ascending=true` 跌幅榜） |
| 个股资金流 | `get_fund_flow` |
| 北向资金 | `get_northbound_flow` |
| 龙虎榜 | `get_dragon_tiger` |
| 融资融券 | `get_margin_trading` |
| 大宗交易 | `get_block_trades` |
| 股东户数 | `get_shareholder_count` |
| 限售解禁 | `get_lockup_expiry` |
| 板块 | `get_sector_info` |
| 研报 | `get_research_reports` |
| 新闻 | `get_stock_news` |
| 财报 | `get_financial_statements` |
| 问财选股 | `iwencai_search` |
| 网页搜索 | `web_search` → `read_url` |
| 交易复盘 | `analyze_trade_journal` |

## 相关 Skills

`regulatory-knowledge` `chanlun` `etf-analysis` `macro-analysis` `event-driven` `trade-journal` `factor-research` `multi-factor`

## 回测与未接入项

- **回测**：用工具 `run_backtest`（日频默认 T+1 + `signal_lag=1` + 开盘成交；支持 `universe_asof`、Layer1/2 归因）
- **未接入（仅 skill 方法论）**：A 股期权链完整链路（options-* skills 有框架）；商业级完整 CNE5 / L2 逐笔
