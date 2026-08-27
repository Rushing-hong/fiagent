"""Simulate order execution against OHLCV bar — deterministic tool."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from policy.execution_engine import (
    ExecutionConfig,
    ExecutionEngine,
    OrderIntent,
    PortfolioSnapshot,
    bar_from_ohlcv_row,
)
from tools.base import BaseTool


class SimulateExecutionTool(BaseTool):
    name = "simulate_execution"
    summary = "模拟 A 股订单成交（涨跌停/T+1/部分成交/冲击成本）"
    description = (
        "对单笔订单做确定性成交模拟，禁止假设收盘价全额成交。"
        "可传入 bar 字段，或 symbol+trade_date 自动拉取最近一根日 K。"
        "返回 filled/partial/rejected 及原因。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "如 600519.SH"},
            "side": {"type": "string", "enum": ["buy", "sell"], "default": "buy"},
            "target_weight": {
                "type": "number",
                "description": "目标仓位权重 0-1（与 quantity 二选一）",
            },
            "quantity": {"type": "integer", "description": "股数（整手）"},
            "exec_style": {
                "type": "string",
                "enum": ["next_open", "close", "vwap"],
                "default": "next_open",
            },
            "trade_date": {
                "type": "string",
                "description": "成交日 YYYY-MM-DD；与 bar 二选一",
            },
            "bar": {
                "type": "object",
                "description": "OHLCV 字典：open/high/low/close/volume/amount/prev_close",
            },
            "nav": {"type": "number", "default": 1_000_000},
            "cash": {"type": "number"},
            "positions": {
                "type": "object",
                "description": "持仓 {symbol: qty}",
            },
            "buy_dates": {
                "type": "object",
                "description": "买入日 {symbol: YYYY-MM-DD}，用于 T+1 卖出检查",
            },
            "participation_rate": {
                "type": "number",
                "default": 0.10,
                "description": "最大参与率（相对当日成交量）",
            },
        },
        "required": ["symbol", "side"],
    }
    is_readonly = True
    repeatable = True

    def _fetch_bar(self, symbol: str, trade_date: str) -> dict | None:
        try:
            from market.market_data import fetch_market_data

            end = trade_date
            start = (
                datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=30)
            ).strftime("%Y-%m-%d")
            data = fetch_market_data(
                codes=[symbol.upper()],
                start_date=start,
                end_date=end,
                source="auto",
                max_rows=40,
            )
            entry = data.get(symbol.upper()) or {}
            rows = entry.get("data") or []
            if not rows:
                return None
            target = trade_date
            row = next(
                (
                    r for r in rows
                    if str(r.get("trade_date") or r.get("date") or "")[:10] == target
                ),
                None,
            )
            if row is None:
                return None
            prev_idx = rows.index(row) - 1
            prev_close = float(rows[prev_idx]["close"]) if prev_idx >= 0 else None
            return bar_from_ohlcv_row(row, prev_close=prev_close)
        except Exception:
            return None

    def execute(self, args: dict, ctx) -> str:
        symbol = str(args.get("symbol", "")).upper()
        side = str(args.get("side") or "buy").lower()
        bar_arg = args.get("bar")
        trade_date = str(args.get("trade_date") or "")

        if side not in {"buy", "sell"}:
            return json.dumps({"status": "error", "error": "side 只能为 buy 或 sell"}, ensure_ascii=False)
        if isinstance(bar_arg, dict):
            required = ("open", "high", "low", "close", "volume", "prev_close")
            missing = [key for key in required if bar_arg.get(key) is None]
            if missing:
                return json.dumps({
                    "status": "error",
                    "error": f"bar 缺少必填字段: {', '.join(missing)}",
                }, ensure_ascii=False)
            bar = bar_from_ohlcv_row(bar_arg)
        elif trade_date:
            bar = self._fetch_bar(symbol, trade_date)
            if bar is None:
                return json.dumps({
                    "status": "error",
                    "error": f"无法获取 {symbol} 在 {trade_date} 的行情 bar",
                }, ensure_ascii=False)
        else:
            return json.dumps({
                "status": "error",
                "error": "需提供 bar 或 trade_date",
            }, ensure_ascii=False)

        try:
            participation_rate = float(args.get("participation_rate") or 0.10)
        except (TypeError, ValueError):
            return json.dumps({"status": "error", "error": "participation_rate 必须为数字"}, ensure_ascii=False)
        if not 0 < participation_rate <= 1:
            return json.dumps({"status": "error", "error": "participation_rate 必须在 (0, 1]"}, ensure_ascii=False)
        cfg = ExecutionConfig(participation_rate=participation_rate)
        pf = PortfolioSnapshot(
            nav=float(args.get("nav") or 1_000_000),
            cash=float(args.get("cash") if args.get("cash") is not None else args.get("nav") or 1_000_000),
            positions={k.upper(): int(v) for k, v in (args.get("positions") or {}).items()},
            buy_dates={k.upper(): str(v) for k, v in (args.get("buy_dates") or {}).items()},
            trade_date=trade_date,
        )
        intent = OrderIntent(
            symbol=symbol,
            side=side,
            target_weight=args.get("target_weight"),
            quantity=args.get("quantity"),
            exec_style=str(args.get("exec_style") or "next_open"),
        )
        report = ExecutionEngine(cfg).simulate(intent, bar, portfolio=pf)
        payload = report.to_dict()
        payload["fill_status"] = payload.pop("status")
        payload["status"] = "ok"
        return json.dumps(payload, ensure_ascii=False)
