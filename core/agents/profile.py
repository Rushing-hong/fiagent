"""Agent profile: role definition, tool/skill allowlists, prompt."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from paths import PROJECT_ROOT

PROFILES_DIR = PROJECT_ROOT / "agents" / "profiles"


@dataclass(frozen=True)
class AgentProfile:
    name: str
    display_name: str
    system_prompt: str
    allowed_tools: frozenset[str] | None = None
    allowed_skills: frozenset[str] | None = None
    max_tool_rounds: int = 20
    memory_namespace: str = ""

    def tool_allowed(self, name: str) -> bool:
        if self.allowed_tools is None:
            return True
        return name in self.allowed_tools

    def skill_allowed(self, name: str) -> bool:
        if self.allowed_skills is None:
            return True
        return name in self.allowed_skills


def _read_prompt(root: Path, rel: str) -> str:
    path = root / rel
    if not path.exists():
        raise FileNotFoundError(f"Agent prompt not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def load_profile(name: str, root: Path = PROJECT_ROOT) -> AgentProfile:
    path = root / "agents" / "profiles" / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Agent profile not found: {path}")
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    prompt_file = raw.get("system_prompt_file", "")
    prompt = raw.get("system_prompt", "")
    if prompt_file:
        prompt = _read_prompt(root, prompt_file)
    elif not prompt:
        raise ValueError(f"Profile {name} needs system_prompt or system_prompt_file")

    tools = raw.get("allowed_tools")
    skills = raw.get("allowed_skills")
    return AgentProfile(
        name=raw.get("name", name),
        display_name=raw.get("display_name", name),
        system_prompt=prompt,
        allowed_tools=frozenset(tools) if tools is not None else None,
        allowed_skills=frozenset(skills) if skills is not None else None,
        max_tool_rounds=int(raw.get("max_tool_rounds", 20)),
        memory_namespace=str(raw.get("memory_namespace", name)),
    )


def list_profiles() -> list[str]:
    if not PROFILES_DIR.is_dir():
        return []
    return sorted(p.stem for p in PROFILES_DIR.glob("*.json"))
