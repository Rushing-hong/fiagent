# fiagent 2026.07 更新日志

## 概览

| 类别 | 数量 |
|------|------|
| 新增 market 模块 | 2 (`akshare_data.py`, `backtest_engine.py`) |
| 新增 tools | 7 |
| 改进现有 tools | 5 |
| 修复代码缺陷 | 5 |
| 重写 skills | 4 |
| 修复/更新 skills | 8 |
| 删除垃圾 | 7 |
| 重构 | 2 |

---

## 一、新增工具

### 1. `get_futures_quote` — 期货行情

覆盖中金所/上期所/能源中心/大商所/郑商所/广期所 6 大交易所热门品种。

```
模式:
  main_list    → 全市场主力合约列表
  daily        → 指定合约历史日线 OHLCV+持仓量
  position     → 前 20 名会员持仓排名
  spot         → 大宗商品现货报价

示例:
  get_futures_quote(mode="main_list", exchange="CFFEX")     → 中金所全部主力合约
  get_futures_quote(mode="daily", symbol="RB2505",
                    start_date="2025-01-01", end_date="2025-06-01")  → 螺纹钢日线
  get_futures_quote(mode="spot", symbol="螺纹钢")           → 螺纹钢现货报价
```

数据源：akshare。未安装时自动降级报错。

### 2. `get_cb_list` + `screen_cb` — 可转债

```
get_cb_list    → 全市场转债快照（双低值/溢价率/YTM/评级/强赎触发价/回售触发价）
screen_cb      → 双低/低价/YTM 三大策略筛选

示例:
  screen_cb(max_double_low=120, min_rating="AA", sort_by="double_low", top_n=15)
      → 双低值<120 的 AA 级以上转债

  screen_cb(max_price=100, min_rating="AA+", sort_by="ytm_rt", top_n=10)
      → 面值附近+高评级的低价策略
```

数据源：akshare（集思录数据）。复用现有 `convertible-bond` skill。

### 3. `get_option_chain` — ETF 期权 T 型报价

支持 50ETF/300ETF/500ETF/1000ETF/科创50ETF/创业板ETF。

```
get_option_chain(underlying="50ETF")
→ 返回: 认购/认沽期权价格、IV、Delta/Gamma/Theta/Vega/Rho、PCR
```

数据源：akshare。复用现有 `options-strategy` + `options-payoff` skills。

### 4. `get_limit_board` — 涨停板复盘

```
get_limit_board(date="2026-07-04")
→ 涨停股列表（含封板时间/炸板次数/连板天数/封单金额/板块）
→ 炸板股列表
→ 跌停股列表
```

A 股散户每日必看数据。复用现有 `sentiment-analysis` skill。

### 5. `get_dividend_calendar` — 分红除权日历

```
get_dividend_calendar(year="2026")
→ 全市场 2026 年分红计划: 预案公告日/除权除息日/股权登记日/每股分红/送转股/股息率

get_dividend_calendar(code="600519", year="2026")
→ 只看茅台
```

数据源：akshare（巨潮资讯）。复用现有 `dividend-analysis` skill。

### 6. `run_backtest` — 策略回测引擎

**内置 4 种策略**：

| 策略 | 参数 | 场景 |
|------|------|------|
| `ma_cross` | fast=5, slow=20 | 趋势跟踪 |
| `rsi` | period=14, oversold=30, overbought=70 | 均值回归 |
| `momentum` | window=20 | 突破买入 |
| `buy_hold` | — | 基准对照 |

**自定义信号模式**：

```
1. get_market_data → 获取 OHLCV
2. write 创建信号计算脚本 → 输出 CSV (date, weight)
3. run_backtest(strategy="custom", signal_file="path/to/signal.csv")
```

**A 股规则自动执行**：
- T+1 交割（当日买入次日可卖）
- 涨跌停限制（主板 ±10%/科创 ±20%/北交 ±30%/ST ±5%，自动板块识别）
- 印花税 0.05%（卖）
- 佣金 0.03% + 过户费 0.001%（可配）
- 最低佣金 5 元
- 最小交易单位 100 股
- A 股做空自动忽略

**输出指标**：
```json
{
  "total_return": 85.3, "annual_return": 12.5,
  "sharpe_ratio": 1.15, "sortino_ratio": 1.8,
  "max_drawdown": -22.3, "calmar_ratio": 0.56,
  "total_trades": 47, "win_rate": 55.3,
  "avg_win": 8520, "avg_loss": -4200,
  "profit_factor": 1.8, "buy_hold_return": 35.2
}
```

### 7. `screen_fundamental` — 基本面筛选

按 PE/PB/ROE/市值/股息率筛选全 A 股。

```
经典策略模板:

  格雷厄姆深度价值: screen_fundamental(max_pe=10, max_pb=1.5, min_dividend_yield=3)
  GARP 合理成长:     screen_fundamental(max_pe=30, min_roe=20, max_pb=5, min_market_cap=100)
  高股息策略:         screen_fundamental(min_dividend_yield=4, max_pe=20, min_market_cap=50)
  低PB反转:           screen_fundamental(max_pb=1.0, min_roe=5)
```

数据源：东财 push2 实时行情（PE/PB/市值/ROE/股息率）。

---

## 二、改进现有工具

| 工具 | 改进 |
|------|------|
| `pattern` | 新增 `cfg` 参数，Agent 可自定义 7 个形态识别阈值（doji 比值/hammer 影线比/头肩对称性/双顶容差等） |
| `screen_market` | `sort_by` 从 4 个扩展到 7 个（+pe/market_cap/pb），输出字段加 PE/PB/市值 |
| `get_sector_info` | 新增 `sector_type` 参数，支持**概念板块**（AI/新能源等）涨跌排行 |
| `iwencai_search` | 列限制 40→60，减少复杂查询截断 |
| `web.py read_url` | Jina Reader 失败时自动降级为原生 requests + HTML 提取 |

---

## 三、代码修复

| 文件 | 修复 |
|------|------|
| `session/store.py` | `save_messages` 加 `BEGIN IMMEDIATE`/`commit`/`rollback` 事务包裹，崩溃不丢数据 |
| `market/http.py` | `requests.Session` 改为 `dict[thread_id, Session]` 每线程独立，消除并发隐患 |
| `market/loaders.py` | `except Exception` → `except (ValueError, TypeError, ConnectionError, OSError)`，不吞 KeyboardInterrupt |
| `core/loop.py` | `MAX_TOOL_ROUNDS`/`MAX_READONLY_WORKERS` 改为环境变量可配 |
| `tools/base.py` | `execute_tool` 加 5 种精确异常分类，不再统一 `Exception` |

---

## 四、重构

| 文件 | 改动 |
|------|------|
| `tools/_pattern_lib.py` | 新增 `PatternConfig` dataclass，7 个硬编码阈值全部外部化 |
| `market/eastmoney.py` | 新增 `validate_a_share()` 统一校验函数，消除 `stock_disclosure` 和 `stock_research` 的重复代码 |

---

## 五、重写的 Skills（引用虚构工具 → 实际可用）

| Skill | 问题 | 修复 |
|-------|------|------|
| `pair-trading` | 引用不存在的 config.json/backtest | 完整工作流: get_market_data → Python 算 Z-score → write 信号 CSV → run_backtest(custom) |
| `strategy-generate` | 50% 内容引用不存在引擎 | 6 步流程: 数据→策略(内置+自定义)→回测→分析→审查→迭代 |
| `multi-factor` | 引用 ZooSignalEngine/Registry | 7 步流程: 因子计算→factor_analysis→IC 筛选→合成→run_backtest |
| `fundamental-filter` | 引用 config.json/DataLoader | 引用 screen_fundamental + get_financial_statements + iwencai_search |

---

## 六、更新的 Skills（添加工具引用）

| Skill | 新增引用 |
|-------|---------|
| `alpha-zoo` | 标记工具未实现，指引使用 factor_analysis |
| `chanlun` | 标记 czsc 需手动安装，指引使用 get_market_data + write |
| `dividend-analysis` | → `get_dividend_calendar` |
| `convertible-bond` | → `get_cb_list` + `screen_cb` |
| `options-strategy` | → `get_option_chain` |
| `sentiment-analysis` | → `get_limit_board` + `get_margin_trading` + `get_northbound_flow` |

---

## 七、System Prompt

`prompts/base.md` 新增**中国 A 股市场核心约束**章节：
- T+1 交割规则
- 主板/科创/创业/北交/ST 涨跌停限制
- 集合竞价时段
- 印花税/佣金/过户费/股息红利税费率
- 科创板/北交所投资者适当性门槛
- 举牌/大股东减持规则

---

## 八、删除内容

| 内容 | 原因 |
|------|------|
| `tools/trigger_2005.py` | Demo 垃圾代码 |
| `skills/trigger-2004/` | 空壳占位 |
| `skills/adr-hshare/` | 空壳占位 |
| `skills/vnpy-export/` | 空壳占位 |
| `skills/shadow-account/` | 空壳占位 |
| `skills/options-advanced/` | 空壳占位 |
| `skills/user/` | 空目录 |

---

## 九、完整工具索引（26 个）

### 行情数据
| 工具 | 内容 |
|------|------|
| `get_market_data` | A 股 OHLCV（腾讯/东财/mootdx/baostock/akshare 五源 fallback）|
| `search_symbol` | 中文名/代码搜索 |
| `screen_market` | 按涨跌幅/量/额/PE/PB/市值排序 |

### 资金流向
| 工具 | 内容 |
|------|------|
| `get_fund_flow` | 个股主力/超大单/大单/中单/小单资金流向 |
| `get_northbound_flow` | 北向资金（沪股通+深股通） |

### 异动与事件
| 工具 | 内容 |
|------|------|
| `get_dragon_tiger` | 龙虎榜（席位级买卖明细） |
| `get_margin_trading` | 融资融券余额 |
| `get_block_trades` | 大宗交易（溢价率/买卖营业部） |
| `get_limit_board` | **新** 涨停板复盘（封板时间/炸板/连板/板块） |
| `get_dividend_calendar` | **新** 分红除权日历 |

### 结构与解禁
| 工具 | 内容 |
|------|------|
| `get_shareholder_count` | 股东户数/人均持股 |
| `get_lockup_expiry` | 限售解禁日历 |
| `get_sector_info` | 行业/概念板块归属+排行 |

### 研究与基本面
| 工具 | 内容 |
|------|------|
| `get_financial_statements` | 三表+主要财务指标 |
| `get_research_reports` | 卖方研报+一致预期 EPS |
| `get_stock_news` | 财经新闻 |
| `screen_fundamental` | **新** PE/PB/ROE/市值/股息率多维筛选 |

### 选股与量化
| 工具 | 内容 |
|------|------|
| `iwencai_search` | 问财自然语言选股 |
| `pattern` | K 线形态识别（8 种，支持自定义阈值） |
| `factor_analysis` | IC/IR + 分层净值 |
| `run_backtest` | **新** 策略回测引擎（4 内置策略+自定义信号） |

### 跨市场
| 工具 | 内容 |
|------|------|
| `get_futures_quote` | **新** 期货行情（6 交易所/4 模式） |
| `get_cb_list` | **新** 可转债全市场快照 |
| `screen_cb` | **新** 可转债双低/低价/YTM 筛选 |
| `get_option_chain` | **新** ETF 期权 T 型报价+希腊字母 |

### 交易分析
| 工具 | 内容 |
|------|------|
| `analyze_trade_journal` | 交易日记分析+FIFO 配对+4 种行为偏差诊断 |

### 通用与 Skill 管理
| 工具 | 内容 |
|------|------|
| `read` / `write` / `edit` / `grep` | 工作区文件操作 |
| `read_url` / `web_search` | 网页抓取+搜索 |
| `load_skill` / `save_skill` / `patch_skill` / `delete_skill` | Skill 管理 |
