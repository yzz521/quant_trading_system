"""V2 市场环境模块 —— Market Regime / Breadth / Risk。

市场状态影响机会评分与仓位（计划书 §15）。本模块为纯计算 + 数据接口解耦：
  * market_regime: 通过指数数据判定 BULL/NEUTRAL/BEAR/HIGH_RISK
  * market_breadth: 市场宽度（涨跌家数、新高新低）
  * market_risk: 市场风险（波动率、回撤、量能异常）
"""
from .market_breadth import MarketBreadth, calc_market_breadth
from .market_regime import REGIME_FACTOR, MarketRegime, MarketRegimeState, detect_market_regime
from .market_risk import MarketRisk, calc_market_risk

__all__ = [
    "MarketRegime",
    "detect_market_regime",
    "MarketRegimeState",
    "REGIME_FACTOR",
    "MarketBreadth",
    "calc_market_breadth",
    "MarketRisk",
    "calc_market_risk",
]
