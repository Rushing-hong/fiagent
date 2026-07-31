"""Multi-agent orchestration layer (Atrading v2 foundation)."""

from core.agents.profile import AgentProfile, load_profile, list_profiles
from core.agents.router import AgentMode, route_query
from core.agents.runner import AgentResult, AgentRunner
from core.agents.orchestrator import ResearchOrchestrator

__all__ = [
    "AgentProfile",
    "AgentResult",
    "AgentRunner",
    "AgentMode",
    "ResearchOrchestrator",
    "list_profiles",
    "load_profile",
    "route_query",
]
