---
name: fundamental-filter
description: Fundamental stock screening — filter A-shares by PE/PB/ROE/market_cap/dividend_yield for value, growth, or dividend strategies.
category: flow
---

# 基本面筛选

## fiagent 可用工具

| 工具 | 用途 |
|------|------|
| `screen_fundamental` | **主力工具** — 按 PE/PB/ROE/市值/股息率一键筛选全 A 股 |
| `get_financial_statements` | 深度财务分析 — 获取三大报表原始数据（杜邦分解、现金流质量） |
| `screen_market` | 按涨跌幅/量/额/PE/市值排序 |
| `iwencai_search` | 自然语言选股（"市盈率低于15的银行股"） |

## 经典策略模板

### 格雷厄姆式深度价值

```
screen_fundamental(max_pe=10, max_pb=1.5, min_dividend_yield=3, sort_by="pe", top_n=20)
```

筛选条件：PE≤10（低估值）+ PB≤1.5（破净边缘）+ 股息率≥3%（有现金回报）

### 合理估值高成长（GARP）

```
screen_fundamental(max_pe=30, min_roe=20, max_pb=5, min_market_cap=100, sort_by="roe", top_n=20)
```

筛选条件：PE≤30（不贵）+ ROE≥20%（高盈利质量）+ 市值≥100亿（避免小盘操纵）

### 高股息策略

```
screen_fundamental(min_dividend_yield=4, max_pe=20, min_market_cap=50, sort_by="dividend_yield", top_n=20)
```

筛选条件：股息率≥4% + PE≤20（盈利支撑分红）+ 市值≥50亿

### 低 PB 反转

```
screen_fundamental(max_pb=1.0, min_roe=5, sort_by="pb", top_n=20)
```

筛选条件：PB≤1（破净）+ ROE≥5%（不是持续亏损的僵尸企业）

## 进阶：结合财报深度分析

先用 `screen_fundamental` 粗筛 → 再用 `get_financial_statements` 对候选股票做深度检查：

```
1. screen_fundamental(max_pe=15, min_roe=15, top_n=30)

2. 对候选中的每只股票:
   get_financial_statements(code="600519.SH", statement="indicators", period="annual")
   
3. 检查:
   - CFO/净利润 > 1.0？  (盈利有现金支撑)
   - 应收账款增速 < 营收增速？ (收入质量好)
   - 商誉/净资产 < 30%？ (无并购暴雷风险)
   - 扣非/归母 > 80%？ (利润来自主营业务)
```

## 财务造假红旗检测

12 个需要关注的信号（详见 `financial-statement` skill）：

| # | 红旗 | 检测 |
|---|------|------|
| 1 | 存贷双高 | 货币资金高 + 有息负债同时 > 营收 30% |
| 2 | 应收暴增 | 应收增速 > 营收增速 × 1.5，持续 2 季 |
| 3 | 经营现金流为负 | CFO 连续 2 年为负但净利润为正 |
| 4 | 审计意见异常 | 非标准无保留意见 |
| 5 | 商誉占比高 | 商誉/净资产 > 30% |

筛查到 2+ 个红旗 → 建议回避。

## PE/PB/ROE 阈值参考（A 股历史中位数）

| 行业 | 合理 PE | 合理 PB | 合理 ROE |
|------|---------|---------|---------|
| 白酒 | 20-35 | 5-10 | 20-35% |
| 银行 | 5-8 | 0.5-1.0 | 10-14% |
| 保险 | 10-15 | 1.5-2.5 | 10-18% |
| 消费 | 20-30 | 3-6 | 15-25% |
| 科技 | 30-50 | 3-8 | 10-20% |
| 医药 | 25-40 | 3-7 | 12-20% |
| 地产 | 5-10 | 0.5-1.0 | 5-10% |
| 公用事业 | 10-20 | 1-2 | 8-12% |
