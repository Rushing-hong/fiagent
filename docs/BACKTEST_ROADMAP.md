# A股回测增强路线图

> 目标：在现有日频引擎上分阶段逼近「全息阿尔法」压力测试，而不是一次重写。

## 已完成（P0）

- 涨跌停锁仓拒单、信号延迟、√冲击成本、拒单统计

## 已完成（P1）

- 停牌 / 现金计息 / `build_tradable_universe` / `build_event_signals`

## 已完成（P2）

- 股指期货对冲子账户、多 sleeve 融合与归因、行业权重上限

## 已完成（P3）

- Black-Litterman 简版、动量暴露帽、上市满 N 日、波动择时对冲比

## 已完成（P4）

- size/vol 风格帽、`track_consensus`、`interval` 分钟入口、研报 `beginTime` 修复

## 已完成（P5）

- **本地研究库** `data/research.db`：分钟 K 缓存、共识快照累积、universe 点位快照
- **Barra-lite 风险**：`analyze_portfolio_risk`（mom/size/vol±行业，系统/特异分解）
- **点位成分**：`build_tradable_universe(save_snapshot=true)` + `load_pit_universe`
- **分钟引擎修复**：日内索引不再被 `bdate_range` 压成日频

### 相关工具（P5）

`analyze_portfolio_risk` / `load_pit_universe` / 增强缓存的分钟拉取 / 共识本地史

## 仍需外部条件（无法免费完整替代）

1. 商业级长历史分钟/Level2 撮合
2. 商业 Barra/CNE 全因子协方差与官方风险归因
3. 付费一致预期历史点位面板（Wind/朝阳等）
4. 交易所官方历史全市场可交易名单回放

本仓库用本地累积快照 + 简模型逼近，`quality=degraded` 处已标明。

## 边界

- 期货单合约日线 + 换月成本近似
- sleeve 归因为暴露加权拆分
- BL / Barra-lite / 共识修订均为工程化简版
- 点位 universe 依赖你持续跑 `build_tradable_universe` 存快照
- 分钟历史长度取决于 akshare 近端 + 本地缓存累积
