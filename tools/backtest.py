"""Backtesting tool for A-share quantitative strategies.

Integrates with existing get_market_data → run backtest → analyze results pipeline.
Supports built-in strategies (MA cross, RSI, momentum, buy-hold) and custom signals.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from market.backtest_engine import (
    BacktestConfig,
    BacktestEngine,
    _BUILTIN_STRATEGIES,
)
from market.market_data import fetch_one
from tools.base import BaseTool


def _parse_data_json(raw: str) -> dict[str, pd.DataFrame]:
    """Parse get_market_data JSON output into DataFrames."""
    payload = json.loads(raw)
    if not payload.get("ok"):
        raise ValueError(payload.get("error", "unknown data error"))
    wrapper = payload.get("data", {})
    result: dict[str, pd.DataFrame] = {}
    for code, val in wrapper.items():
        if isinstance(val, dict) and "data" in val:
            rows = val["data"]
        elif isinstance(val, list):
            rows = val
        else:
            continue
        if not rows:
            continue
        df = pd.DataFrame(rows)
        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.set_index("trade_date").sort_index()
        result[code] = df
    return result


class RunBacktestTool(BaseTool):
    name = "run_backtest"
    summary = "A股策略回测（支持内置策略+自定义信号）"
    description = (
        "对 A 股历史数据执行策略回测，输出绩效指标、交易明细和净值曲线。\n\n"
        "使用流程:\n"
        "1. 先用 get_market_data 获取 OHLCV 数据\n"
        "2. 选择内置策略或传入自定义信号文件路径\n"
        "3. 调用 run_backtest 执行回测\n"
        "4. 解读返回的 metrics (年化收益/夏普/最大回撤/胜率等)\n\n"
        "内置策略:\n"
        f"{chr(10).join(f'  - {k}: {v.__doc__.split(chr(10))[0] if v.__doc__ else k}' for k, v in _BUILTIN_STRATEGIES.items())}\n\n"
        "市场规则自动执行: T+1 交割、涨跌停限制、印花税(卖0.05%)、佣金(0.03%)、过户费(0.001%)、"
        "最小交易单位100股。A股不支持做空，空头信号自动忽略。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "codes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "股票代码列表，如 ['600519.SH']",
            },
            "start_date": {"type": "string", "description": "开始日期 YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
            "strategy": {
                "type": "string",
                "enum": list(_BUILTIN_STRATEGIES.keys()) + ["custom"],
                "default": "ma_cross",
                "description": "策略名称: ma_cross(双均线) / rsi(RSI均值回归) / momentum(动量) / buy_hold(买入持有) / custom(自定义信号)",
            },
            "strategy_params": {
                "type": "object",
                "description": "策略参数。ma_cross: {fast, slow}; rsi: {period, oversold, overbought}; momentum: {window}",
            },
            "signal_file": {
                "type": "string",
                "description": "自定义信号文件路径 (strategy=custom 时必填)。CSV格式: index=date, columns=股票代码, values=-1~1",
            },
            "initial_cash": {"type": "number", "default": 1000000},
            "commission": {"type": "number", "default": 0.0003},
            "stamp_duty": {"type": "number", "default": 0.0005},
            "after_hours": {"type": "boolean", "default": False,
                "description": "启用盘后固定价格交易 (2026.7.6 新规: 15:05-15:30, 收盘价成交)"},
        },
        "required": ["codes", "start_date", "end_date"],
    }
    is_readonly = True
    repeatable = True

    def execute(self, args: dict[str, Any], ctx: Any) -> str:
        codes = args.get("codes")
        if not isinstance(codes, list) or not codes:
            return _err("codes 必须为非空数组")

        start_date = str(args.get("start_date", ""))
        end_date = str(args.get("end_date", ""))
        if not start_date or not end_date:
            return _err("start_date 和 end_date 必填")

        strategy = str(args.get("strategy", "ma_cross"))
        is_custom = strategy == "custom"

        # Fetch data
        data: dict[str, pd.DataFrame] = {}
        for code in codes:
            try:
                rows, source = fetch_one(code, start_date, end_date)
                if not rows:
                    return _err(f"未获取到 {code} 的行情数据")
                df = pd.DataFrame(rows)
                df["trade_date"] = pd.to_datetime(df["trade_date"])
                df = df.set_index("trade_date").sort_index()
                data[code] = df
            except Exception as e:
                return _err(f"获取 {code} 数据失败: {e}")

        if not data:
            return _err("未获取到任何行情数据")

        # Load custom signal if needed
        signal_df = None
        if is_custom:
            signal_file = str(args.get("signal_file", ""))
            if not signal_file:
                return _err("strategy=custom 时必须提供 signal_file")
            try:
                from tools._fs import resolve_path
                path = resolve_path(ctx, signal_file)
                signal_df = pd.read_csv(path, index_col=0, parse_dates=True)
            except Exception as e:
                return _err(f"读取信号文件失败: {e}")

        # Configure and run
        cfg = BacktestConfig(
            initial_cash=float(args.get("initial_cash", 1_000_000)),
            commission=float(args.get("commission", 0.0003)),
            stamp_duty=float(args.get("stamp_duty", 0.0005)),
            after_hours=bool(args.get("after_hours", False)),
        )

        engine = BacktestEngine(cfg)
        try:
            result = engine.run(
                data=data,
                signal=signal_df if is_custom else None,
                strategy=strategy if not is_custom else "",
                strategy_params=args.get("strategy_params") if not is_custom else None,
            )
        except Exception as e:
            return _err(f"回测执行失败: {e}")

        if not result.get("ok"):
            return _err(result.get("error", "回测失败"))

        return json.dumps(result, ensure_ascii=False, default=str)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)
