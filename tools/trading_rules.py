"""A-share trading rules lookup — deterministic compliance tool."""

from __future__ import annotations

import json

from policy.compliance_engine import ComplianceEngine
from tools.base import BaseTool


class GetTradingRulesTool(BaseTool):
    name = "get_trading_rules"
    summary = "查询 A 股交易制度与合规规则"
    description = (
        "返回指定证券在 as_of_date 适用的 A 股交易规则（涨跌停、T+1、税费、最小交易单位等）。"
        "结果为确定性规则版本，供研究与合规门使用；非投资建议。"
        "必须提供经行情源核验的 security_status；未核验状态会触发硬否决。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "security": {
                "type": "string",
                "description": "证券代码，如 600519.SH 或 600519",
            },
            "as_of_date": {
                "type": "string",
                "description": "规则生效参考日 YYYY-MM-DD，默认今天",
            },
            "action": {
                "type": "string",
                "enum": ["buy", "sell", "hold"],
                "default": "buy",
            },
            "security_status": {
                "type": "string",
                "enum": ["NORMAL", "ST", "SUSPENDED", "DELISTED"],
                "description": "可选：若已知 ST/停牌状态可传入以提高准确性",
            },
        },
        "required": ["security"],
    }
    is_readonly = True
    repeatable = True

    def execute(self, args: dict, ctx) -> str:
        engine = ComplianceEngine()
        try:
            rules = engine.get_trading_rules(
                security=str(args.get("security", "")),
                as_of_date=args.get("as_of_date") or None,
                action=str(args.get("action") or "buy"),
                security_status=args.get("security_status") or None,
            )
        except ValueError as e:
            return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)
        return json.dumps({"status": "ok", **rules.to_dict()}, ensure_ascii=False)
