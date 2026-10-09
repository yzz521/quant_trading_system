"""V2 回测模块 —— 验证 Trading Plan 规则的长期有效性。

防 look-ahead 是硬性要求（计划书 §17）：计划只用截至 T 日的数据生成，
评估只用 T 之后的数据。

成交真实性（2026-09 起）：见 ``execution.py`` —— 限价成交可达性、交易成本、
跳空穿透、涨跌停/停牌、容量约束；组合净值见 ``metrics.simulate_portfolio``。
"""
from .execution import (
    LIMIT_PCT_BJ,
    LIMIT_PCT_ST,
    LIMIT_PCT_STAR,
    MARKET_COST_PRESETS,
    MARKET_HAS_PRICE_LIMITS,
    CostModel,
    ExecutionConfig,
    apply_market,
    cost_model_for_market,
    is_suspended,
    limit_pct_for_code,
    market_has_price_limits,
    price_limit_state,
)
from .metrics import BacktestMetrics, calc_metrics, simulate_portfolio
from .trading_plan_backtest import BacktestResult, BacktestTrade, TradingPlanBacktest

__all__ = [
    "BacktestMetrics",
    "calc_metrics",
    "simulate_portfolio",
    "BacktestResult",
    "BacktestTrade",
    "TradingPlanBacktest",
    "CostModel",
    "ExecutionConfig",
    "MARKET_COST_PRESETS",
    "MARKET_HAS_PRICE_LIMITS",
    "apply_market",
    "cost_model_for_market",
    "is_suspended",
    "limit_pct_for_code",
    "market_has_price_limits",
    "price_limit_state",
    "LIMIT_PCT_STAR",
    "LIMIT_PCT_BJ",
    "LIMIT_PCT_ST",
]
