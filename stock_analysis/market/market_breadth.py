"""市场宽度（Market Breadth）：涨跌家数、强弱分布。

数据来自外部快照（如全市场涨跌统计），纯计算模块。输出 0-100 宽度分。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class MarketBreadth:
    """市场宽度结果。"""

    advance: int = 0          # 上涨家数
    decline: int = 0          # 下跌家数
    flat: int = 0             # 平盘家数
    ratio: Optional[float] = None   # 上涨占比
    score: float = 50.0       # 0-100 宽度分
    evidence: dict = None

    def __post_init__(self):
        if self.evidence is None:
            self.evidence = {}

    def to_dict(self) -> dict:
        return {
            "advance": self.advance,
            "decline": self.decline,
            "flat": self.flat,
            "ratio": round(self.ratio, 3) if self.ratio is not None else None,
            "score": round(self.score, 1),
            "evidence": self.evidence,
        }


def calc_market_breadth(
    spot: Optional[pd.DataFrame] = None,
    *,
    pct_col: str = "pct_chg",
) -> MarketBreadth:
    """从全市场快照计算宽度。未传数据返回中性。"""
    if spot is None or spot.empty or pct_col not in spot.columns:
        return MarketBreadth()

    pct = pd.to_numeric(spot[pct_col], errors="coerce").dropna()
    if pct.empty:
        return MarketBreadth()
    advance = int((pct > 0).sum())
    decline = int((pct < 0).sum())
    flat = int((pct == 0).sum())
    ratio = advance / len(pct) if len(pct) else 0.0

    # 宽度分：上涨占比 0.5 为中性，0.7 偏强，0.3 偏弱
    score = float(np.clip((ratio - 0.5) / 0.2 * 50 + 50, 0, 100))
    # 极端分化（涨跌停板多）用平均涨跌幅补充
    avg_pct = float(pct.mean())
    score += float(np.clip(avg_pct * 8, -10, 10))
    score = float(np.clip(score, 0, 100))

    return MarketBreadth(
        advance=advance,
        decline=decline,
        flat=flat,
        ratio=ratio,
        score=score,
        evidence={"avg_pct": round(avg_pct, 2), "total": int(len(pct))},
    )
