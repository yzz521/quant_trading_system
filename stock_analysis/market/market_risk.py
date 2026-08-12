"""市场风险（Market Risk）：波动率、回撤、量能异常。

高分 = 低风险（安全），低分 = 高风险。输出 0-100 风险分。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class MarketRisk:
    """市场风险结果。"""

    score: float = 80.0       # 0-100，越高越安全
    vol_pct: Optional[float] = None
    drawdown_pct: Optional[float] = None
    level: str = "LOW"        # LOW / MEDIUM / HIGH
    evidence: dict = None

    def __post_init__(self):
        if self.evidence is None:
            self.evidence = {}

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 1),
            "vol_pct": self.vol_pct,
            "drawdown_pct": self.drawdown_pct,
            "level": self.level,
            "evidence": self.evidence,
        }


def calc_market_risk(
    index_df: Optional[pd.DataFrame] = None,
    *,
    atr_col: str = "atr",
    lookback: int = 60,
) -> MarketRisk:
    """从指数日K评估市场风险。未传数据返回默认安全分。"""
    if index_df is None or len(index_df) < 20:
        return MarketRisk()

    d = index_df.tail(lookback).reset_index(drop=True)
    close = pd.to_numeric(d["close"], errors="coerce")
    cur = float(close.iloc[-1]) if close.iloc[-1] is not None and not np.isnan(close.iloc[-1]) else None
    if cur is None or cur <= 0:
        return MarketRisk()

    # 波动率：ATR%
    atr = None
    if atr_col in d.columns:
        v = pd.to_numeric(d[atr_col], errors="coerce").iloc[-1]
        atr = float(v) if v is not None and not np.isnan(v) else None
    vol_pct = atr / cur * 100 if atr else None

    # 回撤：距 60 日高点
    hi = float(close.max())
    drawdown_pct = (hi - cur) / hi * 100 if hi > 0 else 0.0

    score = 80.0
    if vol_pct is not None:
        if vol_pct > 5:
            score -= 35
        elif vol_pct > 3:
            score -= 15
    if drawdown_pct > 15:
        score -= 25
    elif drawdown_pct > 8:
        score -= 12
    elif drawdown_pct > 4:
        score -= 5
    score = float(np.clip(score, 0, 100))

    level = "LOW" if score >= 70 else ("MEDIUM" if score >= 45 else "HIGH")

    return MarketRisk(
        score=score,
        vol_pct=round(vol_pct, 2) if vol_pct is not None else None,
        drawdown_pct=round(drawdown_pct, 2),
        level=level,
        evidence={"high_60d": round(hi, 2), "close": round(cur, 2)},
    )
