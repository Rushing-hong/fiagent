# fiagent 优化调查报告

> 日期：2026-07-16  
> 范围：按「Agent Token / 数据管线 / LLM 成本 / Prompt 审计 / ROI 清单」框架，基于当前仓库实测  
> 方法：读代码 + 粗估（`context_budget`：约 2 字 ≈ 1 token）  
> 说明：本报告是调查与方案，不全量落地实现；优化稿见 `docs/PROMPT_BASE_OPTIMIZED_DRAFT.md`

---

## 〇、现状实测（基线）

| 项 | 实测 |
|----|------|
| `prompts/base.md` | 2318 字 ≈ **1159 tokens** |
| `build_system_prompt()`（base + 时间块 + Skills 索引） | ≈ **5995 字 / 2997 tokens** |
| 工具数 | **61** |
| 全量 tool schemas JSON | ≈ **36790 字 / 18395 tokens**（每轮 API 都带） |
| 最长 tool description | `run_backtest` ~600 字 |
| `context_budget` | **仅进度条**，无压缩/截断动作 |
| 近期已做（非本报告范围） | 轮内 schema 缓存、`load_skill` 去 XML、T+1/PIT 等功能修复 |

**单轮固定开销粗估**：system ~3k + tools ~18k ≈ **21k tokens/轮**（尚未计对话与工具结果）。6 轮对话 ≈ **126k+ 输入 tokens** 仅固定开销重复计费。

---

## 一、Agent 核心层（Token & 推理效率）

### 1.1 System Prompt 精简

#### 问题清单

| # | 位置 | 原因 |
|---|------|------|
| 1 | §工作流程 L2 + §原则 L19 | 「同级选用」重复两遍 |
| 2 | L23「如实传达」+ L25「数字查证」 | 可合并为「查证优先 + 如实转述」 |
| 3 | L19「无强制优先级」vs L21「必须先 load_skill」 | **非真矛盾**：前者比 tools↔skills，后者是 skill 内部契约；原文易误读，需改写澄清 |
| 4 | L26 时间规则 | 与 `build_time_context` + 近端时钟 **三处重复**（本文件内可压成一行指针） |
| 5 | §A股 L42–66 | 制度细则必要，但段落松散；ST 涨跌停与「特殊板块」可交叉引用压缩 |
| 6 | 「先思考」等元指令 | 模型已知，可极短 |

#### 优化后全文

见仓库草稿：[`docs/PROMPT_BASE_OPTIMIZED_DRAFT.md`](PROMPT_BASE_OPTIMIZED_DRAFT.md)  
（业务规则：T+1 / 涨跌停表 / 费用 / 板块门槛 / 举牌减持 / quality flag **均保留**）

#### Token 对比

| 版本 | 字符 | 粗估 tokens | 相对原版 |
|------|------|-------------|----------|
| 原 `base.md` | 2318 | ~1159 | — |
| 优化稿 | 1714 | ~857 | **−26.1%**（落在 20–30% 目标内） |

> 注意：真正进 API 的还有时间块 + Skills 索引（~1.8k tokens）。压缩 base 后 system 总降幅约 **8–12%**；更大头在 tools schema。

---

### 1.2 ReAct 循环 Token 预算

#### 现状结论

| 问题 | 证据 | 判定 |
|------|------|------|
| 历史线性膨胀 | `loop.py` 全量 `messages` 每轮送 API；无摘要 | **确认** |
| 工具结果无截断 | L220–224 原样 `content=result`；UI 折行≠API | **确认** |
| 重复调用 | 仅非 `repeatable` 且 count>1 时前缀警告，**不阻断** | 偏弱 |
| Prompt 缓存 | 未设静态前缀 / `prompt_cache` 相关参数 | 未用 |
| budget | 只 `show_context_progress`，**不触发压缩** | 确认 |

#### 建议（优先级 × 复杂度）

| 建议 | 优先级 | 复杂度 | 预期节省 | 要点 |
|------|--------|--------|----------|------|
| A. 只读工具结果 API 侧截断（>1500 字 → 头 500 + 标记） | **高** | 低 | 长会话 **−30~50%** input | UI 仍可展示全文；session 可存全文或双写 |
| B. ratio≥70% 时压缩「3 轮前」为单行摘要 | **高** | 中 | 长会话 **−20~40%** | 保留近 3 轮完整；摘要不删 tool_call 链合法性需小心 |
| C. 非 repeatable 第 4+ 次同名调用直接拒绝 | **中** | 低 | 少浪费轮次 | 警告不够；硬挡 + 提示改参 |
| D. System 静态前缀缓存 | **中** | 中 | 视供应商 **−50~80% 缓存命中部分** | DeepSeek 若支持 cache，需固定前缀字节；时钟近端注入破坏纯静态——**时钟宜留在后缀** |
| E. API 请求前剥离历史 `reasoning_content` | **高** | 低 | thinking 模式 **−10~30%** | 展示/落库可保留；`sanitize` 或组包时删 |

#### 关键片段（不改对外 API 签名）

```python
# core/loop.py — 工具结果写入 messages 前
READONLY_TRUNCATE = int(os.environ.get("FIAGENT_TOOL_TRUNCATE", "1500"))
READONLY_KEEP = int(os.environ.get("FIAGENT_TOOL_KEEP", "500"))

def _maybe_truncate_tool_result(ctx, name: str, result: str) -> str:
    if not ctx.is_readonly_tool(name):
        return result
    if len(result) <= READONLY_TRUNCATE:
        return result
    return result[:READONLY_KEEP] + f"\n…(已截断，原长 {len(result)} 字；需要细节请缩小查询)"

# 重复调用硬挡
if not ctx.is_repeatable_tool(name) and count >= 4:
    return f"已拒绝：`{name}` 本轮第 {count} 次。请基于已有结果继续，或更换参数后再调。"
```

```python
# core/context_budget.py 旁新增 compact_messages_if_needed(messages, usage)
# ratio>=0.7：将较早 user/assistant/tool 块折叠为一条 system/user 摘要
# 约束：不得破坏「assistant.tool_calls ↔ tool.tool_call_id」配对；
# 实践上只压缩「已完整结束的旧 turn」（无悬空 tool）
```

**预期**：中等长度投研对话（含多次 `get_market_data`/`screen_*`）单次 turn 输入可从「暴涨」压到可控；固定 18k schema 仍在，需配 1.3。

---

### 1.3 工具 Schema 精简

#### 基线

- 61 tools × 全量 schema ≈ **18.4k tokens/轮**
- Top 膨胀：`run_backtest`(600)、`screen_fundamental`(300)、`calc_dcf`(274)…

#### 方案排序

| 优先级 | 方案 | 预估 | 复杂度 | 风险 |
|--------|------|------|--------|------|
| **P0** | description 硬顶 80–120 字；细节进 skill/文档 | 18k → **10–12k（−30~40%）** | 低 | 低 |
| **P1** | 意图路由动态子集（A股热门 / 回测 / 宏观 / 文件…）默认 8–15 工具 | 18k → **3–6k（−70%）** | 中 | 漏工具 |
| **P2** | 元工具 `select_tool_group` 再展开 | 首轮极低，次轮展开 | 高 | 多一轮延迟 |
| **P3** | parameters 去 default/长枚举 | −5~10% | 低 | 低 |

**推荐路径**：先 P0 批量砍 description（ROI 最高、行为不变）→ 再 P1 按 `capability_groups` 做「默认 A 股组 + 显式跨市场再加载」。

---

## 二、数据管线（Pandas / 获取）

### 2.1 DataFrame 向量化（调查结论）

| 热点 | 位置倾向 | 方案 |
|------|----------|------|
| 逐股 `fetch_one` | `tools/backtest.py`、`build_factor_panel`、`market_data` | `ThreadPoolExecutor(4–8)` + 按日缓存键 |
| `upsert_bars` 逐行 INSERT | `research_store` | `executemany` 单事务 |
| 因子/IC | `factor_zoo` / `factor_analysis` | 已部分向量化；避免 `iterrows`；IC 已改为 forward |
| 截面筛选 | `fundamental_screen` 等 | 一次拉表 + 布尔索引，禁逐股 HTTP |

**目标对照**：全市场筛选 \<500ms、十年日线回测 \<5s —— 需「批量取数 + 引擎热路径」同时做，单改 pandas 不够。

### 2.2 回测引擎加速

| 可向量化 | 须保留日循环 |
|----------|--------------|
| 内置信号（MA/RSI）整列生成 | T+1 / 涨跌停拒单 / 冲击 / 现金 / 持仓再平衡（状态机） |
| 收益序列、指标、Layer2 OLS | 风格敞口按日（已对齐 sig_date） |

- **Numba**：仅对「无 pandas 对象」的纯数值热圈有意义；当前 Broker 面向对象，收益有限，**不优先**。  
- **多品种并行**：信号预计算可并行；撮合因共享 cash **难并行**，宜「向量化信号 + 单线程撮合」。  
- **预计算**：交易日历数组、ADV、limit 价（按日×码）可缓存。

### 2.3 数据获取

| 方向 | 预期（100 票基本面） |
|------|----------------------|
| 批量接口优先（东财 list/clist） | 20–50s → **2–5s** |
| 必逐股：线程池 8 + 限速 | ×3–8 |
| SQLite 缓存 `(fn, param_hash, asof)` TTL 1h | 同会话重复 **×10+** |

---

## 三、LLM 调用成本

| 问题 | 建议 | 月成本粗估假设 |
|------|------|----------------|
| Prompt caching | 固定 system（无时钟）+ 稳定 tool 子集作前缀；时钟放近端 | 日 500 对话×6 轮：固定 21k×3000 次；若 70% 前缀缓存命中，**固定开销可降 ~50%+**（视价与是否计费 cache） |
| `reasoning_content` | 通常计入 completion；限制 effort / 简单意图关 thinking | 简单问答关 thinking：**completion −30~70%** |
| 简单意图 | 「几点/列出工具」路由短路径：无 tools 或极简 tools | 显著 |
| 「基于结果继续」 | 不建议每轮硬塞；截断+拒重复更有效 | — |

配置建议：

```text
FIAGENT_TOOL_TRUNCATE=1500
FIAGENT_TOOL_KEEP=500
FIAGENT_CONTEXT_COMPACT_RATIO=0.7
FIAGENT_REASONING_SIMPLE_SKIP=1   # 未来：启发式关 thinking
```

---

## 四、System Prompt 质量审计（当前 base.md）

| 维度 | 分 | 问题 | 建议 |
|------|----|------|------|
| 指令冲突 | **7** | tools/skills「同级」vs「必须 load_skill」易误读 | 用优化稿 §1.3 句式澄清 |
| 信息密度 | **6** | 原则区重复；时间三处 | 合并；时间一行指针 |
| 优先级清晰度 | **6** | 缺「跨市场显式 > A股默认 > skill 细则」 | 加一行优先级 |
| 边界条件 | **5** | 「同时问 A+美」未写 | 表：显式多市场则并列工具，默认仍 A |
| Token 效率 | **6** | 可再压 ~26% | 采用优化稿 |

---

## 五、快速见效清单（ROI）

| 优先级 | 做什么 | 预期收益 | 现状 |
|--------|--------|----------|------|
| 🔴 P0 | System prompt 压缩 | Token −20~30%（base） | **草稿已出**，未替换正式 `base.md` |
| 🔴 P0 | 只读工具结果截断 | 长对话 −40%+ | **未做** |
| 🟡 P1 | 历史压缩 @70% | −30% | **未做**（budget 仅展示） |
| 🟡 P1 | Pandas/批量取数 | 数据 3–100× | 部分已知债 |
| 🟢 P2 | Schema 动态注入 / 砍 description | −50% schema | load_skill 已瘦；其余未做 |
| 🟢 P2 | HTTP/SQLite 缓存 | 取数 3–10× | 分钟 bars 有库；日线链路弱 |
| ⚪ P3 | Prompt 缓存 | 成本 −50~80%（命中时） | 依赖供应商 |

---

## 六、「万能钥匙」对照（loop + budget）

| 检查项 | 结论 | 下一步 |
|--------|------|--------|
| 1. 历史是否线性膨胀 | **是** | 70% 摘要压缩 |
| 2. 工具结果截断 | **无（API 路径）** | 只读截断 |
| 3. 重复调用是否够严 | **偏松（仅警告）** | ≥4 硬拒 |
| 4. system 静态前缀缓存 | **未做**；近端时钟破坏纯静态 | 拆「静态 system」+「动态时钟」 |

---

## 七、建议落地顺序（若继续改代码）

1. **替换** `prompts/base.md` ← 优化稿（或人工 diff 合入）  
2. **loop**：只读截断 + 重复调用硬拒 + API 前丢弃历史 reasoning  
3. **budget**：ratio 触发旧 turn 摘要  
4. **批量砍** 超长 tool description  
5. **取数线程池 + SQLite TTL**；回测信号预计算  

---

## 附录：与「安全类」审查的边界

本优化调查**不包含**本地 Agent 读 `.env` 等安全项（产品定位为本地自用）。
