"""Global cap on concurrent LLM HTTP calls (research runs many sub-agents in parallel)."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager

_MAX = max(1, int(os.getenv("FIAGENT_LLM_MAX_PARALLEL", "2")))
_gate = threading.Semaphore(_MAX)


@contextmanager
def llm_slot():
    """Hold one concurrent LLM request slot."""
    _gate.acquire()
    try:
        yield
    finally:
        _gate.release()
