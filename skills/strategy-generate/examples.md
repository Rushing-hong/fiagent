# Strategy Generate — Examples（A 股）

工作流：`get_market_data` →（可选自定义信号 CSV）→ `run_backtest`。

## Example 1: 双均线（内置策略）

User: "用 000001.SZ 做双均线金叉，短期 5 日长期 20 日，回测 2024 年"

```
1. load_skill("strategy-generate")
2. run_backtest(
     codes=["000001.SZ"],
     start_date="2024-01-01",
     end_date="2024-12-31",
     strategy="ma_cross",
     strategy_params={"fast": 5, "slow": 20}
   )
3. 读返回的 metrics / trades，按需迭代参数
```

## Example 2: RSI（内置策略）

User: "茅台 RSI 策略，RSI<30 买、>70 卖，回测 2024"

```
1. run_backtest(
     codes=["600519.SH"],
     start_date="2024-01-01",
     end_date="2024-12-31",
     strategy="rsi",
     strategy_params={"period": 14, "oversold": 30, "overbought": 70}
   )
2. 分析回撤与交易次数；必要时改参数重跑
```

## Example 3: 自定义信号（布林带）

User: "用布林带突破做 000001.SZ 回测"

```
1. get_market_data(codes=["000001.SZ"], start_date="2024-01-01", end_date="2024-12-31")
2. write 脚本 → run_python：算信号，写出 signal.csv（index=date, columns=代码, values=-1~1）
3. run_backtest(
     codes=["000001.SZ"],
     start_date="2024-01-01",
     end_date="2024-12-31",
     strategy="custom",
     signal_file="signal.csv"
   )
```

分钟级见 `minute-analysis`（`interval="5"` 等）。
