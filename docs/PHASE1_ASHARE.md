# Phase 1 — A股不可替代性（工程计划）

> Owner：实现方（本仓库 Agent/开发者）  
> Reviewer：仓库维护者（schema/验收冻结点）  
> 状态：Week1 进行中（2026-07-14）  
> 目标：不做 Vibe-Trading 广度；做 **可信的中国 A 股投研深度**。

相关讨论结论已吸收：依赖序、周闭环、长表因子面板、unit/frequency meta、归因按周拆分、L0/L1/L2 验收。

---

## 1. 原则

1. **默认 A 股**：未指定市场时走 A 股工具与语境。  
2. **基建先于展示**：交易日历、universe 快照、`research.db` schema 必须被引擎消费，不只是独立 tool。  
3. **风险 vs Alpha 分家**：`purpose=risk|alpha`（表字段 + tool 描述）。  
4. **工程验收 ≠ Alpha 验收**：符号错误打 `invert_signal_note`；弱 IC 打 `weak_alpha_note`；**不**用 `IC_IR>0.02` 阻断合入。  
5. **验证自动化**：合入以 L0+L1 为准；L2 为半自动行为回归。

---

## 2. 依赖序（必须遵守）

```
日历模块 ──┬─→ 回测日期轴 / Layer1·2 持仓天数
           ├─→ 宏观发布日对齐（勿把月频当「连续N日」）
           └─→ 因子窗口（交易日计数）

Universe 快照 ──┬─→ 因子截面股票池
                └─→ 回测成分（消除「仅用今日存活股回放历史」）

research.db schema（Week1 冻结）──→ 一切可被下游消费的状态

宏观 ──→（可选）宏观类因子；不阻塞价量/财务 Alpha v0

Barra risk_* ──→ Week4 risk 暴露归因

Prompt A股默认 ──→ Scenario / L2 行为测必须在 Prompt 变更后重跑
```

---

## 3. 四周交付与周闭环

| 周 | 基建 / 能力 | 周末可演示闭环 |
|----|-------------|----------------|
| **Week1** | 日历模块（回测可接线）+ `research_store` DDL 冻结 + `get_macro_data` + Prompt/A股默认 + `_meta` 约定 | 宏观 → Agent 宏观研判 |
| **Week2** | Universe 强制接入因子/回测 + Alpha/risk 命名空间 + 因子长表写入 + Barra 风格子集 + **薄 Layer1** | 宏观 + 因子选股 → Top 名单 + IC 序列 |
| **Week3** | 龙虎榜席位 v0 + 北向信号化 + VaR/A股情景压力 + **Layer1 加厚** | 宏观+因子+微观 → 综合信号/组合建议 |
| **Week4** | Layer2（β_HS300 + β_ZZ500）+ risk 暴露归因 + L1 Scenario 全绿 + L2 行为回归 | 全链路可诊断回测 |

**不做**：Swarm / Live / React / 完整商业 CNE5 / L2 逐笔 / 456 因子搬迁。

---

## 4. `research.db` Schema（Week1 末冻结）

Owner：实现方。变更须显式文档修订。

### 4.1 已有

- `bars` / `consensus_snapshots` / `universe_snapshots`

### 4.2 新增（冻结）

**交易日**

```sql
trade_calendar(
  trade_date TEXT PRIMARY KEY,  -- YYYY-MM-DD
  is_open INTEGER NOT NULL DEFAULT 1
);
```

**宏观（长表）**

```sql
macro_series(
  indicator TEXT NOT NULL,     -- pmi_mfg / cpi_yoy / m2_yoy ...
  asof TEXT NOT NULL,          -- 指标期或发布对齐日 YYYY-MM-DD
  value REAL,
  unit TEXT,                   -- index_point | ratio | CNY_yi | ...
  frequency TEXT,              -- monthly | quarterly | daily
  source TEXT,
  fetch_time TEXT,
  PRIMARY KEY (indicator, asof, source)
);
```

**因子面板（长表，热数据默认 60 交易日）**

```sql
factor_values(
  asof TEXT NOT NULL,          -- 交易日
  code TEXT NOT NULL,
  factor_id TEXT NOT NULL,     -- ep / mom_1m / risk_size ...
  value REAL,
  purpose TEXT NOT NULL,       -- alpha | risk
  PRIMARY KEY (asof, code, factor_id, purpose)
);
-- INDEX (factor_id, asof), (code, asof), (purpose, asof, factor_id)
```

写入：**按日 `executemany` + 单事务**；禁止逐股逐因子单条 INSERT。  
冷数据：按月归档表或后续分区；热查不扫冷表。Phase1 可先限制 universe≤2000 以降压。

**微观信号（日频）**

```sql
micro_signals(
  asof TEXT NOT NULL,
  code TEXT NOT NULL,
  signal_id TEXT NOT NULL,     -- northbound_flow / dt_seat_net ...
  value REAL,
  unit TEXT,
  meta_json TEXT,
  PRIMARY KEY (asof, code, signal_id)
);
```

**按需结果（可选归档，非强制每次写）**

```sql
run_artifacts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,                   -- var | stress | backtest_attr
  created_at TEXT,
  payload_json TEXT
);
```

---

## 5. 工具信封 `_meta`（所有 Phase1 数值工具）

```json
"_meta": {
  "source": "akshare|eastmoney|local|calc",
  "fetch_time": "ISO-8601",
  "stale": false,
  "frequency": "daily|monthly|quarterly|event",
  "unit": "CNY_yuan|CNY_wan|CNY_yi|ratio|index_point|none"
}
```

L1 工具链除不崩溃外必须：

- 月频被日频「连续 N 日」逻辑消费 → **fail**  
- 金额 `unit` 不一致且未换算 → **fail**  
- 字段用规范名；禁止静默 `null` 当 0

---

## 6. 验收

### 6.1 工程（阻断）

| 项 | Pass |
|----|------|
| 日历 | `is_trading_day` 与新浪交易日列表一致（抽样） |
| 宏观 | PMI/CPI/M2 关键字段可解析；`_meta` 齐全；关键指标与第二源或官宣对照误差策略见实现 note |
| 因子 | 目标因子集算完不报错；有 IC 序列输出 |
| Layer1 薄 | Top5 赢家/输家 + 总 PnL |
| Layer1 厚 | 出场原因分组 + 持仓区间 + 剔 Top5 |
| Layer2 | β_HS300、β_ZZ500 + t 值 |
| Schema | Week1 末 DDL 已合入且写入路径 bulk |

### 6.2 Alpha（不阻断，打标）

| 项 | 行为 |
|----|------|
| 符号与**锁定假设表**不符 | `invert_signal_note` |
| IC_IR≈0 | `weak_alpha_note` |
| **不用** IC_IR>0.02 作合入门禁 | — |

### 6.3 Prompt 行为（L2，Prompt 变更后必重跑）

| 输入 | 期望 |
|------|------|
| 「找低估值高ROE的股票」 | A 股筛选工具，非美股 |
| 「分析600519」 | 识别为 A 股 |
| 「PMI怎么样了」 | `get_macro_data` |
| 「最近什么热门」 | A 股热度/板块/涨停类，非美股 |

### 6.4 验证分层

| 层 | 方式 |
|----|------|
| L0 | `pytest` 单测/契约 |
| L1 | 无 LLM 的固定工具链脚本 + unit/frequency 断言 |
| L2 | 固定 user 句 + 断言 tool 名集合 |

**合入门禁 = L0+L1 绿；L2 失败阻断 Prompt/路由相关改动。**

---

## 7. Week1 DoD（已完成）

- [x] 本文档合入  
- [x] `market/trade_calendar.py` + `get_trade_calendar`；回测日轴优先交易所日历  
- [x] `research_store` 扩展上述 DDL  
- [x] `get_macro_data`（至少 PMI/CPI/M2/GDP）+ `_meta` + 可选落库  
- [x] `envelope`/`ok` 支持规范化 `_meta`  
- [x] `prompts/base.md` A 股默认语境  
- [x] 单测：日历、宏观信封、schema  

## 7b. Week2 DoD（已完成）

- [x] `universe_asof` 接入 `run_backtest` / `build_factor_panel`  
- [x] `market/factor_zoo.py`：`alpha_*` / `risk_*` 命名空间（价量 v0）  
- [x] `build_factor_panel`：截面计算 + bulk 写 `factor_values` + IC/符号 note  
- [x] Barra-lite 扩至多个 `risk_*`（股票数不足时自动缩维）  
- [x] 薄 Layer1：`metrics.layer1_attribution`（Top5 赢家/输家 + total_pnl）  
- [x] 单测 `tests/test_phase1_week2.py`  

## 7c. Week3 DoD（已完成）

- [x] `analyze_dragon_tiger`：席位启发式分类 + micro_signals  
- [x] `northbound_signal`：分位/连续净流入（CNY_wan）  
- [x] `calc_var` / `run_stress_test`（2015/2018/2020/2022 情景）  
- [x] Layer1 加厚：出场原因、持仓区间、剔 Top5  
- [x] 单测 `tests/test_phase1_week3.py`  

> 周末演示：宏观+因子+龙虎榜/北向 → 综合信号；回测看加厚 Layer1。
---

## 8. 风险登记（摘要）

| 风险 | 缓解 |
|------|------|
| akshare 不可靠 | `_meta` + stale；关键宏观双源/对照（能做则做） |
| 日历只做 tool | 回测引擎消费 |
| 无 universe 接线 | Week2 强制 asof 快照 |
| 因子面板写入打爆 SQLite | 长表 + bulk + 热 60 日 + 可缩池 |
| Week4 归因挤爆 | Layer1 厚→Week3；Week4 仅 Layer2+risk |
| L1 只抓崩溃 | unit/frequency 断言 |
| Prompt/Scenario 耦合 | Prompt 后重跑 L2 |

---

## 9. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-07-14 | 初版冻结：周计划、长表 schema、验收与验证分层 |
