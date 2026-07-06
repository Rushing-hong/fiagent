"""按精确匹配替换文件中的文本片段。"""

from tools._fs import PathError, resolve_path
from tools.base import BaseTool


class EditTool(BaseTool):
    name = "edit"
    summary = "精确查找替换，局部修改文件"
    description = (
        "精确替换文件中的一段文本。old_string 必须与文件内容完全一致"
        "（包括空格与换行）。修改前务必先 read。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径（相对工作区或绝对路径）"},
            "old_string": {"type": "string", "description": "要被替换的原始文本（精确匹配）"},
            "new_string": {"type": "string", "description": "替换后的新文本"},
            "replace_all": {
                "type": "boolean",
                "description": "是否替换所有匹配项，默认 false（仅替换唯一匹配）",
            },
        },
        "required": ["path", "old_string", "new_string"],
    }
    is_readonly = False
    repeatable = False

    def execute(self, args: dict, ctx) -> str:
        try:
            file_path = resolve_path(ctx, args.get("path", ""))
        except PathError as e:
            return str(e)

        if not file_path.exists():
            return f"文件不存在: {file_path}"
        if not file_path.is_file():
            return f"不是文件: {file_path}"

        old_string = args.get("old_string", "")
        new_string = args.get("new_string", "")
        replace_all = bool(args.get("replace_all", False))

        if old_string == new_string:
            return "old_string 与 new_string 相同，未做修改"

        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"无法以文本编辑: {file_path}"

        count = content.count(old_string)
        if count == 0:
            return "未找到 old_string，请先用 read 确认文件内容后重试"
        if count > 1 and not replace_all:
            return (
                f"old_string 在文件中出现 {count} 次，"
                "请提供更多上下文使其唯一，或设置 replace_all=true"
            )

        replaced = count if replace_all else 1
        updated = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
        file_path.write_text(updated, encoding="utf-8")
        return f"已编辑: {file_path}  (替换 {replaced} 处)"
