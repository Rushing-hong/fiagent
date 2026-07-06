"""在工作区内按正则搜索文件内容。"""

import fnmatch
import re
from pathlib import Path

from tools._fs import PathError, resolve_path
from tools.base import BaseTool

SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    "data", ".cursor", "dist", "build",
}
MAX_FILE_BYTES = 512 * 1024


class GrepTool(BaseTool):
    name = "grep"
    summary = "正则搜索工作区文件内容"
    description = "在工作区内搜索匹配正则的文本。可指定文件或目录，支持 glob 过滤。"
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式搜索模式"},
            "path": {"type": "string", "description": "搜索路径（文件或目录），默认工作区根目录"},
            "glob": {"type": "string", "description": "文件名 glob 过滤，如 *.py"},
            "ignore_case": {"type": "boolean", "description": "是否忽略大小写，默认 false"},
            "max_results": {"type": "integer", "description": "最多返回的匹配行数，默认 100"},
        },
        "required": ["pattern"],
    }
    is_readonly = True

    def _iter_files(self, root: Path, glob_pattern: str | None) -> list[Path]:
        if root.is_file():
            return [root]
        files: list[Path] = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if glob_pattern and not fnmatch.fnmatch(path.name, glob_pattern):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            files.append(path)
        return files

    def _search_file(self, file_path: Path, root: Path, regex: re.Pattern, limit: int) -> list[str]:
        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return []
        rel = file_path.relative_to(root)
        matches = []
        for lineno, line in enumerate(content.splitlines(), start=1):
            if regex.search(line):
                matches.append(f"{rel}:{lineno}:{line}")
                if len(matches) >= limit:
                    break
        return matches

    def execute(self, args: dict, ctx) -> str:
        pattern = args.get("pattern", "")
        if not pattern:
            return "缺少参数: pattern"

        flags = re.IGNORECASE if args.get("ignore_case") else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return f"无效正则: {e}"

        try:
            search_root = resolve_path(ctx, args.get("path") or ".")
        except PathError as e:
            return str(e)

        if not search_root.exists():
            return f"路径不存在: {search_root}"

        max_results = max(1, int(args.get("max_results") or 100))
        workspace = ctx.root.resolve()
        results: list[str] = []
        truncated = False

        for file_path in self._iter_files(search_root, args.get("glob")):
            remaining = max_results - len(results)
            if remaining <= 0:
                truncated = True
                break
            results.extend(self._search_file(file_path, workspace, regex, remaining))
            if len(results) >= max_results:
                truncated = True
                break

        if not results:
            scope = search_root.relative_to(workspace) if search_root != workspace else "."
            return f"未找到匹配: pattern={pattern!r}  path={scope}"

        header = f"找到 {len(results)} 处匹配"
        if truncated:
            header += f"（已达上限 {max_results}）"
        return header + "\n" + "\n".join(results)
