---
name: esg-analysis
description: A 股 ESG 与可持续披露分析——碳价背景、巨潮 ESG/社会责任公告检索、结合财报与公开信息，面向境内上市公司。
category: analysis
---

# A 股 ESG 分析

面向 **沪深北 A 股** 的环境、社会与治理研究。使用 fiagent 工具链，不覆盖美股、加密货币。

## 可用工具

| 分析需求 | 工具 |
|---------|------|
| 国内/欧盟碳价背景 | `get_carbon_prices` — 可选 exchange、limit |
| ESG/可持续公告检索 | `search_esg_reports` — keyword、code、limit |
| 研究入口与碳价快照 | `get_esg_overview` — 可选 code，含巨潮检索指引 |
| 财务与治理底稿 | `get_financial_statements` — 三大报表、审计相关科目 |
| 政策/行业 ESG 动态 | `web_search` — 补充交易所指引、评级机构公开信息 |

## 推荐工作流

### 1. 定范围（单股或主题）

```
用户提供 code 或行业主题
→ get_esg_overview(code=..., keyword="ESG")
→ 查看 recent_reports 与 cninfo_usage
```

### 2. 公告与披露

```
search_esg_reports(keyword="可持续发展", code="600519.SH", limit=20)
→ 逐条核对 title/date/url
→ 对 quality=degraded 的结果在报告中注明数据来源降级
```

关键词可轮换：`ESG`、`社会责任`、`环境信息`、`绿色金融`、`碳中和`。

### 3. 财务与治理交叉验证

```
get_financial_statements(code, report_type="income"/"balance"/"cashflow")
→ 关注：环保支出、政府补助、关联交易、内控审计意见（若有）
→ 与 ESG 报告中的量化指标对照，标注口径差异
```

### 4. 碳价与行业映射（背景，非个股评级）

```
get_carbon_prices(exchange="湖北", limit=30)
→ 高耗能行业（电力、钢铁、水泥、化工）讨论碳成本传导时使用
→ 明确：碳价为市场成交价，不等于企业实际履约成本
```

### 5. 外部公开信息（可选）

```
web_search("A股 ESG 披露指引 2025")
→ 仅引用可核对来源（交易所、证监会、公司公告 PDF）
```

## 输出结构

```markdown
## ESG 评估摘要（A 股）

### 披露与公告
- 近期巨潮命中：…（附 date + url）
- 披露完整性：…

### 财务与治理要点
- …

### 碳/环境背景（如适用）
- 最新碳价：…（注明 exchange 与 trade_date）

### 风险与局限
- 数据来源 quality 说明
- 未覆盖的第三方 ESG 评分需用户自行核实
```

## 执行要点

1. **以公告为准**：ESG 报告、社会责任报告以巨潮 PDF 为权威来源。
2. **标注 quality**：工具返回 `degraded` / `partial` 时，结论中必须说明。
3. **A 股语境**：使用 `.SH` / `.SZ` / `.BJ` 代码；勿套用美股 SASB/GRI 模板而不做披露差异说明。
4. **不臆测评级**：无工具数据时不编造 MSCI/Wind ESG 分数；可说明缺失并给出检索路径。
5. **碳价≠ESG 分**：碳价仅作宏观/行业背景，不可直接推导个股 ESG 等级。
