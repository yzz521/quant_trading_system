# 阶段十一：基准对比 + Paper 闭环

## 基准
```python
from quant_trading_system.analytics import compute_benchmark_metrics
m = compute_benchmark_metrics(portfolio, hs300_close_series)
# excess_total_return, information_ratio, beta, ...
```

## Paper 闭环
```bash
python examples/paper_loop_demo.py
python examples/paper_loop_demo.py --readonly
# 成交写入 results/paper_trades.jsonl
```

LiveConfig 新增: readonly, trade_log_path, heartbeat_sec, t1_enabled
