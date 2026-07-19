"""Per-host throttled HTTP for public quote APIs."""

from __future__ import annotations

import logging
import os
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

# 连接被对端掐断 / 超时：有限次退避重试（东财 RemoteDisconnected 常见）
_HTTP_RETRIES = max(1, int(os.environ.get("FIAGENT_HTTP_RETRIES", "3")))
_HTTP_RETRY_BACKOFF = float(os.environ.get("FIAGENT_HTTP_RETRY_BACKOFF", "0.8"))

_sessions: dict[str, dict[int, requests.Session]] = {}  # host_key → thread_id → Session
_session_lock = threading.Lock()
_last_request: dict[str, float] = {}
_throttle_lock = threading.Lock()

_RETRYABLE = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


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
    # Reserve this host's next slot atomically, then sleep outside the global
    # lock. Otherwise one throttled host blocks unrelated hosts as well.
    with _throttle_lock:
        now = time.monotonic()
        last = _last_request.get(host_key, now - min_interval)
        scheduled = max(now, last + min_interval)
        if scheduled > now:
            scheduled += random.uniform(0, _JITTER_MAX_S)
        _last_request[host_key] = scheduled
    sleep_for = scheduled - now
    if sleep_for > 0:
        time.sleep(sleep_for)


def resolve_min_interval(env_var: str, default: float) -> float:
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
    last_exc: BaseException | None = None
    for attempt in range(1, _HTTP_RETRIES + 1):
        _wait(host_key, min_interval)
        session = _get_session(host_key)
        try:
            resp = session.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except _RETRYABLE as exc:
            last_exc = exc
            if attempt >= _HTTP_RETRIES:
                break
            sleep_for = _HTTP_RETRY_BACKOFF * attempt + random.uniform(0, _JITTER_MAX_S)
            logger.warning(
                "HTTP retry %s/%s %s: %s (sleep %.1fs)",
                attempt,
                _HTTP_RETRIES,
                host_key,
                exc,
                sleep_for,
            )
            time.sleep(sleep_for)
        except requests.exceptions.HTTPError:
            raise
    assert last_exc is not None
    raise last_exc


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
