"""Run Python code tool — execute user-written scripts in the workspace.

Used when the agent needs to run calculations that can't be expressed
as a single tool call (e.g., custom factor computation, data transformation).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from tools._fs import PathError, resolve_path
from tools.base import BaseTool


class RunPythonTool(BaseTool):
    name = "run_python"
    summary = "执行工作区内的 Python 脚本"
    description = (
        "运行工作区内的 Python 脚本文件。用于执行自定义计算（因子生成、"
        "信号计算、数据格式转换等）。脚本输出到 stdout 的内容会被捕获返回。\n"
        "注意: 脚本文件必须先通过 write 工具创建。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file": {
                "type": "string",
                "description": "Python 脚本路径（相对工作区或绝对路径）",
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "命令行参数，传递给脚本的 sys.argv",
                "default": [],
            },
            "timeout": {
                "type": "integer",
                "default": 30,
                "description": "超时秒数",
            },
        },
        "required": ["file"],
    }
    # 任意脚本可能写盘/联网/改 DB，不可与只读工具并行
    is_readonly = False
    repeatable = False

    def execute(self, args: dict[str, Any], ctx: Any) -> str:
        try:
            file_path = resolve_path(ctx, str(args.get("file", "")))
        except PathError as e:
            return f"路径错误: {e}"

        if not file_path.exists():
            return f"文件不存在: {file_path}"
        if not file_path.is_file():
            return f"不是文件: {file_path}"

        timeout = min(int(args.get("timeout", 30)), 120)
        script_args = args.get("args") or []

        try:
            result = subprocess.run(
                [sys.executable, str(file_path)] + [str(a) for a in script_args],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(ctx.root),
                env={**__import__("os").environ, "PYTHONUNBUFFERED": "1"},
            )
        except subprocess.TimeoutExpired:
            return f"脚本执行超时 ({timeout}s)"
        except Exception as e:
            return f"脚本执行失败: {e}"

        output = result.stdout
        if result.stderr:
            output += "\n\n[stderr]\n" + result.stderr

        if result.returncode != 0:
            return f"脚本返回非零退出码 {result.returncode}:\n{output}"

        return output
