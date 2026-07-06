# session/store.py

**路径**：`/session/store.py`

## 作用

`SessionStore`：SQLite 持久化多会话对话。

## 功能

- 创建 / 列表 / 加载 / 保存 / 删除 / 重命名 session
- `auto_title()`：首条用户消息截取为标题
- `maybe_auto_purge()`：清理超过 `RETENTION_DAYS` 未更新的会话
- **线程安全**：`threading.local` 每线程独立 DB 连接 + WAL

## 数据文件

`data/agent.db`
