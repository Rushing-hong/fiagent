"""Web 工具：read_url + web_search。"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
import time
from urllib.parse import urlsplit

import requests

from tools.base import BaseTool

logger = logging.getLogger(__name__)

_JINA_PREFIX = "https://r.jina.ai/"
_TIMEOUT = 30
_MAX_LENGTH = 8000
_DEFAULT_BACKENDS = "duckduckgo, google, bing, brave, mojeek, yahoo"
_MAX_ATTEMPTS = 3


def _ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or (hasattr(ip, "is_unspecified") and ip.is_unspecified)
        or not ip.is_global
    )


def _url_allowed(url: str) -> tuple[bool, str]:
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return False, "target URL is not allowed"
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return False, "target URL is not allowed"
    if parsed.username or parsed.password:
        return False, "target URL is not allowed"
    host = parsed.hostname.rstrip(".").lower()
    if host in ("localhost",) or host.endswith(".localhost") or host.endswith(".local"):
        return False, "target URL is not allowed"
    # 字面 IP
    try:
        if _ip_blocked(ipaddress.ip_address(host.split("%", 1)[0])):
            return False, "target URL is not allowed"
        return True, ""
    except ValueError:
        pass
    # 主机名：解析全部 A/AAAA，拒绝落在非全局地址（防 DNS rebinding / nip.io）
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False, "target URL is not allowed"
    if not infos:
        return False, "target URL is not allowed"
    for info in infos:
        addr = info[4][0]
        try:
            if _ip_blocked(ipaddress.ip_address(addr.split("%", 1)[0])):
                return False, "target URL is not allowed"
        except ValueError:
            return False, "target URL is not allowed"
    return True, ""


def _read_url_direct(url: str) -> str:
    """Fallback: direct HTTP fetch without Jina."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; fiagent/1.0)"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        text = resp.text
        # Try to extract plain text from HTML
        try:
            from html.parser import HTMLParser
            class Stripper(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.text = []
                def handle_data(self, data):
                    self.text.append(data.strip())
            s = Stripper()
            s.feed(text)
            text = "\n".join(t for t in s.text if t)[:_MAX_LENGTH]
        except Exception:
            text = text[:_MAX_LENGTH]
        if len(text) > _MAX_LENGTH:
            text = text[:_MAX_LENGTH] + "\n\n... (truncated)"
        return json.dumps(
            {"status": "ok", "title": "", "url": url, "content": text, "source": "direct"},
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)


def read_url(url: str, no_cache: bool = False) -> str:
    target = url.strip()
    allowed, error = _url_allowed(target)
    if not allowed:
        return json.dumps({"status": "error", "error": error}, ensure_ascii=False)
    try:
        headers = {"Accept": "text/markdown"}
        if no_cache:
            headers["x-no-cache"] = "true"
        resp = requests.get(f"{_JINA_PREFIX}{target}", headers=headers, timeout=_TIMEOUT)
        if resp.status_code != 200:
            # Fallback: try direct fetch
            return _read_url_direct(target)
        text = resp.text
        title = ""
        for line in text.split("\n"):
            if line.startswith("Title:"):
                title = line[6:].strip()
                break
        if len(text) > _MAX_LENGTH:
            text = text[:_MAX_LENGTH] + f"\n\n... (truncated, total {len(resp.text)} chars)"
        return json.dumps(
            {"status": "ok", "title": title, "url": target, "content": text},
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)


class ReadUrlTool(BaseTool):
    name = "read_url"
    summary = "抓取网页为 Markdown"
    description = "通过 Jina Reader 将 URL 转为 Markdown，适合读公告、新闻、文档。"
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "no_cache": {"type": "boolean", "default": False},
        },
        "required": ["url"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        return read_url(args.get("url", ""), no_cache=bool(args.get("no_cache", False)))


class WebSearchTool(BaseTool):
    name = "web_search"
    summary = "多引擎网页搜索"
    description = "免费搜索引擎聚合（ddgs），用于发现 URL 后再 read_url 精读。"

    @classmethod
    def check_available(cls) -> bool:
        try:
            import ddgs  # noqa: F401
            return True
        except ImportError:
            try:
                import duckduckgo_search  # noqa: F401
                return True
            except ImportError:
                return False

    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    }
    is_readonly = True

    def execute(self, args: dict, ctx) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return json.dumps({"status": "error", "error": "query 不能为空"}, ensure_ascii=False)
        max_results = min(int(args.get("max_results", 5)), 10)
        backends = os.getenv("FIAGENT_SEARCH_BACKENDS", _DEFAULT_BACKENDS).strip() or "auto"
        try:
            from ddgs import DDGS
            supports_backend = True
        except ImportError:
            try:
                from duckduckgo_search import DDGS
            except ImportError:
                return json.dumps(
                    {"status": "error", "error": "请安装: pip install ddgs"},
                    ensure_ascii=False,
                )
            supports_backend = False

        last_error = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                with DDGS() as client:
                    if supports_backend:
                        raw = list(client.text(query, max_results=max_results, backend=backends))
                    else:
                        raw = list(client.text(query, max_results=max_results))
                results = [
                    {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
                    for r in raw
                ]
                return json.dumps(
                    {"status": "ok", "query": query, "results": results},
                    ensure_ascii=False,
                )
            except Exception as exc:
                last_error = exc
                if attempt < _MAX_ATTEMPTS:
                    time.sleep(0.8 * attempt)
        return json.dumps({"status": "error", "error": str(last_error)}, ensure_ascii=False)
