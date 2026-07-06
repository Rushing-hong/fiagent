# 设计参考：Vibe-Trading 与 OpenCode

fiagent 不是这两个项目的 fork，而是在其思路上做的 **独立精简版**，面向本地 DeepSeek + A 股量化场景。

## Vibe-Trading

**路径**：`D:\Vibe-Trading`（开发时本地参考仓库）

### 借鉴内容

| 领域 | 说明 |
|------|------|
| Agent 范式 | ReAct：LLM 思考 → 工具调用 → 观察 → 再推理 |
| 工具生态 | A 股行情、资金流、公告、研报到 iwencai、web 搜索等工具划分 |
| Skills | 每个子目录 `SKILL.md` + 可选脚本/参考文档，按需 `load_skill` |
| 市场数据 | `market/` 层封装东财、腾讯等非官方接口，节流与错误信封 |
| Hooks | 在 session/turn/llm/tool 生命周期插入逻辑 |

### fiagent 中的对应

```
Vibe-Trading 概念     →  fiagent 位置
─────────────────────────────────────
agent loop           →  core/loop.py
tools/*              →  tools/*
skills/*             →  skills/*（迁移自 VT）
market data          →  market/*
hooks                →  hooks/
```

### 差异

- 默认 **DeepSeek** API（含 thinking 流式），非 VT 原模型配置
- 增加 **Textual TUI**、Session SQLite、流式输出、Esc 暂停
- 工具重复调用软提醒（非硬阻断）

---

## OpenCode

**路径**：`D:\opencode-dev\opencode-dev`（开发时本地参考仓库）

### 借鉴内容

| 领域 | 说明 |
|------|------|
| 折叠 UI | 思考、工具参数/结果默认折叠，只显示一行摘要 |
| 展开交互 | `e` 展开最新、`1-9` 展开历史项、`list` 列表（纯终端）；TUI 点击标题展开 |
| 行数截断 | `FIAGENT_THINK_MAX_LINES`、`FIAGENT_TOOL_MAX_LINES` 控制预览高度 |
| KV 偏好 | `ui/prefs.py` 持久化 `thinking_mode`、`ui_mode`（类似 OpenCode 的 UI 状态） |

### fiagent 中的对应

```
OpenCode 概念        →  fiagent 位置
─────────────────────────────────────
collapse preview     →  ui/collapse.py
thinking fold        →  ui.show_thinking + TUI Collapsible
expand slot          →  ui.expand_slot / tui_expand_slot
UI preferences       →  ui/prefs.py → data/ui_prefs.json
```

### 差异

- OpenCode 是完整 IDE Agent；fiagent 专注 **量化研究对话 + A 股工具**
- TUI 使用 **Textual** 全屏，而非 OpenCode 的终端渲染栈
- 无 OpenCode 的 LSP / 多工作区等 IDE 能力

---

## 致谢

感谢 Vibe-Trading 项目在 A 股 Agent 工具链上的探索，以及 OpenCode 在终端 Agent UX（折叠、偏好、信息密度）上的实践，为 fiagent 提供了清晰的可复用模式。
