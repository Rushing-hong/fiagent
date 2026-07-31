"""A/B evaluation metrics for single-agent vs multi-agent runs."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from paths import DATA_DIR

EVAL_DIR = DATA_DIR / "evals"
EVAL_LOG = EVAL_DIR / "agent_runs.jsonl"


@dataclass
class RunMetrics:
    variant: str  # A=fast, B=research, C=committee
    query: str
    latency_ms: int
    tool_rounds: int = 0
    success: bool = True
    run_id: str = ""
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvalRecorder:
    """Append-only JSONL log for comparing agent variants."""

    def __init__(self, path: Path = EVAL_LOG) -> None:
        self.path = path
        EVAL_DIR.mkdir(parents=True, exist_ok=True)

    def record(self, metrics: RunMetrics) -> None:
        line = json.dumps(metrics.to_dict(), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def load_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def summarize_by_variant(self) -> dict[str, dict[str, float]]:
        rows = self.load_all()
        buckets: dict[str, list[dict]] = {}
        for r in rows:
            buckets.setdefault(r.get("variant", "?"), []).append(r)
        summary = {}
        for variant, items in buckets.items():
            n = len(items)
            if n == 0:
                continue
            summary[variant] = {
                "count": n,
                "avg_latency_ms": sum(i.get("latency_ms", 0) for i in items) / n,
                "success_rate": sum(1 for i in items if i.get("success")) / n,
            }
        return summary


class EvalTimer:
    def __init__(self) -> None:
        self._t0 = time.perf_counter()

    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self._t0) * 1000)
