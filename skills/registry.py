import re
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    bundled: bool = True


class SkillRegistry:
    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = skills_dir
        self.user_dir = skills_dir / "user"
        self._skills: list[Skill] = []
        self.user_dir.mkdir(parents=True, exist_ok=True)
        self.refresh()

    @staticmethod
    def _parse_frontmatter(text: str) -> tuple[dict, str]:
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
        if not match:
            return {}, text
        meta: dict = {}
        for line in match.group(1).splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip()
        return meta, match.group(2).strip()

    @staticmethod
    def _format_skill_file(name: str, description: str, body: str) -> str:
        return f"---\nname: {name}\ndescription: {description}\n---\n\n{body.strip()}\n"

    def _load_from_file(self, skill_file: Path, bundled: bool) -> Skill | None:
        if skill_file.name != "SKILL.md":
            return None
        text = skill_file.read_text(encoding="utf-8")
        meta, _ = self._parse_frontmatter(text)
        return Skill(
            name=meta.get("name") or skill_file.parent.name,
            description=meta.get("description", ""),
            path=skill_file,
            bundled=bundled,
        )

    def refresh(self) -> None:
        by_name: dict[str, Skill] = {}

        if self.skills_dir.exists():
            for skill_file in sorted(self.skills_dir.glob("*/SKILL.md")):
                if skill_file.parent.name == "user":
                    continue
                skill = self._load_from_file(skill_file, bundled=True)
                if skill:
                    by_name[skill.name] = skill

        if self.user_dir.exists():
            for skill_file in sorted(self.user_dir.glob("*/SKILL.md")):
                skill = self._load_from_file(skill_file, bundled=False)
                if skill:
                    by_name[skill.name] = skill

        self._skills = list(by_name.values())

    def all(self) -> list[Skill]:
        return list(self._skills)

    def get(self, name: str) -> Skill | None:
        for skill in self._skills:
            if skill.name == name:
                return skill
        return None

    def load_body(self, name: str) -> str:
        skill = self.get(name)
        if skill is None:
            return f"未找到 skill: {name}"
        text = skill.path.read_text(encoding="utf-8")
        _, body = self._parse_frontmatter(text)
        return body

    @staticmethod
    def _short_desc(text: str, *, limit: int = 48) -> str:
        """System / tool schema 用的短摘要（渐进披露第一层）。"""
        text = " ".join((text or "").split())
        if len(text) <= limit:
            return text
        cut = text[: limit - 1]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        return cut.rstrip("，,;；、./") + "…"

    def format_catalog_xml(self, skills: list[Skill] | None = None) -> str:
        """供 load_skill 的 schema：仅短描述，全文仍靠 execute 返回。"""
        items = self._skills if skills is None else skills
        if not items:
            return ""
        lines = ["<available_skills>"]
        for skill in items:
            scope = "bundled" if skill.bundled else "user"
            lines.append("  <skill>")
            lines.append(f"    <name>{skill.name}</name>")
            lines.append(
                f"    <description>{self._short_desc(skill.description)}</description>"
            )
            lines.append(f"    <scope>{scope}</scope>")
            lines.append("  </skill>")
        lines.append("</available_skills>")
        return "\n".join(lines)

    def get_descriptions(self, skills: list[Skill] | None = None) -> str:
        """System prompt 技能索引：名称 + 短触发语，勿塞全文。"""
        items = self._skills if skills is None else skills
        if not items:
            return "（无可用 skill）"
        lines = []
        for skill in items:
            tag = "内置" if skill.bundled else "用户"
            short = self._short_desc(skill.description)
            lines.append(f"- [{tag}] `{skill.name}`: {short}")
        return "\n".join(lines)

    def save(self, name: str, description: str, content: str) -> str:
        name = name.strip()
        if not name:
            return "skill 名称不能为空"
        existing = self.get(name)
        if existing and existing.bundled:
            return f"内置 skill `{name}` 不可覆盖，请换名称"

        skill_dir = self.user_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(
            self._format_skill_file(name, description, content),
            encoding="utf-8",
        )
        self.refresh()
        action = "已更新" if existing else "已创建"
        return f"{action}用户 skill: {name}"

    def patch(self, name: str, old_text: str, new_text: str) -> str:
        skill = self.get(name)
        if skill is None:
            return f"未找到 skill: {name}"
        if skill.bundled:
            return f"内置 skill `{name}` 不可修改，请 save_skill 创建用户版本"

        text = skill.path.read_text(encoding="utf-8")
        if old_text not in text:
            return "未找到 old_text，请先用 load_skill 确认内容"
        skill.path.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        self.refresh()
        return f"已更新 skill: {name}"

    def delete(self, name: str) -> str:
        skill = self.get(name)
        if skill is None:
            return f"未找到 skill: {name}"
        if skill.bundled:
            return f"内置 skill `{name}` 不可删除"
        shutil.rmtree(skill.path.parent)
        self.refresh()
        return f"已删除用户 skill: {name}"
