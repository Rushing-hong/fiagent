---
name: industry-chain
description: A股产业链知识图谱——本地 ChainKnowledgeGraph 数据，query_industry_chain 查上下游/路径，结合行情与新闻做事件传导分析。
category: knowledge
---

# A 股产业链知识图谱

基于 [ChainKnowledgeGraph](https://github.com/liuhuanyong/ChainKnowledgeGraph) 的上市公司—行业—产品关系，用于**上下游传导**、**主题扩散**和**同业映射**（仅 A 股，不含港股/美股）。

## 1. 安装数据

1. 克隆或下载 [ChainKnowledgeGraph/data](https://github.com/liuhuanyong/ChainKnowledgeGraph/tree/main/data)  
2. 将多个 JSONL 合并为单一 `edges` 列表，或直接使用项目提供的合并格式  
3. 保存到 **`data/industry_chain.json`**（项目根目录下）  
4. 或设置环境变量 **`FIAGENT_INDUSTRY_CHAIN_PATH`** 指向你的 JSON 路径  

仓库内 **`data/industry_chain.json.example`** 为 10 条以内的光伏链示例，可复制改名试用。

未安装时 `query_industry_chain` 仍返回 `ok` 但 **`quality=degraded`**，并在 `note` 中提示安装步骤。

## 2. 工具用法

### 查直接邻居

```
query_industry_chain(entity="隆基绿能", action="neighbors")
query_industry_chain(entity="601012.SH", action="neighbors", direction="upstream")
```

`direction`: `both` | `out` | `in` | `upstream` | `downstream`

### 沿链追溯（多跳）

```
query_industry_chain(entity="工业硅", action=trace, depth=3, limit=30)
```

`trace` 与 `path` 等价；从起点 BFS 展开上下游实体。

### 图谱规模

```
query_industry_chain(action="stats")
```

## 3. 与行情 / 新闻组合（事件传导）

典型工作流：**图谱定范围 → 新闻定事件 → 行情验证**

### 示例：硅料涨价 → 光伏链

```
1. query_industry_chain(entity="多晶硅", action=trace, depth=2)
   → 上游：工业硅；下游：单晶硅片；关联公司：通威股份、隆基绿能…

2. get_stock_news(codes=["600438.SH","601012.SH"], limit=10)
   → 检索「硅料」「组件」涨价/减产等关键词

3. get_market_data(codes=["600438.SH","601012.SH"], period="daily", count=20)
   → 对比事件日前后涨跌幅与成交量
```

### 示例：光伏板块政策 → 组件龙头

```
1. query_industry_chain(entity="光伏", action=neighbors)
   → 下游应用指向光伏组件、单晶硅片

2. get_stock_news(query="光伏 装机 政策", limit=15)
   → 政策/招标/出口数据

3. get_market_data(codes=["688599.SH","601012.SH"], period="daily")
   → 天合光能、隆基绿能等龙头反应
```

### 传导分析要点

| 步骤 | 工具 | 目的 |
|------|------|------|
| 定链路 | `query_industry_chain` | 谁在上游/下游、几跳可达 |
| 定事件 | `get_stock_news` / `read_url` | 事件类型与时间 |
| 验证 | `get_market_data` | 价格/量是否同步 |
| 资金 | `get_fund_flow` | 龙头 vs 二线分化 |

引用图谱结论时若 `quality=degraded`，必须说明**本地图谱未安装或实体未命中**。

## 4. 光伏链参考（A 股）

```
工业硅 → 多晶硅 → 单晶硅片 → 光伏组件 → 光伏电站
         通威股份    隆基绿能    天合光能
```

常用代码：`600438.SH` 通威、`601012.SH` 隆基、`688599.SH` 天合。

## 5. 与 ai-industry-chain skill 的区别

| | industry-chain | ai-industry-chain |
|--|----------------|-------------------|
| 数据 | 本地 JSON 图谱 | 内置 AI 算力映射表 |
| 工具 | `query_industry_chain` | `get_market_breadth` 等 |
| 场景 | 任意行业上下游 | AI 光模块/PCB/HBM 主题 |

两者可并用：AI 主题用 `ai-industry-chain`；具体产品传导用本 skill + 图谱工具。
