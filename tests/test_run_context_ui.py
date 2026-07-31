"""Research run context UI suppression."""

from research.run_context import (
    ResearchRunContext,
    is_research_run_active,
    set_research_run_active,
    set_run_context,
    suppress_main_chat_ui,
)


class _FakeStore:
    run_id = "r1"


def test_suppress_subagent_not_orchestrator():
    set_research_run_active(False)
    set_run_context(None)
    assert suppress_main_chat_ui() is False

    set_run_context(ResearchRunContext("r1", _FakeStore(), "market_regime"))
    assert suppress_main_chat_ui() is True

    set_run_context(ResearchRunContext("r1", _FakeStore(), "orchestrator"))
    assert suppress_main_chat_ui() is False
    set_run_context(None)


def test_suppress_during_active_research_without_agent_context():
    set_run_context(None)
    set_research_run_active(True)
    try:
        assert is_research_run_active()
        assert suppress_main_chat_ui() is True
    finally:
        set_research_run_active(False)
