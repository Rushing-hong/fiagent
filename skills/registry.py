import re
import shutil
from dataclasses import dataclass
from pathlib import Path

# 仅允许安全目录名，防止 save/delete 路径穿越
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    bundled: bool = True


def _validate_skill_name(name: str) -> str | None:
    token = (name or "").strip()
    if not token or not _SKILL_NAME_RE.fullmatch(token):
        return None
    if ".." in token or "/" in token or "\\" in token:
        return None
    return token


class SkillRegistry:
    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = skills_dir
        self.user_dir = skills_dir / "user"
        self._skills: list[Skill] = []
        self._by_name: dict[str, Skill] = {}
        self._file_cache: dict[Path, tuple[tuple[int, int, bool], Skill]] = {}
        self.user_dir.mkdir(parents=True, exist_ok=True)
        self.refresh()

    def _user_skill_dir(self, name: str) -> Path | None:
        safe = _validate_skill_name(name)
        if not safe:
            return None
        root = self.user_dir.resolve()
        target = (self.user_dir / safe).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return None
        return target

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
        try:
            stat = skill_file.stat()
        except OSError:
            return None
        signature = (stat.st_mtime_ns, stat.st_size, bundled)
        cached = self._file_cache.get(skill_file)
        if cached is not None and cached[0] == signature:
            return cached[1]
        text = skill_file.read_text(encoding="utf-8")
        meta, _ = self._parse_frontmatter(text)
        skill = Skill(
            name=meta.get("name") or skill_file.parent.name,
            description=meta.get("description", ""),
            path=skill_file,
            bundled=bundled,
        )
        self._file_cache[skill_file] = (signature, skill)
        return skill

    def refresh(self) -> None:
        by_name: dict[str, Skill] = {}
        live_paths: set[Path] = set()

        if self.skills_dir.exists():
            for skill_file in sorted(self.skills_dir.glob("*/SKILL.md")):
                if skill_file.parent.name == "user":
                    continue
                live_paths.add(skill_file)
                skill = self._load_from_file(skill_file, bundled=True)
                if skill:
                    by_name[skill.name] = skill

        if self.user_dir.exists():
            for skill_file in sorted(self.user_dir.glob("*/SKILL.md")):
                live_paths.add(skill_file)
                skill = self._load_from_file(skill_file, bundled=False)
                if skill:
                    by_name[skill.name] = skill

        self._skills = list(by_name.values())
        self._by_name = by_name
        stale = set(self._file_cache) - live_paths
        for path in stale:
            self._file_cache.pop(path, None)

    def all(self) -> list[Skill]:
        return list(self._skills)

    def get(self, name: str) -> Skill | None:
        return self._by_name.get(name)

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
        safe = _validate_skill_name(name)
        if not safe:
            return "skill 名称非法（仅允许字母数字、_、-，且不可含路径）"
        existing = self.get(safe)
        if existing and existing.bundled:
            return f"内置 skill `{safe}` 不可覆盖，请换名称"

        skill_dir = self._user_skill_dir(safe)
        if skill_dir is None:
            return "skill 路径非法"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(
            self._format_skill_file(safe, description, content),
            encoding="utf-8",
        )
        self.refresh()
        action = "已更新" if existing else "已创建"
        return f"{action}用户 skill: {safe}"

    def patch(self, name: str, old_text: str, new_text: str) -> str:
        safe = _validate_skill_name(name) or (name or "").strip()
        skill = self.get(safe)
        if skill is None:
            return f"未找到 skill: {name}"
        if skill.bundled:
            return f"内置 skill `{safe}` 不可修改，请 save_skill 创建用户版本"
        # 仅允许改写已登记且仍在 user_dir 内的路径
        root = self.user_dir.resolve()
        try:
            skill.path.resolve().relative_to(root)
        except ValueError:
            return "skill 路径非法"

        text = skill.path.read_text(encoding="utf-8")
        if old_text not in text:
            return "未找到 old_text，请先用 load_skill 确认内容"
        skill.path.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        self.refresh()
        return f"已更新 skill: {safe}"

    def delete(self, name: str) -> str:
        safe = _validate_skill_name(name) or (name or "").strip()
        skill = self.get(safe)
        if skill is None:
            return f"未找到 skill: {name}"
        if skill.bundled:
            return f"内置 skill `{safe}` 不可删除"
        skill_dir = self._user_skill_dir(safe)
        if skill_dir is None or not skill_dir.exists():
            return "skill 路径非法"
        shutil.rmtree(skill_dir)
        self.refresh()
        return f"已删除用户 skill: {safe}"
