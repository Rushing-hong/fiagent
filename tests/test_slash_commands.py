"""Slash command completion matching."""

from __future__ import annotations

from core.commands import COMMAND_ALIASES, SESSION_COMMANDS, list_slash_matches, match_slash_command


def _cmds(query: str) -> list[str]:
    return [c for c, _ in list_slash_matches(query)]


def test_menu_has_no_alias_duplicates():
    cmds = _cmds("/")
    for alias in COMMAND_ALIASES:
        assert alias not in cmds
    assert set(cmds) == set(SESSION_COMMANDS)


def test_r_does_not_match_effort_or_verbose():
    cmds = _cmds("/r")
    assert "/effort" not in cmds
    assert "/verbose" not in cmds
    assert "/reload" in cmds
    assert "/reload_comp" in cmds
    assert "/resume" in cmds
    assert "/reexec" not in cmds
    assert "/re" not in cmds
    assert "/rc" not in cmds


def test_re_shows_reload_family_not_aliases():
    cmds = _cmds("/re")
    assert cmds == ["/reload", "/reload_comp", "/resume"]
    assert "/re" not in cmds
    assert "/reexec" not in cmds


def test_reload_shows_reload_comp():
    cmds = _cmds("/reload")
    assert cmds[0] == "/reload"
    assert "/reload_comp" in cmds


def test_comp_and_rc_find_reload_comp():
    assert "/reload_comp" in _cmds("/comp")
    cmds_rc = _cmds("/rc")
    assert "/reload_comp" in cmds_rc
    assert "/rc" not in cmds_rc
    assert match_slash_command("/reload_comp", "/comp")


def test_bare_slash_includes_reload_comp():
    cmds = _cmds("/")
    assert "/reload_comp" in cmds
    assert "/reload" in cmds
    assert "/quit" in cmds
    assert "/exit" not in cmds
    assert "/q" not in cmds
    # 首屏常用序：reload_comp 紧跟 reload，不挤到字母序末尾
    assert cmds.index("/reload") < cmds.index("/reload_comp")
    assert cmds.index("/reload_comp") < 10
