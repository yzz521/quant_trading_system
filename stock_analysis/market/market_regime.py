"""市场状态（Market Regime）判定。

基于指数日K（如沪深300/上证指数）：
  * 均线趋势（MA20 vs MA60）
  * 价格相对均线位置
  * 近期动量（20日收益率）
  * 波动率（ATR%）

输出 BULL / NEUTRAL / BEAR / HIGH_RISK，并给出市场分（0-100）供评分系统使用。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd


class MarketRegimeState(str, Enum):
    BULL = "BULL"
    NEUTRAL = "NEUTRAL"
    BEAR = "BEAR"
    HIGH_RISK = "HIGH_RISK"


# 市场状态 → 仓位调节系数（计划书 §15：市场环境影响仓位）
REGIME_FACTOR = {
    MarketRegimeState.BULL: 1.0,
    MarketRegimeState.NEUTRAL: 0.75,
    MarketRegimeState.BEAR: 0.5,
    MarketRegimeState.HIGH_RISK: 0.25,
}


@dataclass
class MarketRegime:
    """市场状态结果。"""

    state: MarketRegimeState = MarketRegimeState.NEUTRAL
    score: float = 50.0          # 0-100，供 Stock Score 的市场环境维度
    factor: float = 0.75         # 仓位调节系数
    evidence: dict = None

    def __post_init__(self):
        if self.evidence is None:
            self.evidence = {}

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "score": round(self.score, 1),
            "factor": self.factor,
            "evidence": self.evidence,
        }


def _num(x) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
        return None if np.isnan(v) else v
    except (TypeError, ValueError):
        return None


def detect_market_regime(
    index_df: Optional[pd.DataFrame] = None,
    *,
    ma20_col: str = "ma20",
    ma60_col: str = "ma60",
    atr_col: str = "atr",
) -> MarketRegime:
    """从指数日K判定市场状态。未传数据时返回中性（不影响主流程）。"""
    if index_df is None or len(index_df) < 30:
        return MarketRegime()

    d = index_df.tail(60).reset_index(drop=True)
    close = pd.to_numeric(d["close"], errors="coerce")
    cur = _num(close.iloc[-1])
    if cur is None:
        return MarketRegime()

    ma20 = _num(d[ma20_col].iloc[-1]) if ma20_col in d.columns else None
    ma60 = _num(d[ma60_col].iloc[-1]) if ma60_col in d.columns else None
    atr = _num(d[atr_col].iloc[-1]) if atr_col in d.columns else None
    ret20 = _num((close.iloc[-1] / close.iloc[-21] - 1) * 100) if len(close) > 21 else None

    # 趋势得分
    score = 50.0
    evidence = {"close": round(cur, 2), "ret20_pct": round(ret20, 2) if ret20 else None}
    if ma20 is not None:
        score += (15 if cur > ma20 else -15)
        evidence["ma20"] = round(ma20, 2)
    if ma60 is not None:
        score += (15 if cur > ma60 else -15)
        evidence["ma60"] = round(ma60, 2)
    if ret20 is not None:
        score += float(np.clip(ret20 * 2, -15, 15))
    if atr is not None and cur > 0:
        atr_pct = atr / cur * 100
        evidence["atr_pct"] = round(atr_pct, 2)
        if atr_pct > 5:  # 高波动 = 风险上升
            score -= 20
        elif atr_pct > 3:
            score -= 8

    score = float(np.clip(score, 0, 100))

    if score >= 70:
        state = MarketRegimeState.BULL
    elif score >= 45:
        state = MarketRegimeState.NEUTRAL
    elif score >= 30:
        state = MarketRegimeState.BEAR
    else:
        state = MarketRegimeState.HIGH_RISK

    return MarketRegime(state=state, score=score, factor=REGIME_FACTOR[state], evidence=evidence)
