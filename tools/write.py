"""写入或覆盖工作区内文件。"""

from tools._fs import PathError, resolve_path
from tools.base import BaseTool


class WriteTool(BaseTool):
    name = "write"
    summary = "创建或覆盖工作区内的文件"
    description = (
        "写入文件。若文件不存在则创建；若已存在则整体覆盖。"
        "仅用于新建文件或需要重写全部内容的场景；局部修改请用 edit。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径（相对工作区或绝对路径）"},
            "content": {"type": "string", "description": "要写入的完整文件内容"},
        },
        "required": ["path", "content"],
    }
    is_readonly = False
    repeatable = False

    def execute(self, args: dict, ctx) -> str:
        try:
            file_path = resolve_path(ctx, args.get("path", ""))
        except PathError as e:
            return str(e)

        content = args.get("content")
        if content is None:
            return "缺少参数: content"

        file_path.parent.mkdir(parents=True, exist_ok=True)
        existed = file_path.exists()
        file_path.write_text(content, encoding="utf-8")
        action = "已覆盖" if existed else "已创建"
        lines = 0 if content == "" else content.count("\n") + (0 if content.endswith("\n") else 1)
        return f"{action}: {file_path}  ({lines} 行, {len(content.encode('utf-8'))} 字节)"
