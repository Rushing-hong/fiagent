# market/

**路径**：`/market/`

## 作用

A 股市场数据访问层（自 **Vibe-Trading** 迁移），封装非官方 HTTP 数据源。

## 文件

| 文件 | 说明 |
|------|------|
| `http.py` | 节流请求、JSON 解析 |
| `eastmoney.py` | 东方财富接口 |
| `market_data.py` | 行情聚合（含腾讯等） |
| `loaders.py` | 数据加载辅助 |
| `envelope.py` | 统一错误/数值裁剪响应格式 |

## 说明

数据来自公开网页接口，**非交易所官方 API**；仅供研究，注意频率与合规。
