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
        "市场规则自动执行: T+1 交割、涨跌停锁仓拒单(默认)、信号延迟次日开盘成交(默认 signal_lag=1)、"
        "√冲击成本、印花税(卖0.05%)、佣金(0.03%)、过户费、最小100股。"
        "A股不做空。已支持: 期货对冲、行业/风格帽、sleeve、BL工具、事件信号、分钟K(interval=5等，近端历史)。"
        "尚不支持: 全市场点位成分仿真、完整 Barra 协方差。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "codes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "股票代码列表，如 ['600519.SH']；可与 universe_asof 二选一",
            },
            "universe_asof": {
                "type": "string",
                "description": "从 research.db 点位池加载 codes（需先 build_tradable_universe 存快照）",
            },
            "universe_name": {"type": "string", "default": "default"},
            "max_universe": {"type": "integer", "default": 50, "description": "点位池截断上限"},
            "start_date": {"type": "string", "description": "开始日期 YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
            "strategy": {
                "type": "string",
                "enum": list(_BUILTIN_STRATEGIES.keys()) + ["custom"],
                "default": "ma_cross",
                "description": "策略名称: ma_cross / rsi / momentum / buy_hold / custom",
            },
            "strategy_params": {
                "type": "object",
                "description": "策略参数。ma_cross: {fast, slow}; rsi: {period, oversold, overbought}; momentum: {window}",
            },
            "signal_file": {
                "type": "string",
                "description": "自定义信号文件路径 (strategy=custom 时必填)。CSV: index=date, columns=代码, values=-1~1",
            },
            "initial_cash": {"type": "number", "default": 1000000},
            "commission": {"type": "number", "default": 0.0003},
            "stamp_duty": {"type": "number", "default": 0.0005},
            "after_hours": {
                "type": "boolean",
                "default": False,
                "description": "盘后固定价格交易开关（粗实现：用收盘价）",
            },
            "signal_lag": {
                "type": "integer",
                "default": 1,
                "description": "信号延迟交易日数。1=T日信号T+1开盘执行（默认）；0=同bar成交（易前视）",
            },
            "exec_price": {
                "type": "string",
                "enum": ["open", "close"],
                "default": "open",
                "description": "成交参考价：open/close",
            },
            "reject_limit_lock": {
                "type": "boolean",
                "default": True,
                "description": "涨停无法买入、跌停无法卖出",
            },
            "use_impact_model": {
                "type": "boolean",
                "default": True,
                "description": "启用 √(成交额/ADV) 冲击成本",
            },
            "impact_coef": {
                "type": "number",
                "default": 0.001,
                "description": "冲击成本系数，slip=max(固定滑点, coef*sqrt(金额/ADV))",
            },
            "skip_halted": {
                "type": "boolean",
                "default": True,
                "description": "缺K线或成交量=0视为停牌，禁止交易并用昨收估价",
            },
            "cash_annual_rate": {
                "type": "number",
                "default": 0.0,
                "description": "闲置资金年化利率（如 0.015≈GC001），按交易日 cash*rate/365 计息",
            },
            "hedge_enabled": {
                "type": "boolean",
                "default": False,
                "description": "启用股指期货空头对冲（需 futures_symbol）",
            },
            "hedge_symbol": {
                "type": "string",
                "default": "IF",
                "description": "IF/IC/IM/IH，决定合约乘数",
            },
            "hedge_ratio": {
                "type": "number",
                "default": 1.0,
                "description": "对冲比例=期货名义/股票市值，1=近似满对冲",
            },
            "futures_symbol": {
                "type": "string",
                "description": "期货合约代码（如 IF2506）；启用对冲时用于拉日线。也可用 index 代理。",
            },
            "max_industry_weight": {
                "type": "number",
                "description": "单行业目标权重上限（如 0.3）；需同时传 industry_map",
            },
            "industry_map": {
                "type": "object",
                "description": "代码→行业名，如 {\"600519.SH\":\"白酒\"}",
            },
            "sleeve_files": {
                "type": "object",
                "description": "多策略信号文件 {\"fund\":\"signals/a.csv\",\"mom\":\"signals/b.csv\"}，等权或配合 sleeve_weights",
            },
            "sleeve_weights": {
                "type": "object",
                "description": "sleeve 权重，如 {\"fund\":0.6,\"mom\":0.4}",
            },
            "max_momentum_exposure": {
                "type": "number",
                "description": "Barra-lite：组合动量暴露 |w·mom_z| 上限（如 0.3）",
            },
            "max_size_exposure": {
                "type": "number",
                "description": "Barra-lite：|w·size_z| 上限，size≈log(ADV)",
            },
            "max_vol_exposure": {
                "type": "number",
                "description": "Barra-lite：|w·vol_z| 上限（实现波动）",
            },
            "momentum_window": {
                "type": "integer",
                "default": 20,
                "description": "动量因子回看交易日数",
            },
            "style_window": {
                "type": "integer",
                "default": 20,
                "description": "size/vol 因子回看窗口",
            },
            "interval": {
                "type": "string",
                "enum": ["1d", "1", "5", "15", "30", "60"],
                "default": "1d",
                "description": "K线周期：1d=日线；1/5/15/30/60=分钟（akshare 近端，历史较短）",
            },
        },
        "required": ["start_date", "end_date"],
    }
    is_readonly = True
    repeatable = True

    def execute(self, args: dict[str, Any], ctx: Any) -> str:
        codes = args.get("codes")
        if not isinstance(codes, list) or not codes:
            asof = str(args.get("universe_asof") or "").strip()
            if not asof:
                return _err("需要 codes 或 universe_asof")
            try:
                from market.research_store import get_store
                pit = get_store().load_universe_pit(
                    asof, name=str(args.get("universe_name") or "default")
                )
            except Exception as e:
                return _err(f"加载点位池失败: {e}")
            if pit is None or not pit.get("codes"):
                return _err(
                    f"无 universe 快照 asof<={asof}；请先 build_tradable_universe(save_snapshot=true)"
                )
            max_u = int(args.get("max_universe") or 50)
            codes = list(pit["codes"])[:max_u]

        start_date = str(args.get("start_date", ""))
        end_date = str(args.get("end_date", ""))
        if not start_date or not end_date:
            return _err("start_date 和 end_date 必填")

        strategy = str(args.get("strategy", "ma_cross"))
        is_custom = strategy == "custom"

        # Fetch data
        interval = str(args.get("interval") or "1d")
        data: dict[str, pd.DataFrame] = {}
        for code in codes:
            try:
                if interval != "1d":
                    from market.loaders import fetch_akshare_minute
                    rows = fetch_akshare_minute(
                        code,
                        period=interval,
                        start_date=start_date,
                        end_date=end_date,
                    )
                    source = "akshare_minute"
                else:
                    rows, source = fetch_one(code, start_date, end_date)
                if not rows:
                    return _err(f"未获取到 {code} 的行情数据 ({interval})")
                df = pd.DataFrame(rows)
                df["trade_date"] = pd.to_datetime(df["trade_date"])
                df = df.set_index("trade_date").sort_index()
                # Cap minute bars to keep engine responsive
                if interval != "1d" and len(df) > 3000:
                    df = df.iloc[-3000:]
                data[code] = df
            except Exception as e:
                return _err(f"获取 {code} 数据失败: {e}")

        if not data:
            return _err("未获取到任何行情数据")

        # Minute: T+1 lock + overnight cash interest are ill-defined per bar
        if interval != "1d":
            args = dict(args)
            if "cash_annual_rate" not in args:
                args["cash_annual_rate"] = 0.0
            # after_hours True relaxes same-bar sell for intraday demos
            if "after_hours" not in args:
                args["after_hours"] = True
            if "signal_lag" not in args:
                args["signal_lag"] = 1
            # disable cash interest on sub-daily to avoid over-accrual
            args["cash_annual_rate"] = 0.0
        # Load custom signal / sleeves
        signal_df = None
        sleeves = None
        sleeve_weights = args.get("sleeve_weights") if isinstance(args.get("sleeve_weights"), dict) else None
        sleeve_files = args.get("sleeve_files") if isinstance(args.get("sleeve_files"), dict) else None
        if sleeve_files:
            from tools._fs import resolve_path
            sleeves = {}
            try:
                for name, path in sleeve_files.items():
                    sleeves[str(name)] = pd.read_csv(
                        resolve_path(ctx, str(path)), index_col=0, parse_dates=True
                    )
            except Exception as e:
                return _err(f"读取 sleeve 信号失败: {e}")
            is_custom = True
            strategy = "custom"
        elif is_custom:
            signal_file = str(args.get("signal_file", ""))
            if not signal_file:
                return _err("strategy=custom 时必须提供 signal_file 或 sleeve_files")
            try:
                from tools._fs import resolve_path
                path = resolve_path(ctx, signal_file)
                signal_df = pd.read_csv(path, index_col=0, parse_dates=True)
            except Exception as e:
                return _err(f"读取信号文件失败: {e}")

        futures_df = None
        hedge_enabled = bool(args.get("hedge_enabled", False))
        if hedge_enabled:
            fut_sym = str(args.get("futures_symbol") or "").strip().upper()
            if not fut_sym:
                return _err("hedge_enabled 时需要 futures_symbol（如 IF2506）")
            try:
                from market.akshare_data import get_futures_daily
                raw = json.loads(get_futures_daily(fut_sym, start_date, end_date))
                if not raw.get("ok"):
                    return _err(raw.get("error") or f"期货 {fut_sym} 数据失败")
                rows = (raw.get("data") or {}).get("records") or []
                futures_df = pd.DataFrame(rows)
                futures_df["trade_date"] = pd.to_datetime(futures_df["trade_date"])
                futures_df = futures_df.set_index("trade_date").sort_index()
            except Exception as e:
                return _err(f"获取期货数据失败: {e}")

        industry_map = args.get("industry_map") if isinstance(args.get("industry_map"), dict) else None
        max_ind = args.get("max_industry_weight")
        max_ind_f = float(max_ind) if max_ind is not None else None
        max_mom = args.get("max_momentum_exposure")
        max_mom_f = float(max_mom) if max_mom is not None else None
        max_sz = args.get("max_size_exposure")
        max_sz_f = float(max_sz) if max_sz is not None else None
        max_vo = args.get("max_vol_exposure")
        max_vo_f = float(max_vo) if max_vo is not None else None

        cfg = BacktestConfig(
            initial_cash=float(args.get("initial_cash", 1_000_000)),
            commission=float(args.get("commission", 0.0003)),
            stamp_duty=float(args.get("stamp_duty", 0.0005)),
            after_hours=bool(args.get("after_hours", False)),
            signal_lag=int(args.get("signal_lag", 1)),
            exec_price=str(args.get("exec_price", "open")),
            reject_limit_lock=bool(args.get("reject_limit_lock", True)),
            use_impact_model=bool(args.get("use_impact_model", True)),
            impact_coef=float(args.get("impact_coef", 0.001)),
            skip_halted=bool(args.get("skip_halted", True)),
            cash_annual_rate=float(args.get("cash_annual_rate", 0.0)),
            hedge_enabled=hedge_enabled,
            hedge_symbol=str(args.get("hedge_symbol", "IF")),
            hedge_ratio=float(args.get("hedge_ratio", 1.0)),
            max_industry_weight=max_ind_f,
            max_momentum_exposure=max_mom_f,
            max_size_exposure=max_sz_f,
            max_vol_exposure=max_vo_f,
            momentum_window=int(args.get("momentum_window", 20)),
            style_window=int(args.get("style_window", 20)),
        )

        engine = BacktestEngine(cfg)
        try:
            result = engine.run(
                data=data,
                signal=signal_df if (is_custom and not sleeves) else None,
                strategy=strategy if not is_custom else "",
                strategy_params=args.get("strategy_params") if not is_custom else None,
                futures_data=futures_df,
                sleeves=sleeves,
                sleeve_weights=sleeve_weights,
                industry_map=industry_map,
            )
        except Exception as e:
            return _err(f"回测执行失败: {e}")

        if not result.get("ok"):
            return _err(result.get("error", "回测失败"))

        return json.dumps(result, ensure_ascii=False, default=str)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)
