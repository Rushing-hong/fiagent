"""读取工作区内文件内容。"""

from tools._fs import PathError, format_lines, resolve_path
from tools.base import BaseTool


class ReadTool(BaseTool):
    name = "read"
    summary = "读取工作区内的文件"
    description = (
        "读取文件内容。修改文件前应先 read 确认现有内容。"
        "大文件可用 offset/limit 按行分段读取。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径（相对工作区或绝对路径）"},
            "offset": {"type": "integer", "description": "起始行号，从 1 开始，默认 1"},
            "limit": {"type": "integer", "description": "最多读取的行数，默认读取全部"},
        },
        "required": ["path"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        try:
            file_path = resolve_path(ctx, args.get("path", ""))
        except PathError as e:
            return str(e)

        if not file_path.exists():
            return f"文件不存在: {file_path}"
        if not file_path.is_file():
            return f"不是文件: {file_path}"

        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"无法以文本读取（可能是二进制文件）: {file_path}"

        lines = content.splitlines()
        total = len(lines)
        offset = max(1, int(args.get("offset") or 1))
        limit = args.get("limit")

        if offset > total and total > 0:
            return f"offset 超出范围: 文件共 {total} 行"

        start_idx = offset - 1
        if limit is not None:
            end_idx = start_idx + max(1, int(limit))
            selected = lines[start_idx:end_idx]
        else:
            selected = lines[start_idx:]

        body = format_lines("\n".join(selected), start_line=offset)
        end_line = offset + len(selected) - 1 if selected else offset
        header = f"文件: {file_path}  (共 {total} 行，显示 {offset}-{end_line})"
        return f"{header}\n{body}"
