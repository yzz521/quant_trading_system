"""V2 Opportunity Engine —— 交易机会引擎。

把个股的机会拆解为：支撑/阻力、入场区间、止损、目标价、风险收益比、
仓位建议，最终由 TradingPlan 统一表达。
"""
from .entry_price import EntryPrice, calc_entry_zone
from .exit_price import ExitPrice, calc_exit_prices
from .opportunity_engine import OpportunityEngine
from .position_sizing import PositionSizing, calc_position_size
from .risk_reward import RiskReward, calc_risk_reward
from .support_resistance import SupportResistance, detect_support_resistance
from .trading_plan import DecisionState, TradingPlan, build_trading_plan

__all__ = [
    "SupportResistance",
    "detect_support_resistance",
    "EntryPrice",
    "calc_entry_zone",
    "ExitPrice",
    "calc_exit_prices",
    "RiskReward",
    "calc_risk_reward",
    "PositionSizing",
    "calc_position_size",
    "TradingPlan",
    "DecisionState",
    "build_trading_plan",
    "OpportunityEngine",
]
