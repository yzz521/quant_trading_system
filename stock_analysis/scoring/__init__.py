"""V2 评分系统 —— Stock Score（个股质量） + Opportunity Score（交易机会）。

计划书 §05：两个评分维度分离：
  * Stock Score：股票本身好不好（基本面/技术/资金/估值/环境/风险）
  * Opportunity Score：当前价位是否值得交易（价位/支撑/趋势/距离/风险收益/波动/相似形态）

``composite``：把质量 / 时机 / 赔率合成一个可排序的组合分，替代「只按机会分排序」。

``factor_weights``：读 ``results/factor_validation_report.json``，按因子验证结论把
组合分权重**自动降权** —— 让「回验」真正回到公式里，而不是只打印结论。
"""
from .composite import COMPOSITE_WEIGHTS, composite_score, rr_component, score_plan
from .factor_weights import (
    DEFAULT_MAX_AGE_DAYS,
    DIMENSION_FACTOR,
    VERDICT_LABEL_ZH,
    VERDICT_MULTIPLIER,
    active_weights,
    classify_verdict,
    clear_cache,
    default_report_path,
    downweight_from_report,
    load_validated_weights,
    multiplier_for,
)
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
    "COMPOSITE_WEIGHTS",
    "composite_score",
    "rr_component",
    "score_plan",
    "DIMENSION_FACTOR",
    "VERDICT_MULTIPLIER",
    "VERDICT_LABEL_ZH",
    "DEFAULT_MAX_AGE_DAYS",
    "active_weights",
    "classify_verdict",
    "clear_cache",
    "default_report_path",
    "downweight_from_report",
    "load_validated_weights",
    "multiplier_for",
]
