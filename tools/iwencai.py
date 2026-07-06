"""问财自然语言选股（需 API Key）。"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from market.envelope import clamp_int, err
from market.http import resolve_min_interval, throttled_get_json
from tools.base import BaseTool

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.iwencai.com/customized/chart/get-robot-data"
_HOST_KEY = "iwencai"
_KEY_ENVS = ("FIAGENT_IWENCAI_KEY", "VIBE_TRADING_IWENCAI_KEY")
_MAX_COLUMNS = 60


def _iwencai_key() -> str | None:
    for env in _KEY_ENVS:
        val = os.getenv(env, "").strip()
        if val:
            return val
    return None


class IwencaiSearchTool(BaseTool):
    name = "iwencai_search"
    summary = "问财自然语言选股"
    description = (
        "用自然语言查询 A 股（同花顺问财）。需配置环境变量 FIAGENT_IWENCAI_KEY。"
        '示例: {"query": "市盈率低于15的银行股", "limit": 10}'
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 20},
        },
        "required": ["query"],
    }
    is_readonly = True
    repeatable = True

    @classmethod
    def check_available(cls) -> bool:
        return _iwencai_key() is not None

    def execute(self, args: dict, ctx) -> str:
        key = _iwencai_key()
        if not key:
            return err("未配置 FIAGENT_IWENCAI_KEY，问财工具不可用")
        query = str(args.get("query") or "").strip()
        if not query:
            return err("query 不能为空")
        limit = clamp_int(args.get("limit"), 20, 1, 100)
        interval = resolve_min_interval("FIAGENT_IWENCAI_MIN_INTERVAL", 1.5)
        try:
            payload = throttled_get_json(
                _SEARCH_URL,
                host_key=_HOST_KEY,
                min_interval=interval,
                params={
                    "question": query,
                    "perpage": str(limit),
                    "page": "1",
                    "source": "Ths_iwencai_Xuangu",
                },
                headers={"Authorization": f"Bearer {key}"},
            )
        except Exception as exc:
            return err(f"问财查询失败: {exc}")
        rows = _extract_rows(payload)
        results = [_project_row(r) for r in rows[:limit]]
        return json.dumps(
            {
                "ok": True,
                "market": "a_share",
                "source": "iwencai",
                "data": {"query": query, "count": len(results), "results": results},
            },
            ensure_ascii=False,
        )


def _extract_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    for answer in data.get("answer", []) if isinstance(data.get("answer"), list) else []:
        if not isinstance(answer, dict):
            continue
        for txt in answer.get("txt", []) if isinstance(answer.get("txt"), list) else []:
            rows = _rows_from_txt(txt)
            if rows:
                return rows
    return []


def _rows_from_txt(txt: Any) -> list[dict[str, Any]]:
    if not isinstance(txt, dict):
        return []
    content = txt.get("content")
    if not isinstance(content, dict):
        return []
    for comp in content.get("components", []) if isinstance(content.get("components"), list) else []:
        if not isinstance(comp, dict):
            continue
        data = comp.get("data")
        if isinstance(data, dict) and isinstance(data.get("datas"), list):
            return [r for r in data["datas"] if isinstance(r, dict)]
    return []


def _project_row(row: dict[str, Any]) -> dict[str, Any]:
    items = list(row.items())[:_MAX_COLUMNS]
    return dict(items)
