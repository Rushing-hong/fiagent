"""Critical security + calendar fallback regressions (roadmap S1/S2/S3/#6)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from skills.registry import SkillRegistry, _validate_skill_name
from tools._fs import PathError, resolve_path
from tools.run_python import _sanitized_subprocess_env


def test_skill_name_rejects_traversal():
    assert _validate_skill_name("ok-skill_1") == "ok-skill_1"
    assert _validate_skill_name("../etc") is None
    assert _validate_skill_name("a/b") is None
    assert _validate_skill_name("..") is None
    assert _validate_skill_name("") is None


def test_skill_save_blocks_path_escape(tmp_path: Path):
    skills = tmp_path / "skills"
    skills.mkdir()
    reg = SkillRegistry(skills)
    msg = reg.save("../escape", "x", "body")
    assert "非法" in msg
    assert not (tmp_path / "escape").exists()
    assert list((skills / "user").glob("*")) == [] or not any(
        p.name == ".." for p in (skills / "user").iterdir()
    )


def test_resolve_path_blocks_env(tmp_path: Path):
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    (tmp_path / "ok.txt").write_text("hi", encoding="utf-8")
    ctx = SimpleNamespace(root=tmp_path)
    assert resolve_path(ctx, "ok.txt").name == "ok.txt"
    with pytest.raises(PathError, match="敏感"):
        resolve_path(ctx, ".env")
    with pytest.raises(PathError, match="敏感"):
        resolve_path(ctx, "./.env")


def test_sanitized_env_strips_secrets(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret")
    monkeypatch.setenv("TUSHARE_TOKEN", "tok")
    monkeypatch.setenv("FIAGENT_IWENCAI_KEY", "k")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("NORMAL_FLAG", "1")
    env = _sanitized_subprocess_env()
    assert "DEEPSEEK_API_KEY" not in env
    assert "TUSHARE_TOKEN" not in env
    assert "FIAGENT_IWENCAI_KEY" not in env
    assert env.get("PATH") == "/usr/bin"
    assert env.get("NORMAL_FLAG") == "1"
    assert env.get("PYTHONUNBUFFERED") == "1"


def test_trading_days_no_weekday_fallback(monkeypatch):
    import market.trade_calendar as tc

    monkeypatch.setattr(tc, "_cached_set", lambda: frozenset())
    monkeypatch.setattr(tc, "_cached_days", lambda: ())
    assert tc.trading_days("2024-01-01", "2024-01-10") == []
    assert tc.is_trading_day("2024-01-02") is False
