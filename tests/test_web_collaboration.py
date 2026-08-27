"""Web collaboration UX and API contract regressions."""

from __future__ import annotations

from core.agents.router import AgentMode
from paths import PROJECT_ROOT
from ui.web.server import _collaboration_mode, _public_messages


def test_web_collaboration_selection_is_explicit_and_one_shot():
    assert _collaboration_mode("") is None
    assert _collaboration_mode("research") == AgentMode.RESEARCH
    assert _collaboration_mode("committee") == AgentMode.COMMITTEE
    assert _collaboration_mode("trade_review") == AgentMode.TRADE_REVIEW
    assert _collaboration_mode("duo-agent") is None


def test_public_messages_expose_safe_collaboration_card_metadata():
    messages = [
        {"role": "user", "content": "分析茅台"},
        {
            "role": "assistant",
            "content": "最终结论",
            "_fiagent": {
                "collaboration": {
                    "run_id": "abc123",
                    "mode": "research",
                    "query": "分析茅台",
                },
            },
        },
    ]

    assert _public_messages(messages) == [
        {"role": "user", "content": "分析茅台"},
        {
            "role": "assistant",
            "content": "最终结论",
            "collaboration": {
                "run_id": "abc123",
                "mode": "research",
                "query": "分析茅台",
            },
        },
    ]


def test_web_uses_one_shot_collaboration_ui_not_sticky_agent_modes():
    index = (PROJECT_ROOT / "ui" / "web" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    desk = (PROJECT_ROOT / "ui" / "web" / "static" / "desk.js").read_text(
        encoding="utf-8"
    )
    collaboration = (
        PROJECT_ROOT / "ui" / "web" / "static" / "desk-collaboration.js"
    ).read_text(encoding="utf-8")

    assert 'id="collaboration-trigger"' in index
    assert 'id="collaboration-view"' in index
    assert "data-agent-mode" not in index
    assert 'id="agent-panel"' not in index
    assert 'id="rail-agent-team"' not in index
    assert "pendingCollaboration" in desk
    assert 'type: "collaboration"' not in collaboration  # server owns SSE type
    assert not (PROJECT_ROOT / "ui" / "web" / "static" / "desk-team.js").exists()
