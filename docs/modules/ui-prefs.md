# ui/prefs.py

**路径**：`/ui/prefs.py`

## 作用

UI 偏好读写 `data/ui_prefs.json`（借鉴 OpenCode KV 思路）。

## 字段

| 键 | 值 | 说明 |
|----|-----|------|
| `thinking_mode` | `show` / `hide` | 思考过程展开或折叠一行 |
| `ui_mode` | `tui` / `plain` | 默认界面模式 |

## API

`get_thinking_mode`、`toggle_thinking_mode`、`get_ui_mode`、`set_ui_mode`、`ui_mode_label`
