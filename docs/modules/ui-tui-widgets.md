# ui/tui/widgets.py

**路径**：`/ui/tui/widgets.py`

## 作用

`ImeInput`：继承 Textual `Input` 的底部输入框。

## 行为

- 有内容时隐藏 `Message /help` 占位符
- `-has-text` / `-focused` 类名驱动高亮样式（`tui.tcss`）
- 聚焦时顶栏蓝色描边
