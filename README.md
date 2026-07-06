# fiagent

<p align="center">
  <b>面向中国市场的 AI 量化研究 Agent</b><br>
  DeepSeek ReAct Agent · 40+ 工具 · 49 个领域技能 · 双界面
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/model-DeepSeek%20V4-purple" alt="Model">
</p>

---

fiagent 是一个运行在终端里的 AI 投研助手。用自然语言对话完成：行情查询、策略回测、因子分析、财务研究、交易复盘。覆盖 **A 股、期货、可转债、ETF 期权**四大市场。

```
你: 帮我看看AI算力板块现在拥挤度怎么样
🤖: [调用 get_market_breadth] TMT成交占比 38.5%，极度拥挤。通信ETF上半年净流入443亿...

你: 筛选 PE<15 且 ROE>20 的股票，市值>100亿
🤖: [调用 screen_fundamental] 找到23只，前5: 茅台PE=14.2 ROE=28%...

你: 回测茅台双均线策略 2020-2025
🤖: [调用 run_backtest] 年化收益12.5%，夏普1.15，最大回撤-22.3%...
```

## 快速开始

```bash
# 1. 安装
git clone https://github.com/<your-username>/fiagent.git
cd fiagent
pip install -r requirements.txt

# 2. 设置 API Key（二选一）
echo DEEPSEEK_API_KEY=sk-xxxxxxxx > .env   # 方式A: 写入文件
set DEEPSEEK_API_KEY=sk-xxxxxxxx           # 方式B: 环境变量（首次运行会引导输入）

# 3. 启动
python agent.py          # Textual TUI 全屏界面（推荐）
python agent.py --plain  # Rich 纯终端界面
```

**对话内命令**: `/help` `/new` `/sessions` `/resume <id>` `/reload` `/thinking` `/tui` `/plain`

**环境变量**:
| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥（必填） |
| `FIAGENT_TZ` | 时区，默认 `Asia/Shanghai` |
| `FIAGENT_MAX_TOOL_ROUNDS` | 最大工具调用轮次，默认 10 |
| `FIAGENT_IWENCAI_KEY` | 同花顺问财 API Key（可选） |
| `FIAGENT_PLAIN_UI` | 设为 `1` 默认纯终端模式 |

---

## 功能概览

### 40+ 工具 — 覆盖四大市场

| 市场 | 工具 | 说明 |
|------|------|------|
| **A 股** | `get_market_data` `screen_market` `screen_fundamental` `iwencai_search` | 行情/筛选/选股 |
| | `get_dragon_tiger` `get_margin_trading` `get_block_trades` | 龙虎榜/两融/大宗 |
| | `get_fund_flow` `get_northbound_flow` `get_etf_flow` | 资金流向 |
| | `get_financial_statements` `get_research_reports` `get_stock_news` | 财报/研报/新闻 |
| | `get_shareholder_count` `get_lockup_expiry` `get_sector_info` | 股东/解禁/板块 |
| | `get_limit_board` `get_dividend_calendar` `get_insider_trades` | 涨停板/分红/增减持 |
| | `get_ipo_calendar` `get_index_constituents` `get_yield_curve` | 新股/指数成分/利率 |
| | `get_market_breadth` | TMT 拥挤度/板块涨跌比 |
| **期货** | `get_futures_quote` | 6 大交易所（中金所/上期所/大商所/郑商所/能源中心/广期所） |
| **可转债** | `get_cb_list` `screen_cb` | 全市场转债快照+双低/低价/YTM 筛选 |
| **期权** | `get_option_chain` | ETF 期权 T 型报价+希腊字母+PCR |
| **量化** | `pattern` `factor_analysis` `run_backtest` `analyze_trade_journal` | 形态识别/因子评估/策略回测/交易复盘 |
| **通用** | `read` `write` `edit` `grep` `read_url` `web_search` | 文件/网页/搜索 |

### 策略回测引擎

```
get_market_data → [内置策略 OR 自定义信号] → run_backtest → 绩效报告
```

4 种内置策略（MA 双均线/RSI/动量/买入持有）+ 自定义信号 CSV 模式。自动执行 A 股规则：T+1、涨跌停、印花税 0.05%、佣金 0.03%（2026.7.6 新规适配）。

### 交易日记分析

上传同花顺/东方财富/富途交割单 → 自动 FIFO 配对 → 输出：

- **交易画像**：胜率、盈亏比、夏普、最大回撤、时段分布
- **行为偏差诊断**：处置效应、过度交易、追涨、锚定效应
- **策略风格识别**：短线打板/波段操作/长期价值/高频交易

### 49 个领域技能

渐进披露式设计——system prompt 中只注入摘要，Agent 按需 `load_skill` 加载全文。覆盖：

| 类别 | 技能 |
|------|------|
| 技术分析 | `technical-analysis`(15种蜡烛+7大指标+缠论) |
| 基本面 | `financial-statement`(三表勾稽+12造假红旗+杜邦) `valuation-model`(DCF/DDM/PE-Band) |
| 策略 | `strategy-generate` `multi-factor` `pair-trading` `sector-rotation` |
| 资产配置 | `asset-allocation`(MPT/BL/风险预算/全天候) `risk-analysis`(VaR/CVaR/MC) |
| 衍生品 | `convertible-bond` `options-strategy` `options-payoff` |
| 行为金融 | `behavioral-finance` `sentiment-analysis` `market-microstructure` |
| 宏观/行业 | `macro-analysis` `ai-industry-chain` `commodity-analysis` `credit-analysis` |
| 市场 | `etf-analysis`(878行最完整) `fund-analysis` `dividend-analysis` `earnings-analysis` |
| 其他 | `report-generate` `backtest-diagnose` `regulatory-knowledge` `event-driven` 等 |

---

## 架构

```
用户输入
  → Hooks (turn.start)
  → core/loop  ReAct 循环 (最多10轮)
       ├─ core/stream  流式 DeepSeek V4 (思考链+正文+tool_calls)
       ├─ tools  并行只读(8路) / 串行写入
       ├─ Hooks (llm.before/after, tool.before/after)
       └─ ui  展示
  → session/SQLite 持久化
  → Hooks (turn.end)
```

```
fiagent/
├── agent.py              # 入口
├── core/                 # ReAct 循环 + 流式 LLM + CLI + 命令
├── session/              # SQLite 多会话持久化
├── hooks/                # 可插拔事件钩子
├── ui/                   # Rich 纯终端 + Textual TUI
├── market/               # 数据源(东财/akshare) + 回测引擎
├── tools/                # 40+ Agent 可调用工具
├── skills/               # 49 个领域技能文档
├── analysis/             # 因子分析核心算法
├── prompts/              # System prompt 模板
└── tests/                # 单元测试
```

---

## 设计参考

| 来源 | 借鉴 |
|------|------|
| **OpenCode** | 终端 UI 折叠/展开、思考过程默认折叠、`e`/数字键交互 |
| **Vibe-Trading** | ReAct Agent 骨架、A 股工具链、Skills 组织 |

---

## 贡献

欢迎提交 PR！新增工具或技能请参考 [CONTRIBUTING.md](CONTRIBUTING.md)。

- **新工具**：在 `tools/` 继承 `BaseTool`，`/reload` 自动发现
- **新技能**：在 `skills/<name>/SKILL.md` 编写说明，Agent 按需加载
- **新 Hook**：在 `hooks/` 编写模块，在 `hooks.json` 注册

## License

MIT
