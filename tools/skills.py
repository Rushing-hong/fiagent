"""Skill 相关工具：渐进披露 + CRUD。"""

from tools.base import BaseTool


class LoadSkillTool(BaseTool):
    name = "load_skill"
    summary = "加载 skill 完整指令"
    description = "加载本地 skill 的完整指令。匹配 description 时应先调用此工具。"
    dynamic_schema = True
    is_readonly = True

    def build_schema(self, ctx) -> dict:
        # Skills 短索引已在 system prompt；此处不再嵌入全量 XML，避免每轮 schema 膨胀
        names = [s.name for s in ctx.enabled_skills()]
        name_prop: dict = {
            "type": "string",
            "description": "skill 名称（见 system 中 Skills 短索引）",
        }
        if names:
            name_prop["enum"] = names
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "加载 skill 的完整 SKILL.md 正文（与取数 tools 同级，按需选用）。"
                    "决定使用某个 skill 时先调用本工具获取全文，再按全文执行；"
                    "不要只凭短描述臆造该 skill 的流程。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"name": name_prop},
                    "required": ["name"],
                },
            },
        }

    def execute(self, args: dict, ctx) -> str:
        from ui.prefs import is_skill_enabled

        name = args.get("name", "")
        if name and not is_skill_enabled(name):
            return f"skill `{name}` 已被用户禁用（Ctrl+P → 管理 Skills 可重新开启）"
        body = ctx.skills.load_body(name)
        if body.startswith("未找到"):
            return body
        return f'<skill name="{name}">\n{body}\n</skill>'


class SaveSkillTool(BaseTool):
    name = "save_skill"
    summary = "创建或覆盖用户 skill"
    description = "在 skills/user/ 下创建或覆盖 SKILL.md。内置 skill 不可覆盖。"
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "skill 名称（小写+连字符）"},
            "description": {"type": "string", "description": "skill 描述（写入 frontmatter）"},
            "content": {"type": "string", "description": "SKILL.md 正文（不含 frontmatter）"},
        },
        "required": ["name", "description", "content"],
    }
    is_readonly = False
    repeatable = False

    def execute(self, args: dict, ctx) -> str:
        return ctx.skills.save(
            name=args.get("name", ""),
            description=args.get("description", ""),
            content=args.get("content", ""),
        )


class PatchSkillTool(BaseTool):
    name = "patch_skill"
    summary = "精确替换用户 skill 内容"
    description = "在用户 skill 的 SKILL.md 中精确替换文本。仅可修改 user skill。"
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "skill 名称"},
            "old_text": {"type": "string", "description": "要被替换的原文（精确匹配）"},
            "new_text": {"type": "string", "description": "替换后的文本"},
        },
        "required": ["name", "old_text", "new_text"],
    }
    is_readonly = False
    repeatable = False

    def execute(self, args: dict, ctx) -> str:
        return ctx.skills.patch(
            name=args.get("name", ""),
            old_text=args.get("old_text", ""),
            new_text=args.get("new_text", ""),
        )


class DeleteSkillTool(BaseTool):
    name = "delete_skill"
    summary = "删除用户 skill"
    description = "删除 skills/user/ 下的 skill。内置 skill 不可删除。"
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "要删除的 skill 名称"},
        },
        "required": ["name"],
    }
    is_readonly = False
    repeatable = False

    def execute(self, args: dict, ctx) -> str:
        return ctx.skills.delete(args.get("name", ""))
