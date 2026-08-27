"""Global cap on concurrent LLM HTTP calls (research runs many sub-agents in parallel)."""

from __future__ import annotations

import threading
from contextlib import contextmanager

from core.config import env_int

_MAX = env_int("FIAGENT_LLM_MAX_PARALLEL", 2, minimum=1, maximum=64)
_gate = threading.Semaphore(_MAX)


@contextmanager
def llm_slot():
    """Hold one concurrent LLM request slot."""
    _gate.acquire()
    try:
        yield
    finally:
        _gate.release()
