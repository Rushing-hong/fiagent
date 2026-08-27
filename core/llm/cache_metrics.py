"""Provider-neutral prompt-cache usage metrics."""

from __future__ import annotations

import threading
from typing import Any


_lock = threading.Lock()
_totals: dict[str, int] = {
    "requests": 0,
    "prompt_tokens": 0,
    "hit_tokens": 0,
    "miss_tokens": 0,
    "write_tokens": 0,
}
_by_provider: dict[str, dict[str, int]] = {}
_last: dict[str, Any] | None = None


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            data = dump()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    data = getattr(value, "__dict__", None)
    return data if isinstance(data, dict) else {}


def _integer(*values: Any) -> int:
    for value in values:
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


def normalize_cache_usage(usage: Any) -> dict[str, int] | None:
    """Normalize OpenAI, DeepSeek, Claude-gateway, and Gemini usage shapes."""
    data = _as_dict(usage)
    if not data:
        return None
    prompt_details = _as_dict(
        data.get("prompt_tokens_details") or data.get("input_tokens_details")
    )
    anthropic_read = _integer(data.get("cache_read_input_tokens"))
    anthropic_write = _integer(data.get("cache_creation_input_tokens"))
    uncached_input = _integer(data.get("input_tokens"))
    prompt = _integer(data.get("prompt_tokens"), data.get("input_tokens"))
    hit = _integer(
        data.get("prompt_cache_hit_tokens"),
        prompt_details.get("cached_tokens"),
        anthropic_read,
        data.get("total_cached_tokens"),
    )
    explicit_miss = data.get("prompt_cache_miss_tokens")
    write = _integer(
        data.get("cache_write_tokens"),
        prompt_details.get("cache_write_tokens"),
        anthropic_write,
    )
    if anthropic_read or anthropic_write:
        # Claude reports post-breakpoint input separately from cache reads and
        # writes; reconstruct the total described in its official docs.
        miss = uncached_input + anthropic_write
        prompt = anthropic_read + miss
    else:
        miss = _integer(explicit_miss) if explicit_miss is not None else max(0, prompt - hit)
    if prompt == 0:
        prompt = hit + miss
    if prompt == 0 and hit == 0 and miss == 0 and write == 0:
        return None
    return {
        "prompt_tokens": prompt,
        "hit_tokens": hit,
        "miss_tokens": miss,
        "write_tokens": write,
    }


def record_cache_usage(provider: str, model: str, usage: Any) -> dict[str, Any] | None:
    global _last
    normalized = normalize_cache_usage(usage)
    if normalized is None:
        return None
    row: dict[str, Any] = {"provider": provider, "model": model, **normalized}
    denominator = normalized["hit_tokens"] + normalized["miss_tokens"]
    row["hit_rate"] = normalized["hit_tokens"] / denominator if denominator else 0.0
    with _lock:
        _totals["requests"] += 1
        for key in ("prompt_tokens", "hit_tokens", "miss_tokens", "write_tokens"):
            _totals[key] += normalized[key]
        provider_totals = _by_provider.setdefault(
            provider,
            {
                "requests": 0,
                "prompt_tokens": 0,
                "hit_tokens": 0,
                "miss_tokens": 0,
                "write_tokens": 0,
            },
        )
        provider_totals["requests"] += 1
        for key in ("prompt_tokens", "hit_tokens", "miss_tokens", "write_tokens"):
            provider_totals[key] += normalized[key]
        _last = dict(row)
    return row


def get_cache_metrics() -> dict[str, Any]:
    with _lock:
        totals = dict(_totals)
        by_provider = {name: dict(values) for name, values in _by_provider.items()}
        last = dict(_last) if _last else None
    denominator = totals["hit_tokens"] + totals["miss_tokens"]
    totals["hit_rate"] = totals["hit_tokens"] / denominator if denominator else 0.0
    for values in by_provider.values():
        denom = values["hit_tokens"] + values["miss_tokens"]
        values["hit_rate"] = values["hit_tokens"] / denom if denom else 0.0
    return {"totals": totals, "by_provider": by_provider, "last": last}


def reset_cache_metrics() -> None:
    global _last
    with _lock:
        for key in _totals:
            _totals[key] = 0
        _by_provider.clear()
        _last = None


def format_cache_metrics() -> str:
    snapshot = get_cache_metrics()
    totals = snapshot["totals"]
    if not totals["requests"]:
        return "暂无 Prompt Cache 用量数据；完成一次模型请求后再查看。"
    lines = [
        "Prompt Cache（当前进程）",
        f"总请求: {totals['requests']}  命中率: {totals['hit_rate']:.1%}",
        f"命中/未命中/写入: {totals['hit_tokens']:,} / {totals['miss_tokens']:,} / {totals['write_tokens']:,} tokens",
    ]
    for provider, values in sorted(snapshot["by_provider"].items()):
        lines.append(
            f"- {provider}: {values['requests']} 次，{values['hit_rate']:.1%}，"
            f"hit {values['hit_tokens']:,} / miss {values['miss_tokens']:,}"
        )
    last = snapshot["last"]
    if last:
        lines.append(
            f"最近: {last['provider']}/{last['model']} · {last['hit_rate']:.1%}"
        )
    return "\n".join(lines)
