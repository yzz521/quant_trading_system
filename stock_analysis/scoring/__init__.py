"""V2 评分系统 —— Stock Score（个股质量） + Opportunity Score（交易机会）。

计划书 §05：两个评分维度分离：
  * Stock Score：股票本身好不好（基本面/技术/资金/估值/环境/风险）
  * Opportunity Score：当前价位是否值得交易（价位/支撑/趋势/距离/风险收益/波动/相似形态）
"""
from .opportunity_score import OpportunityScore, calc_opportunity_score
from .score_components import (
    normalize_component,
    score_price_position,
    score_rr,
    score_support_strength,
    score_trend,
    score_volatility,
    score_volume,
)
from .stock_score import StockScore, calc_stock_score

__all__ = [
    "StockScore",
    "calc_stock_score",
    "OpportunityScore",
    "calc_opportunity_score",
    "score_trend",
    "score_volume",
    "score_volatility",
    "score_price_position",
    "score_support_strength",
    "score_rr",
    "normalize_component",
]
