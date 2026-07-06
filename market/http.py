"""Per-host throttled HTTP for public quote APIs."""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_JITTER_MAX_S = 0.4

_sessions: dict[str, dict[int, requests.Session]] = {}  # host_key → thread_id → Session
_session_lock = threading.Lock()
_last_request: dict[str, float] = {}
_throttle_lock = threading.Lock()


def _get_session(host_key: str) -> requests.Session:
    tid = threading.get_ident()
    with _session_lock:
        if host_key not in _sessions:
            _sessions[host_key] = {}
        per_host = _sessions[host_key]
        if tid not in per_host:
            session = requests.Session()
            session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
            per_host[tid] = session
        return per_host[tid]


def _wait(host_key: str, min_interval: float) -> None:
    if min_interval <= 0:
        return
    with _throttle_lock:
        now = time.monotonic()
        last = _last_request.get(host_key)
        if last is not None and now < last + min_interval:
            sleep_for = last + min_interval - now + random.uniform(0, _JITTER_MAX_S)
            time.sleep(sleep_for)
        _last_request[host_key] = time.monotonic()


def resolve_min_interval(env_var: str, default: float) -> float:
    import os

    raw = os.getenv(env_var)
    if raw is None:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def throttled_get(
    url: str,
    *,
    host_key: str,
    min_interval: float = 1.0,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> requests.Response:
    _wait(host_key, min_interval)
    session = _get_session(host_key)
    resp = session.get(url, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp


def throttled_get_json(
    url: str,
    *,
    host_key: str,
    min_interval: float = 1.0,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> Any:
    resp = throttled_get(
        url,
        host_key=host_key,
        min_interval=min_interval,
        params=params,
        headers=headers,
        timeout=timeout,
    )
    return resp.json()
