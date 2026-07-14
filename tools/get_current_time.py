"""当前本地时间（时区可配）。"""

from __future__ import annotations

from market.envelope import ok
from tools.base import BaseTool


class GetCurrentTimeTool(BaseTool):
    name = "get_current_time"
    summary = "查询当前本地日期时间"
    description = (
        "返回 Agent 主机当前本地时间（默认 Asia/Shanghai，可用 FIAGENT_TZ 覆盖）。"
        "用户问「现在几点/今天几号」或长会话后时间不确定时优先调用本工具，"
        "不要依赖对话开头或训练记忆中的日期。"
    )
    parameters = {"type": "object", "properties": {}}
    is_readonly = True
    repeatable = True

    def execute(self, args: dict, ctx) -> str:
        import os

        now = ctx._now()
        weekdays = "一二三四五六日"
        tz_name = os.getenv("FIAGENT_TZ", "Asia/Shanghai")
        offset = now.strftime("%z")
        return ok(
            {
                "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S"),
                "weekday": f"星期{weekdays[now.weekday()]}",
                "timezone": tz_name,
                "utc_offset": f"UTC{offset[:3]}:{offset[3:]}" if offset else None,
                "iso": now.isoformat(timespec="seconds"),
                "unix": int(now.timestamp()),
            },
            market="local",
            source="system_clock",
            tool="get_current_time",
            quality="normal",
        )
