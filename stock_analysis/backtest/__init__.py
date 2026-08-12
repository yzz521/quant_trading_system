"""V2 回测模块 —— 验证 Trading Plan 规则的长期有效性。

防 look-ahead 是硬性要求（计划书 §17）：计划只用截至 T 日的数据生成，
评估只用 T 之后的数据。
"""
from .metrics import BacktestMetrics, calc_metrics
from .trading_plan_backtest import BacktestResult, BacktestTrade, TradingPlanBacktest

__all__ = [
    "BacktestMetrics",
    "calc_metrics",
    "BacktestResult",
    "BacktestTrade",
    "TradingPlanBacktest",
]
