"""Task DAG executor for multi-agent workflows."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskNode:
    task_id: str
    agent_name: str
    run: Callable[[], Any]
    depends_on: list[str] = field(default_factory=list)


class TaskGraph:
    """Minimal DAG: topological layers, parallel within layer."""

    def __init__(self) -> None:
        self._nodes: dict[str, TaskNode] = {}

    def add(self, node: TaskNode) -> None:
        if node.task_id in self._nodes:
            raise ValueError(f"duplicate task_id: {node.task_id}")
        self._nodes[node.task_id] = node

    def _layers(self) -> list[list[TaskNode]]:
        remaining = dict(self._nodes)
        done: set[str] = set()
        layers: list[list[TaskNode]] = []
        while remaining:
            ready = [
                n for tid, n in remaining.items()
                if all(d in done for d in n.depends_on)
            ]
            if not ready:
                raise ValueError("task graph has a cycle or missing dependency")
            layers.append(ready)
            for n in ready:
                done.add(n.task_id)
                del remaining[n.task_id]
        return layers

    def run(self, *, max_workers: int = 2) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for layer in self._layers():
            if len(layer) == 1:
                n = layer[0]
                results[n.task_id] = n.run()
                continue
            workers = min(max_workers, len(layer))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(n.run): n for n in layer}
                for fut in as_completed(futures):
                    n = futures[fut]
                    results[n.task_id] = fut.result()
        return results
