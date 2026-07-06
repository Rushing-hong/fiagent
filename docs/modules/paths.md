# paths.py

**路径**：`/paths.py`

## 作用

集中定义项目根目录与常用路径，避免模块移动后 `Path(__file__).parent` 不一致。

## 常量

| 常量 | 路径 |
|------|------|
| `PROJECT_ROOT` | 仓库根目录 |
| `DATA_DIR` | `data/` |
| `ENV_PATH` | `.env` |

## 使用方

`agent.py`、`session/store.py`、`ui/__init__.py`、`ui/prefs.py`、`hooks/registry.py`
