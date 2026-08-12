"""Opportunity Score —— 交易机会评分（0-100）。

回答「当前价位是否值得交易」。
权重（计划书 §05）：
  现价位置 20% | 支撑强度 15% | 趋势状态 15% | 距入场距离 15% | 风险收益比 20% | 波动率 5% | 历史相似形态 10%
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .score_components import (
    score_price_position,
    score_rr,
    score_support_strength,
    score_trend,
    score_volatility,
)

WEIGHTS = {
    "price_position": 0.20,
    "support_strength": 0.15,
    "trend": 0.15,
    "distance_to_entry": 0.15,
    "risk_reward": 0.20,
    "volatility": 0.05,
    "similar_pattern": 0.10,
}


@dataclass
class OpportunityScore:
    """交易机会评分结果。"""

    total: float = 0.0
    components: dict = field(default_factory=dict)
    breakdown: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"total": round(self.total, 1), "components": self.components, "breakdown": self.breakdown}


def _score_distance_to_entry(current_price: float, entry_low: float, entry_high: float) -> float:
    """距入场区间：现价越接近/处于区间，分越高。"""
    if entry_low is None or entry_high is None or entry_low <= 0:
        return 40.0
    if entry_low <= current_price <= entry_high:
        return 95.0
    if current_price < entry_low:
        # 已在区间下方（更便宜）：仍给高分，但略降（可能继续跌）
        below = (entry_low - current_price) / entry_low
        return max(60.0, 95.0 - below * 200)
    # 现价高于区间上沿：越远越低
    above = (current_price - entry_high) / entry_high
    return max(0.0, 95.0 - above * 300)


def _score_similar_pattern(pattern_score: Optional[float]) -> float:
    """历史相似形态（0-100）；未提供时给中性。"""
    return float(np.clip(pattern_score if pattern_score is not None else 50, 0, 100))


def calc_opportunity_score(
    df: Optional[pd.DataFrame] = None,
    *,
    current_price: float,
    entry_low: Optional[float] = None,
    entry_high: Optional[float] = None,
    key_support: Optional[float] = None,
    risk_reward_1: Optional[float] = None,
    similar_pattern_score: Optional[float] = None,
    weights: Optional[dict] = None,
) -> OpportunityScore:
    """计算交易机会评分。

    Args:
        df: 已加指标的日K（趋势/价位/波动维度使用）。
        current_price: 现价。
        entry_low/entry_high: 入场区间下/上沿。
        key_support: 关键支撑位。
        risk_reward_1: 风险收益比（T1）。
        similar_pattern_score: 历史相似形态评分（0-100）。
    """
    w = {**WEIGHTS, **(weights or {})}

    comps = {
        "price_position": score_price_position(df) if df is not None else 50.0,
        "support_strength": score_support_strength(df, key_support) if df is not None else 50.0,
        "trend": score_trend(df) if df is not None else 50.0,
        "distance_to_entry": _score_distance_to_entry(current_price, entry_low, entry_high),
        "risk_reward": score_rr(risk_reward_1),
        "volatility": score_volatility(df) if df is not None else 50.0,
        "similar_pattern": _score_similar_pattern(similar_pattern_score),
    }
    total = sum(comps[k] * w[k] for k in w)
    breakdown = {k: {"weight": round(w[k], 3), "score": round(comps[k], 1)} for k in w}
    return OpportunityScore(
        total=total, components={k: round(v, 1) for k, v in comps.items()}, breakdown=breakdown
    )
